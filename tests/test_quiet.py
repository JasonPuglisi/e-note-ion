import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import quiet as _mod


@pytest.fixture(autouse=True)
def _reset_state() -> None:
  """Reset quiet module state before each test."""
  _mod._active = False
  _mod._virtual_state = None
  _mod._changed.clear()


# --- init ---


def test_init_defaults_to_inactive(tmp_path: Path) -> None:
  with patch('quiet._config_mod.get_optional_bool', return_value=False) as mock:
    _mod.init()
  mock.assert_called_once_with('scheduler', 'quiet', default=False)
  assert not _mod.is_quiet()


def test_init_restores_active_state(tmp_path: Path) -> None:
  with patch('quiet._config_mod.get_optional_bool', return_value=True) as mock:
    _mod.init()
  mock.assert_called_once_with('scheduler', 'quiet', default=False)
  assert _mod.is_quiet()


def test_init_quiet_false_stays_inactive() -> None:
  """Regression: [scheduler] quiet = false must not activate quiet mode."""
  with patch.dict('config._config', {'scheduler': {'quiet': False}}):
    _mod.init()
  assert not _mod.is_quiet()


# --- set_quiet ---


def test_set_quiet_true_activates() -> None:
  with patch('quiet._config_mod.write_config_section'):
    _mod.set_quiet(True)
  assert _mod.is_quiet()


def test_set_quiet_true_persists_to_config() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.set_quiet(True)
  mock_write.assert_called_once_with('scheduler', {'quiet': True})


def test_set_quiet_true_sets_changed_event() -> None:
  with patch('quiet._config_mod.write_config_section'):
    _mod.set_quiet(True)
  assert _mod.changed_event().is_set()


def test_set_quiet_true_idempotent() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.set_quiet(True)
    _mod.set_quiet(True)  # second call should be a no-op
  mock_write.assert_called_once()


def test_set_quiet_false_deactivates() -> None:
  _mod._active = True
  with patch('quiet._config_mod.write_config_section'):
    _mod.set_quiet(False)
  assert not _mod.is_quiet()


def test_set_quiet_false_persists_to_config() -> None:
  _mod._active = True
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.set_quiet(False)
  mock_write.assert_called_once_with('scheduler', {'quiet': False})


def test_set_quiet_false_sets_changed_event() -> None:
  _mod._active = True
  with patch('quiet._config_mod.write_config_section'):
    _mod.set_quiet(False)
  assert _mod.changed_event().is_set()


def test_set_quiet_false_when_already_inactive_is_noop() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.set_quiet(False)
  mock_write.assert_not_called()


def test_set_quiet_false_preserves_virtual_state() -> None:
  """Virtual state is preserved for the worker to retrieve via pop_virtual_state."""
  _mod._active = True
  _mod._virtual_state = [[1, 2, 3]]
  with patch('quiet._config_mod.write_config_section'):
    _mod.set_quiet(False)
  assert _mod.get_virtual_state() == [[1, 2, 3]]


# --- virtual state ---


def test_set_and_get_virtual_state() -> None:
  grid = [[1, 2], [3, 4]]
  _mod.set_virtual_state(grid)
  assert _mod.get_virtual_state() == grid


def test_get_virtual_state_default_none() -> None:
  assert _mod.get_virtual_state() is None


def test_pop_virtual_state_returns_and_clears() -> None:
  grid = [[5, 6], [7, 8]]
  _mod.set_virtual_state(grid)
  result = _mod.pop_virtual_state()
  assert result == grid
  assert _mod.get_virtual_state() is None


def test_pop_virtual_state_none_when_empty() -> None:
  assert _mod.pop_virtual_state() is None


# --- delayed wake ---


def _quiet_with_wake(delay: float = 600) -> None:
  """Put the module in quiet mode with a pending wake (config writes mocked by caller)."""
  _mod._active = True
  _mod.schedule_wake(delay)


def test_schedule_wake_arms_timer_and_persists() -> None:
  with patch('quiet._config_mod.write_config_section'):
    before = time.time()
    _quiet_with_wake(300)
  at = _mod.pending_wake_at()
  assert at is not None and before + 300 <= at <= time.time() + 300
  assert _mod.is_quiet()
  assert json.loads(_mod._WAKE_PATH.read_text())['wake_at'] == pytest.approx(at, abs=1)


def test_schedule_wake_fires_and_wakes() -> None:
  _mod._active = True
  with (
    patch('quiet._config_mod.write_config_section') as mock_write,
    patch('quiet._homebridge_mod.notify_mode_change') as mock_notify,
  ):
    _mod.schedule_wake(0.05)
    assert _mod.changed_event().wait(timeout=2)
    # The event is set inside the lock, before notify runs; let it finish.
    deadline = time.monotonic() + 2
    while not mock_notify.called and time.monotonic() < deadline:
      time.sleep(0.01)
  assert not _mod.is_quiet()
  mock_write.assert_called_once_with('scheduler', {'quiet': False})
  mock_notify.assert_called_once_with('quiet', False)
  assert _mod.pending_wake_at() is None
  assert not _mod._WAKE_PATH.exists()


def test_schedule_wake_noop_when_awake() -> None:
  _mod.schedule_wake(300)
  assert _mod.pending_wake_at() is None
  assert not _mod._WAKE_PATH.exists()


@pytest.mark.parametrize('delay', [0, -1, _mod.MAX_WAKE_DELAY + 1])
def test_schedule_wake_rejects_out_of_range(delay: float) -> None:
  _mod._active = True
  with pytest.raises(ValueError):
    _mod.schedule_wake(delay)
  assert _mod.pending_wake_at() is None


