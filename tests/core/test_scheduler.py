import json
import time
from pathlib import Path
from queue import Empty
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import integrations.vestaboard as vb
import scheduler as _mod
from exceptions import IntegrationDataUnavailableError


@pytest.fixture()
def sched() -> Generator[BackgroundScheduler, None, None]:
  s = BackgroundScheduler()
  yield s
  if s.running:
    s.shutdown(wait=False)


@pytest.fixture(autouse=True)
def drain_queue() -> Generator[None, None, None]:
  """Drain the shared queue before each test to prevent cross-test pollution."""
  while True:
    try:
      _mod._queue.get_nowait()
    except Empty:
      break
  yield


# --- parse_cron ---


def test_parse_cron_valid() -> None:
  result = _mod.parse_cron('0 8 * * 1-5')
  assert result == {
    'minute': '0',
    'hour': '8',
    'day': '*',
    'month': '*',
    'day_of_week': '1-5',
  }


def test_parse_cron_too_few_fields() -> None:
  with pytest.raises(ValueError):
    _mod.parse_cron('0 8 * *')


def test_parse_cron_too_many_fields() -> None:
  with pytest.raises(ValueError):
    _mod.parse_cron('0 8 * * * extra')


# --- QueuedMessage ordering ---


def test_higher_priority_sorts_first() -> None:
  low = _mod.QueuedMessage(priority=3, seq=0, name='low', scheduled_at=0.0, data={}, hold=60, timeout=60)
  high = _mod.QueuedMessage(priority=8, seq=1, name='high', scheduled_at=0.0, data={}, hold=60, timeout=60)
  assert high < low  # min-heap: high priority should be popped first


def test_equal_priority_earlier_seq_first() -> None:
  first = _mod.QueuedMessage(priority=5, seq=0, name='first', scheduled_at=0.0, data={}, hold=60, timeout=60)
  second = _mod.QueuedMessage(priority=5, seq=1, name='second', scheduled_at=0.0, data={}, hold=60, timeout=60)
  assert first < second


# --- pop_valid_message ---


def test_pop_valid_message_returns_message() -> None:
  msg = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={},
    hold=60,
    timeout=60,
  )
  _mod._queue.put(msg)
  with patch('time.sleep'):
    result = _mod.pop_valid_message()
  assert result is msg


def test_pop_valid_message_discards_expired_returns_next() -> None:
  expired = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='expired',
    scheduled_at=time.monotonic() - 100,  # waited 100s
    data={},
    hold=60,
    timeout=10,  # timeout only 10s
  )
  valid = _mod.QueuedMessage(
    priority=5,
    seq=1,
    name='valid',
    scheduled_at=time.monotonic(),
    data={},
    hold=60,
    timeout=60,
  )
  _mod._queue.put(expired)
  _mod._queue.put(valid)
  with patch('time.sleep'):
    result = _mod.pop_valid_message()
  assert result is not None
  assert result.name == 'valid'


def test_pop_valid_message_returns_none_when_empty() -> None:
  # Queue is empty (drained by autouse fixture); waits up to 1s then returns None
  result = _mod.pop_valid_message()
  assert result is None


def test_pop_valid_message_prefers_higher_priority_coscheduled() -> None:
  low = _mod.QueuedMessage(priority=0, seq=0, name='low', scheduled_at=time.monotonic(), data={}, hold=60, timeout=60)
  high = _mod.QueuedMessage(priority=9, seq=1, name='high', scheduled_at=time.monotonic(), data={}, hold=60, timeout=60)
  _mod._queue.put(low)
  _mod._queue.put(high)
  with patch('time.sleep'):
    result = _mod.pop_valid_message()
  assert result is not None
  assert result.name == 'high'


def test_pop_valid_message_requeues_lower_priority() -> None:
  low = _mod.QueuedMessage(priority=0, seq=0, name='low', scheduled_at=time.monotonic(), data={}, hold=60, timeout=60)
  high = _mod.QueuedMessage(priority=9, seq=1, name='high', scheduled_at=time.monotonic(), data={}, hold=60, timeout=60)
  _mod._queue.put(low)
  _mod._queue.put(high)
  with patch('time.sleep'):
    _mod.pop_valid_message()
  assert not _mod._queue.empty()
  requeued = _mod._queue.get_nowait()
  assert requeued.name == 'low'


