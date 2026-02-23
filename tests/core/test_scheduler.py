import json
import os
import time
from pathlib import Path
from queue import Empty
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import integrations.vestaboard as vb
import scheduler as _mod


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
  result = _mod.pop_valid_message()
  assert result is not None
  assert result.name == 'valid'


def test_pop_valid_message_returns_none_when_empty() -> None:
  # Queue is empty (drained by autouse fixture); waits up to 1s then returns None
  result = _mod.pop_valid_message()
  assert result is None


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
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.worker()
  out = capsys.readouterr().out
  assert 'user.test.my_template' in out


# --- watch_content ---


def test_watch_content_detects_new_file(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  user_dir = tmp_path / 'content' / 'user'
  user_dir.mkdir(parents=True)
  monkeypatch.chdir(tmp_path)
  new_file = user_dir / 'new.json'
  loaded: list[Path] = []

  def fake_sleep(n: float) -> None:
    # Write the file during sleep so eligible_files() sees it on the next scan.
    if not new_file.exists():
      new_file.write_text(json.dumps(_make_content()))

  def fake_load(s: Any, path: Path, public: bool) -> None:
    loaded.append(path)
    raise KeyboardInterrupt  # stop after the first detection

  with (
    patch.object(_mod, '_load_file', side_effect=fake_load),
    patch('time.sleep', side_effect=fake_sleep),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.watch_content(sched, False, set())

  assert any(p.resolve() == new_file.resolve() for p in loaded)


def test_watch_content_detects_removed_file(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  user_dir = tmp_path / 'content' / 'user'
  user_dir.mkdir(parents=True)
  existing = user_dir / 'existing.json'
  existing.write_text(json.dumps(_make_content()))
  monkeypatch.chdir(tmp_path)
  # Seed the scheduler with the file's job so we can verify it gets removed.
  _mod._load_file(sched, existing, False)
  assert len(sched.get_jobs()) == 1

  sleep_count = 0

  def fake_sleep(n: float) -> None:
    nonlocal sleep_count
    sleep_count += 1
    if sleep_count == 1:
      existing.unlink()
    else:
      raise KeyboardInterrupt

  with patch('time.sleep', side_effect=fake_sleep):
    with pytest.raises(KeyboardInterrupt):
      _mod.watch_content(sched, False, set())

  assert len(sched.get_jobs()) == 0


def test_watch_content_detects_modified_file(
  sched: BackgroundScheduler, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  user_dir = tmp_path / 'content' / 'user'
  user_dir.mkdir(parents=True)
  existing = user_dir / 'existing.json'
  existing.write_text(json.dumps(_make_content()))
  monkeypatch.chdir(tmp_path)
  reloaded: list[Path] = []

  sleep_count = 0

  def fake_sleep(n: float) -> None:
    nonlocal sleep_count
    sleep_count += 1
    if sleep_count == 1:
      existing.write_text(json.dumps(_make_content()))
      # Explicitly advance mtime by 1s — on fast CI filesystems two writes
      # within the same timestamp quantum produce identical mtimes.
      st = existing.stat()
      os.utime(existing, (st.st_atime + 1, st.st_mtime + 1))
    else:
      raise KeyboardInterrupt

  def fake_load(s: Any, path: Path, public: bool) -> None:
    reloaded.append(path)

  with (
    patch.object(_mod, '_load_file', side_effect=fake_load),
    patch('time.sleep', side_effect=fake_sleep),
  ):
    with pytest.raises(KeyboardInterrupt):
      _mod.watch_content(sched, False, set())

  assert any(p.resolve() == existing.resolve() for p in reloaded)


def test_watch_content_load_error_does_not_crash(
  sched: BackgroundScheduler,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  user_dir = tmp_path / 'content' / 'user'
  user_dir.mkdir(parents=True)
  monkeypatch.chdir(tmp_path)

  sleep_count = 0

  def fake_sleep(n: float) -> None:
    nonlocal sleep_count
    sleep_count += 1
    if sleep_count == 1:
      (user_dir / 'bad.json').write_text('not valid json')
    else:
      raise KeyboardInterrupt

  with patch('time.sleep', side_effect=fake_sleep):
    with pytest.raises(KeyboardInterrupt):
      _mod.watch_content(sched, False, set())

  out = capsys.readouterr().out
  assert 'Error' in out  # error printed; watcher did not crash


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
  monkeypatch.setattr('sys.argv', ['e-note-ion.py'])
  mock_sched = _mock_sched()
  with (
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
  monkeypatch.setattr('sys.argv', ['e-note-ion.py', '--flagship'])
  monkeypatch.setattr(vb, 'model', vb.VestaboardModel.NOTE)  # ensures restoration
  mock_sched = _mock_sched()
  with (
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
  monkeypatch.setattr('sys.argv', ['e-note-ion.py', '--public'])
  mock_sched = _mock_sched()
  with (
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
  monkeypatch.setattr('sys.argv', ['e-note-ion.py', '--content-enabled', 'bart'])
  mock_sched = _mock_sched()
  with (
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
  monkeypatch.setattr('sys.argv', ['e-note-ion.py'])
  mock_sched = _mock_sched()
  with (
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', side_effect=vb.EmptyBoardError('no message')),
    patch('threading.Thread'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()
  out = capsys.readouterr().out
  assert '(no current message)' in out


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


def test_get_integration_caches_module() -> None:
  import integrations.bart as bart_mod

  # Clear cache so the test starts fresh.
  _mod._integrations.pop('bart', None)
  with patch('importlib.import_module', return_value=bart_mod) as mock_import:
    _mod._get_integration('bart')
    _mod._get_integration('bart')
  mock_import.assert_called_once()