def test_quiet_while_quiet_cancels_pending_wake() -> None:
  """Regression guard: the already-quiet early return must still cancel."""
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _quiet_with_wake()
    _mod.set_quiet(True)
  assert _mod.pending_wake_at() is None
  assert not _mod._WAKE_PATH.exists()
  assert _mod.is_quiet()
  mock_write.assert_not_called()


def test_immediate_wake_cancels_pending_wake() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _quiet_with_wake()
    _mod.set_quiet(False)
  assert not _mod.is_quiet()
  assert _mod.pending_wake_at() is None
  assert not _mod._WAKE_PATH.exists()
  mock_write.assert_called_once_with('scheduler', {'quiet': False})


def test_second_schedule_wake_replaces_first() -> None:
  with patch('quiet._config_mod.write_config_section'):
    _quiet_with_wake(0.05)
    first_generation = _mod._wake_generation
    _mod.schedule_wake(600)
    time.sleep(0.2)  # the first timer's deadline passes
  assert _mod.is_quiet()
  assert _mod._wake_generation == first_generation + 1
  at = _mod.pending_wake_at()
  assert at is not None and at > time.time() + 500


def test_stale_timer_callback_is_ignored() -> None:
  """A callback blocked on the lock while it was cancelled must not wake."""
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _quiet_with_wake()
    stale = _mod._wake_generation
    _mod.schedule_wake(600)  # replace → stale generation
    _mod._fire_pending_wake(stale)
    assert _mod.is_quiet()
    assert _mod.pending_wake_at() is not None

    _mod.set_quiet(True)  # cancel → nothing pending
    _mod._fire_pending_wake(_mod._wake_generation)
  assert _mod.is_quiet()
  mock_write.assert_not_called()


def test_fire_pending_wake_logs_exception(caplog: pytest.LogCaptureFixture) -> None:
  with patch('quiet._config_mod.write_config_section'):
    _quiet_with_wake()
  generation = _mod._wake_generation
  with patch('quiet._config_mod.write_config_section', side_effect=OSError('disk full')):
    _mod._fire_pending_wake(generation)  # must not raise
  assert 'Delayed wake failed' in caplog.text
  # Quiet=false was never persisted, so the file is kept for the next startup.
  assert _mod._WAKE_PATH.exists()


def test_write_wake_file_failure_still_arms_timer() -> None:
  _mod._active = True
  with patch('quiet.os.replace', side_effect=OSError('read-only')):
    _mod.schedule_wake(300)
  assert _mod.pending_wake_at() is not None


# --- delayed wake: restore on init ---


def _persist_wake(at: Any) -> None:
  _mod._WAKE_PATH.write_text(json.dumps({'wake_at': at}))


def test_init_rearms_future_wake() -> None:
  _persist_wake(time.time() + 120)
  with patch('quiet._config_mod.get_optional_bool', return_value=True):
    _mod.init()
  at = _mod.pending_wake_at()
  assert _mod.is_quiet()
  assert at is not None and 100 < at - time.time() <= 120


def test_init_wakes_when_deadline_passed() -> None:
  _persist_wake(time.time() - 5)
  with (
    patch('quiet._config_mod.get_optional_bool', return_value=True),
    patch('quiet._config_mod.write_config_section') as mock_write,
  ):
    _mod.init()
  assert not _mod.is_quiet()
  mock_write.assert_called_once_with('scheduler', {'quiet': False})
  assert not _mod._WAKE_PATH.exists()


def test_init_discards_wake_when_not_quiet() -> None:
  _persist_wake(time.time() + 120)
  with patch('quiet._config_mod.get_optional_bool', return_value=False):
    _mod.init()
  assert _mod.pending_wake_at() is None
  assert not _mod._WAKE_PATH.exists()


def test_init_clamps_far_future_wake() -> None:
  """A wall clock that moved backwards while stopped cannot extend the delay."""
  _persist_wake(time.time() + 10 * _mod.MAX_WAKE_DELAY)
  with patch('quiet._config_mod.get_optional_bool', return_value=True):
    _mod.init()
  at = _mod.pending_wake_at()
  assert at is not None and at <= time.time() + _mod.MAX_WAKE_DELAY


@pytest.mark.parametrize('content', ['not json', '{}', '{"wake_at": "soon"}', '{"wake_at": true}', '{"wake_at": NaN}'])
def test_init_ignores_malformed_wake_file(content: str) -> None:
  _mod._WAKE_PATH.write_text(content)
  with patch('quiet._config_mod.get_optional_bool', return_value=True):
    _mod.init()
  assert _mod.is_quiet()
  assert _mod.pending_wake_at() is None
  assert not _mod._WAKE_PATH.exists()


# --- thread safety ---


def test_concurrent_set_quiet() -> None:
  """Rapid concurrent set_quiet should not corrupt state."""
  errors: list[Exception] = []

  def _toggle(n: int) -> None:
    try:
      for _ in range(n):
        _mod.set_quiet(True)
        _mod.set_quiet(False)
    except Exception as e:  # noqa: BLE001
      errors.append(e)

  with patch('quiet._config_mod.write_config_section'):
    threads = [threading.Thread(target=_toggle, args=(50,)) for _ in range(4)]
    for t in threads:
      t.start()
    for t in threads:
      t.join()
  assert not errors
  # Final state should be inactive (all threads did activate+deactivate pairs)
  assert not _mod.is_quiet()