def test_pop_valid_message_discards_expired_in_batch() -> None:
  expired = _mod.QueuedMessage(
    priority=9, seq=0, name='expired', scheduled_at=time.monotonic() - 100, data={}, hold=60, timeout=10
  )
  valid = _mod.QueuedMessage(
    priority=0, seq=1, name='valid', scheduled_at=time.monotonic(), data={}, hold=60, timeout=60
  )
  _mod._queue.put(expired)
  _mod._queue.put(valid)
  with patch('time.sleep'):
    result = _mod.pop_valid_message()
  assert result is not None
  assert result.name == 'valid'
  assert _mod._queue.empty()


# --- _load_file ---


def _make_content(
  *,
  priority: int = 5,
  public: bool = True,
  truncation: str | None = None,
) -> dict[str, Any]:
  template: dict[str, Any] = {
    'schedule': {'cron': '0 8 * * *', 'hold': 60, 'timeout': 60},
    'priority': priority,
    'public': public,
    'templates': [{'format': ['HELLO']}],
  }
  if truncation is not None:
    template['truncation'] = truncation
  return {'templates': {'tmpl': template}}


def test_load_file_prints_registration(
  sched: BackgroundScheduler, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content()))
  _mod._load_file(sched, f, False)
  out = capsys.readouterr().out
  assert 'test.json' in out
  assert 'tmpl' in out
  assert 'cron=' in out
  assert 'priority=' in out


def test_load_file_log_cron_padding_outside_quotes(
  sched: BackgroundScheduler, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  # Two templates with crons of different lengths — the shorter one should be
  # padded with spaces OUTSIDE the quotes, not inside (bug #150).
  content: dict[str, Any] = {
    'templates': {
      'short': {
        'schedule': {'cron': '0 8 * * *', 'hold': 60, 'timeout': 60},
        'priority': 5,
        'templates': [{'format': ['HI']}],
      },
      'long': {
        'schedule': {'cron': '0 20 * * 1-5', 'hold': 60, 'timeout': 60},
        'priority': 5,
        'templates': [{'format': ['BYE']}],
      },
    }
  }
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(content))
  _mod._load_file(sched, f, False)
  out = capsys.readouterr().out
  # Quotes must close immediately after the cron value — no trailing spaces inside
  assert 'cron="0 8 * * *"' in out
  assert 'cron="0 20 * * 1-5"' in out


def test_load_file_log_hold_timeout_suffix_before_padding(
  sched: BackgroundScheduler, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
  # Two templates where hold values differ in length — the 's' suffix must be
  # attached to the value before padding, not after (bug #150).
  content: dict[str, Any] = {
    'templates': {
      'short_hold': {
        'schedule': {'cron': '0 8 * * *', 'hold': 180, 'timeout': 120},
        'priority': 5,
        'templates': [{'format': ['HI']}],
      },
      'long_hold': {
        'schedule': {'cron': '0 8 * * *', 'hold': 3600, 'timeout': 3600},
        'priority': 5,
        'templates': [{'format': ['BYE']}],
      },
    }
  }
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(content))
  _mod._load_file(sched, f, False)
  out = capsys.readouterr().out
  # 's' must immediately follow the number — no space between number and 's'
  assert 'hold=180s' in out
  assert 'timeout=120s' in out


def test_load_file_log_widths_from_effective_values(
  sched: BackgroundScheduler,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  # When a cron override is shorter than the JSON value, column widths must be
  # computed from the effective (post-override) value, not the original (bug #150).
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'test': {'schedules': {'tmpl': {'cron': '* * * * *'}}}},
  )
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content()))  # JSON cron is '0 8 * * *' (same length)
  _mod._load_file(sched, f, False)
  out = capsys.readouterr().out
  # Override cron is '* * * * *'; must appear without extra padding inside quotes
  assert 'cron="* * * * *"' in out


def test_load_file_registers_job(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content()))
  _mod._load_file(sched, f, False)
  assert len(sched.get_jobs()) == 1


