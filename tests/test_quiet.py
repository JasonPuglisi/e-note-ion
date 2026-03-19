import threading
from pathlib import Path
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
  with patch('quiet._config_mod.get_optional_bool', return_value=False):
    _mod.init()
  assert not _mod.is_active()


def test_init_restores_active_state(tmp_path: Path) -> None:
  with patch('quiet._config_mod.get_optional_bool', return_value=True):
    _mod.init()
  assert _mod.is_active()


# --- activate ---


def test_activate_sets_active() -> None:
  with patch('quiet._config_mod.write_config_section'):
    _mod.activate()
  assert _mod.is_active()


def test_activate_persists_to_config() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.activate()
  mock_write.assert_called_once_with('scheduler.quiet', {'active': True})


def test_activate_sets_changed_event() -> None:
  with patch('quiet._config_mod.write_config_section'):
    _mod.activate()
  assert _mod.changed_event().is_set()


def test_activate_idempotent() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.activate()
    _mod.activate()  # second call should be a no-op
  mock_write.assert_called_once()


# --- deactivate ---


def test_deactivate_clears_active() -> None:
  _mod._active = True
  with patch('quiet._config_mod.write_config_section'):
    _mod.deactivate()
  assert not _mod.is_active()


def test_deactivate_persists_to_config() -> None:
  _mod._active = True
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.deactivate()
  mock_write.assert_called_once_with('scheduler.quiet', {'active': False})


def test_deactivate_sets_changed_event() -> None:
  _mod._active = True
  with patch('quiet._config_mod.write_config_section'):
    _mod.deactivate()
  assert _mod.changed_event().is_set()


def test_deactivate_when_already_inactive_is_noop() -> None:
  with patch('quiet._config_mod.write_config_section') as mock_write:
    _mod.deactivate()
  mock_write.assert_not_called()


def test_deactivate_preserves_virtual_state() -> None:
  """Virtual state is preserved for the worker to retrieve via pop_virtual_state."""
  _mod._active = True
  _mod._virtual_state = [[1, 2, 3]]
  with patch('quiet._config_mod.write_config_section'):
    _mod.deactivate()
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


# --- thread safety ---


def test_concurrent_activate_deactivate() -> None:
  """Rapid concurrent activate/deactivate should not corrupt state."""
  errors: list[Exception] = []

  def _toggle(n: int) -> None:
    try:
      for _ in range(n):
        _mod.activate()
        _mod.deactivate()
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
  assert not _mod.is_active()