def test_load_file_invalid_priority_raises(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'bad.json'
  f.write_text(json.dumps(_make_content(priority=99)))
  with pytest.raises(ValueError, match='priority'):
    _mod._load_file(sched, f, False)


def test_load_file_invalid_truncation_raises(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'bad.json'
  f.write_text(json.dumps(_make_content(truncation='bogus')))
  with pytest.raises(ValueError, match='truncation'):
    _mod._load_file(sched, f, False)


def test_load_file_public_mode_skips_private(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(public=False)))
  _mod._load_file(sched, f, public_mode=True)
  assert len(sched.get_jobs()) == 0


def test_load_file_public_mode_keeps_public(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(public=True)))
  _mod._load_file(sched, f, public_mode=True)
  assert len(sched.get_jobs()) == 1


# --- enqueue ---


def test_enqueue_puts_message_on_queue() -> None:
  _mod.enqueue(priority=5, data={'x': 1}, hold=30, timeout=60, name='test')
  msg = _mod._queue.get_nowait()
  assert msg.priority == 5
  assert msg.name == 'test'
  assert msg.hold == 30
  assert msg.timeout == 60
  assert msg.data == {'x': 1}


def test_enqueue_seq_increments() -> None:
  _mod.enqueue(priority=5, data={}, hold=10, timeout=10, name='first')
  _mod.enqueue(priority=5, data={}, hold=10, timeout=10, name='second')
  # Both have the same priority, so lower seq is popped first.
  msg1 = _mod._queue.get_nowait()
  msg2 = _mod._queue.get_nowait()
  assert msg1.seq < msg2.seq


# --- load_content ---


def _make_file(directory: Path, name: str = 'test.json') -> Path:
  f = directory / name
  f.write_text(json.dumps(_make_content()))
  return f


def test_load_content_loads_user_files(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  user_dir = tmp_path / 'content' / 'user'
  user_dir.mkdir(parents=True)
  _make_file(user_dir)
  monkeypatch.chdir(tmp_path)
  _mod.load_content(sched)
  assert len(sched.get_jobs()) == 1


def test_load_content_contrib_disabled_by_default(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  contrib_dir = tmp_path / 'content' / 'contrib'
  contrib_dir.mkdir(parents=True)
  _make_file(contrib_dir)
  monkeypatch.chdir(tmp_path)
  _mod.load_content(sched)
  assert len(sched.get_jobs()) == 0


def test_load_content_contrib_enabled_by_stem(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  contrib_dir = tmp_path / 'content' / 'contrib'
  contrib_dir.mkdir(parents=True)
  _make_file(contrib_dir, 'bart.json')
  monkeypatch.chdir(tmp_path)
  _mod.load_content(sched, content_enabled={'bart'})
  assert len(sched.get_jobs()) == 1


def test_load_content_contrib_enabled_star(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  contrib_dir = tmp_path / 'content' / 'contrib'
  contrib_dir.mkdir(parents=True)
  _make_file(contrib_dir, 'anything.json')
  monkeypatch.chdir(tmp_path)
  _mod.load_content(sched, content_enabled={'*'})
  assert len(sched.get_jobs()) == 1


def test_load_content_missing_dirs_dont_raise(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  monkeypatch.chdir(tmp_path)
  _mod.load_content(sched)  # no content/ dir — should not raise
  assert len(sched.get_jobs()) == 0


# --- _load_file schedule overrides ---


def test_load_file_applies_schedule_override(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  import config as _cfg

  # Override hold and timeout for the template in this file.
  monkeypatch.setattr(
    _cfg,
    '_config',
    {'test': {'schedules': {'tmpl': {'hold': 120, 'timeout': 30}}}},
  )
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content()))
  _mod._load_file(sched, f, False)
  jobs = sched.get_jobs()
  assert len(jobs) == 1
  # job.args: [priority, data, hold, timeout, job_id]
  assert jobs[0].args[2] == 120  # hold overridden
  assert jobs[0].args[3] == 30  # timeout overridden


def test_load_file_applies_priority_override(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'test': {'schedules': {'tmpl': {'priority': 9}}}},
  )
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(priority=5)))
  _mod._load_file(sched, f, False)
  # job.args: [priority, data, hold, timeout, job_id]
  assert sched.get_jobs()[0].args[0] == 9  # priority overridden from 5 to 9


def test_load_file_ignores_invalid_type_priority_override(
  sched: BackgroundScheduler,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'test': {'schedules': {'tmpl': {'priority': 'high'}}}},
  )
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(priority=5)))
  _mod._load_file(sched, f, False)
  assert sched.get_jobs()[0].args[0] == 5  # original priority preserved
  assert 'Warning' in capsys.readouterr().out


def test_load_file_ignores_out_of_range_priority_override(
  sched: BackgroundScheduler,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'test': {'schedules': {'tmpl': {'priority': 11}}}},
  )
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(priority=5)))
  _mod._load_file(sched, f, False)
  assert sched.get_jobs()[0].args[0] == 5  # original priority preserved
  assert 'Warning' in capsys.readouterr().out


def test_load_file_ignores_unknown_override_keys(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'test': {'schedules': {'tmpl': {'hold': 90, 'unknown_field': 'ignored'}}}},
  )
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content()))
  _mod._load_file(sched, f, False)  # must not raise on unknown key
  assert sched.get_jobs()[0].args[2] == 90  # hold applied


# --- worker ---


def _make_worker_msg(*, scheduled_at: float, timeout: int) -> Any:
  return _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=scheduled_at,
    data={
      'templates': [{'format': ['HELLO']}],
      'variables': {},
      'truncation': 'hard',
    },
    hold=60,
    timeout=timeout,
  )


def test_worker_board_locked_requeues_within_timeout() -> None:
  msg = _make_worker_msg(scheduled_at=time.monotonic(), timeout=3600)
  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch('integrations.vestaboard.set_state', side_effect=vb.BoardLockedError('locked')),
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  assert not _mod._queue.empty()


def test_worker_board_locked_discards_after_timeout() -> None:
  msg = _make_worker_msg(scheduled_at=time.monotonic() - 1000, timeout=10)
  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch('integrations.vestaboard.set_state', side_effect=vb.BoardLockedError('locked')),
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  assert _mod._queue.empty()


def test_worker_log_includes_template_name(capsys: pytest.CaptureFixture[str]) -> None:
  msg = _make_worker_msg(scheduled_at=time.monotonic(), timeout=3600)
  msg.name = 'user.test.my_template'
  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch('integrations.vestaboard.set_state'),
    patch('time.sleep'),
    patch.object(_mod, '_hold_interrupt'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  out = capsys.readouterr().out
  assert 'user.test.my_template' in out


# --- _load_file (public key missing) ---


def test_load_file_public_key_missing_included_in_public_mode(sched: BackgroundScheduler, tmp_path: Path) -> None:
  # A template with no 'public' key should default to included (True) in
  # public mode rather than raising a KeyError.
  content: dict[str, Any] = {
    'templates': {
      'tmpl': {
        'schedule': {'cron': '0 8 * * *', 'hold': 60, 'timeout': 60},
        'priority': 5,
        # 'public' key intentionally omitted
        'templates': [{'format': ['HELLO']}],
      }
    }
  }
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(content))
  _mod._load_file(sched, f, public_mode=True)
  assert len(sched.get_jobs()) == 1


# --- _validate_template ---


def _base_template() -> dict[str, Any]:
  return {
    'schedule': {'cron': '0 8 * * *', 'hold': 60, 'timeout': 60},
    'priority': 5,
    'templates': [{'format': ['HELLO']}],
  }


def test_validate_template_valid_passes() -> None:
  _mod._validate_template('ctx.tmpl', _base_template())


def test_validate_template_missing_schedule_raises() -> None:
  t = _base_template()
  del t['schedule']
  with pytest.raises(ValueError, match='schedule'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_invalid_cron_raises() -> None:
  t = _base_template()
  t['schedule']['cron'] = 123
  with pytest.raises(ValueError, match='cron'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_negative_hold_raises() -> None:
  t = _base_template()
  t['schedule']['hold'] = -1
  with pytest.raises(ValueError, match='hold'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_negative_timeout_raises() -> None:
  t = _base_template()
  t['schedule']['timeout'] = -5
  with pytest.raises(ValueError, match='timeout'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_invalid_priority_raises() -> None:
  t = _base_template()
  t['priority'] = 99
  with pytest.raises(ValueError, match='priority'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_invalid_truncation_raises() -> None:
  t = _base_template()
  t['truncation'] = 'bogus'
  with pytest.raises(ValueError, match='truncation'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_no_templates_no_integration_raises() -> None:
  t = _base_template()
  del t['templates']
  with pytest.raises(ValueError, match='templates.*integration|integration.*templates'):
    _mod._validate_template('ctx.tmpl', t)


def test_validate_template_integration_only_passes() -> None:
  t = _base_template()
  del t['templates']
  t['integration'] = 'bart'
  _mod._validate_template('ctx.tmpl', t)


def test_validate_template_both_templates_and_integration_passes() -> None:
  t = _base_template()
  t['integration'] = 'bart'
  _mod._validate_template('ctx.tmpl', t)


def test_validate_template_zero_hold_timeout_passes() -> None:
  t = _base_template()
  t['schedule']['hold'] = 0
  t['schedule']['timeout'] = 0
  _mod._validate_template('ctx.tmpl', t)


# --- main ---


def _mock_sched() -> MagicMock:
  """Return a BackgroundScheduler mock with get_jobs returning an empty list."""
  m = MagicMock()
  m.get_jobs.return_value = []
  return m


def test_main_note_startup_banner(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch('config.get_model', return_value='note'),
    patch('config.get_public_mode', return_value=False),
    patch('config.get_content_enabled', return_value=set()),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  out = capsys.readouterr().out
  assert 'Note (3×15)' in out


def test_main_flagship_sets_model_and_banner(
  monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  monkeypatch.setattr(vb, 'model', vb.VestaboardModel.NOTE)  # ensures restoration
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch('config.get_model', return_value='flagship'),
    patch('config.get_public_mode', return_value=False),
    patch('config.get_content_enabled', return_value=set()),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  assert vb.model is vb.VestaboardModel.FLAGSHIP
  out = capsys.readouterr().out
  assert 'Flagship (6×22)' in out


def test_main_public_mode_in_banner(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch('config.get_model', return_value='note'),
    patch('config.get_public_mode', return_value=True),
    patch('config.get_content_enabled', return_value=set()),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  out = capsys.readouterr().out
  assert 'public mode' in out


def test_main_content_enabled_in_banner(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch('config.get_model', return_value='note'),
    patch('config.get_public_mode', return_value=False),
    patch('config.get_content_enabled', return_value={'bart'}),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  out = capsys.readouterr().out
  assert 'contrib: bart' in out


def test_main_empty_board_on_startup(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch('config.get_model', return_value='note'),
    patch('config.get_public_mode', return_value=False),
    patch('config.get_content_enabled', return_value=set()),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', side_effect=vb.EmptyBoardError('no message')),
    patch('threading.Thread'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  out = capsys.readouterr().out
  assert '(no current message)' in out


def test_main_passes_timezone_to_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
  from zoneinfo import ZoneInfo

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'scheduler': {'timezone': 'America/New_York'}})
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch('config.get_model', return_value='note'),
    patch('config.get_public_mode', return_value=False),
    patch('config.get_content_enabled', return_value=set()),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
    patch('scheduler.BackgroundScheduler', return_value=mock_sched) as mock_bs,
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  mock_bs.assert_called_once_with(
    misfire_grace_time=300,
    timezone=ZoneInfo('America/New_York'),
  )


def test_worker_calls_integration_get_variables() -> None:
  msg = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={
      'templates': [{'format': ['{greeting}']}],
      'variables': {},
      'truncation': 'hard',
      'integration': 'bart',
    },
    hold=0,
    timeout=3600,
  )
  mock_integration = MagicMock()
  mock_integration.get_variables.return_value = {'greeting': [['HELLO']]}
  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch.object(_mod, '_get_integration', return_value=mock_integration),
    patch('integrations.vestaboard.set_state'),
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  mock_integration.get_variables.assert_called_once()


def test_worker_logs_and_skips_on_missing_integration_deps() -> None:
  msg = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={
      'templates': [{'format': ['HELLO']}],
      'variables': {},
      'truncation': 'hard',
      'integration': 'bart',
    },
    hold=0,
    timeout=3600,
  )
  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch.object(_mod, '_get_integration', side_effect=RuntimeError('missing dependencies')),
    patch('integrations.vestaboard.set_state') as mock_set_state,
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  mock_set_state.assert_not_called()


def test_worker_skips_on_duplicate_content() -> None:
  msg = _make_worker_msg(scheduled_at=time.monotonic(), timeout=3600)
  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch('integrations.vestaboard.set_state', side_effect=vb.DuplicateContentError('already shown')),
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  # Message is discarded — not re-enqueued and hold sleep not called
  assert _mod._queue.empty()


# --- _get_integration ---


def test_get_integration_unknown_raises() -> None:
  with pytest.raises(ValueError, match='Unknown integration'):
    _mod._get_integration('os')


def test_get_integration_path_traversal_raises() -> None:
  with pytest.raises(ValueError, match='Unknown integration'):
    _mod._get_integration('../something')


def test_get_integration_known_loads_module() -> None:
  import integrations.bart as bart_mod

  with patch('importlib.import_module', return_value=bart_mod) as mock_import:
    result = _mod._get_integration('bart')
  mock_import.assert_called_once_with('integrations.bart')
  assert result is bart_mod


def test_get_integration_missing_deps_raises_runtime_error() -> None:
  _mod._integrations.pop('bart', None)
  with patch('importlib.import_module', side_effect=ImportError('No module named requests')):
    with pytest.raises(RuntimeError, match='missing dependencies'):
      _mod._get_integration('bart')


def test_get_integration_caches_module() -> None:
  import integrations.bart as bart_mod

  # Clear cache so the test starts fresh.
  _mod._integrations.pop('bart', None)
  with patch('importlib.import_module', return_value=bart_mod) as mock_import:
    _mod._get_integration('bart')
    _mod._get_integration('bart')
  mock_import.assert_called_once()


# --- worker: IntegrationDataUnavailableError ---


def test_worker_silently_skips_on_data_unavailable() -> None:
  msg = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={
      'templates': [{'format': ['HELLO']}],
      'variables': {},
      'truncation': 'hard',
      'integration': 'bart',
    },
    hold=0,
    timeout=3600,
  )
  mock_integration = MagicMock()
  mock_integration.get_variables.side_effect = IntegrationDataUnavailableError('no data')

  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch.object(_mod, '_get_integration', return_value=mock_integration),
    patch('integrations.vestaboard.set_state') as mock_set_state,
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()

  mock_set_state.assert_not_called()
  assert _mod._queue.empty()


# --- worker: integration_fn ---


def test_worker_uses_integration_fn_when_specified() -> None:
  msg = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={
      'templates': [{'format': ['{val}']}],
      'variables': {},
      'truncation': 'hard',
      'integration': 'bart',
      'integration_fn': 'get_variables_custom',
    },
    hold=0,
    timeout=3600,
  )
  mock_integration = MagicMock()
  mock_integration.get_variables_custom.return_value = {'val': [['OK']]}

  with (
    patch.object(_mod, 'pop_valid_message', side_effect=[msg, KeyboardInterrupt()]),
    patch.object(_mod, '_get_integration', return_value=mock_integration),
    patch('integrations.vestaboard.set_state'),
    patch('time.sleep'),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()

  mock_integration.get_variables_custom.assert_called_once()
  mock_integration.get_variables.assert_not_called()


# --- _validate_template: integration_fn ---


def test_validate_template_integration_fn_string_passes() -> None:
  t = _base_template()
  t['integration'] = 'bart'
  t['integration_fn'] = 'get_variables_custom'
  _mod._validate_template('ctx.tmpl', t)


def test_validate_template_integration_fn_non_string_raises() -> None:
  t = _base_template()
  t['integration'] = 'bart'
  t['integration_fn'] = 123
  with pytest.raises(ValueError, match='integration_fn'):
    _mod._validate_template('ctx.tmpl', t)


# --- _validate_startup ---


def test_validate_startup_errors_on_config_dir(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  config_dir = tmp_path / 'config.toml'
  config_dir.mkdir()
  monkeypatch.chdir(tmp_path)
  with pytest.raises(SystemExit):
    _mod._validate_startup()
  assert 'directory' in capsys.readouterr().err.lower()


def test_validate_startup_errors_on_missing_config(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  monkeypatch.chdir(tmp_path)
  with pytest.raises(SystemExit):
    _mod._validate_startup()
  assert 'not found' in capsys.readouterr().err.lower()


def test_validate_startup_errors_on_empty_config(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  (tmp_path / 'config.toml').write_text('')
  monkeypatch.chdir(tmp_path)
  with pytest.raises(SystemExit):
    _mod._validate_startup()
  assert 'empty' in capsys.readouterr().err.lower()


def test_validate_startup_warns_on_empty_content_dir(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
  (tmp_path / 'config.toml').write_text('[vestaboard]\napi_key = "x"\n')
  user_dir = tmp_path / 'content' / 'user'
  user_dir.mkdir(parents=True)
  monkeypatch.chdir(tmp_path)
  _mod._validate_startup()  # must not raise
  assert 'warning' in capsys.readouterr().out.lower()


def test_validate_startup_passes_with_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  (tmp_path / 'config.toml').write_text('[vestaboard]\napi_key = "x"\n')
  monkeypatch.chdir(tmp_path)
  _mod._validate_startup()  # must not raise


# --- _do_hold tests ---


def _make_message(priority: int, hold: int = 120) -> _mod.QueuedMessage:
  return _mod.QueuedMessage(
    priority=priority,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={},
    hold=hold,
    timeout=300,
  )


def _enqueue_priority(priority: int) -> None:
  """Put a bare message with the given priority directly onto the shared queue."""
  _mod._queue.put(_make_message(priority))


def test_do_hold_runs_full_duration_no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
  """Hold runs to completion when queue is empty."""
  message = _make_message(priority=4, hold=2)
  _mod._hold_interrupt.clear()
  start = time.monotonic()
  _mod._do_hold(message, min_hold=1)
  assert time.monotonic() - start >= 1.9


def test_do_hold_webhook_interrupt_exits_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
  """Webhook interrupt (_hold_interrupt set) exits before full hold."""
  message = _make_message(priority=4, hold=30)
  _mod._hold_interrupt.set()
  start = time.monotonic()
  _mod._do_hold(message, min_hold=1)
  assert time.monotonic() - start < 5


def test_do_hold_interrupts_after_min_hold(monkeypatch: pytest.MonkeyPatch) -> None:
  """After min_hold, a High-priority queued item interrupts a low-priority hold."""
  message = _make_message(priority=4, hold=30)
  _mod._hold_interrupt.clear()
  _enqueue_priority(8)
  start = time.monotonic()
  _mod._do_hold(message, min_hold=1)
  elapsed = time.monotonic() - start
  assert elapsed < 10  # exited early, not the full 30s


def test_do_hold_not_interrupted_before_min_hold(monkeypatch: pytest.MonkeyPatch) -> None:
  """High-priority item in queue but min_hold not elapsed — hold runs to completion."""
  message = _make_message(priority=4, hold=2)
  _mod._hold_interrupt.clear()
  _enqueue_priority(8)
  start = time.monotonic()
  _mod._do_hold(message, min_hold=60)  # min_hold longer than hold
  assert time.monotonic() - start >= 1.9  # ran the full hold


def test_do_hold_no_interrupt_when_current_is_high_priority(monkeypatch: pytest.MonkeyPatch) -> None:
  """High-priority current message is never interrupted even with a high-priority waiter."""
  message = _make_message(priority=8, hold=2)
  _mod._hold_interrupt.clear()
  _enqueue_priority(9)
  start = time.monotonic()
  _mod._do_hold(message, min_hold=0)
  assert time.monotonic() - start >= 1.9  # ran the full hold


def test_do_hold_no_interrupt_when_queued_item_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
  """Queued item at priority 7 (Elevated) does not interrupt a low-priority hold."""
  message = _make_message(priority=4, hold=2)
  _mod._hold_interrupt.clear()
  _enqueue_priority(7)
  start = time.monotonic()
  _mod._do_hold(message, min_hold=0)
  assert time.monotonic() - start >= 1.9  # ran the full hold
