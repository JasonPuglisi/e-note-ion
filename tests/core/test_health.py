"""Tests for the health tracking module."""

import json
import logging
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import health as _mod


@pytest.fixture(autouse=True)
def _reset_health() -> None:
  """Ensure clean health state for each test.

  Log path isolation is handled by the conftest fixture.
  """
  _mod.reset()
  _mod.init()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_creates_integration() -> None:
  _mod.register('weather')
  summary = _mod.get_summary()
  assert 'weather' in summary['integrations']


def test_register_is_idempotent() -> None:
  _mod.register('weather')
  _mod.register('weather')
  summary = _mod.get_summary()
  assert len(summary['integrations']) == 1


# ---------------------------------------------------------------------------
# Event recording
# ---------------------------------------------------------------------------


def test_record_success() -> None:
  _mod.register('weather')
  _mod.record_success('weather')
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['status'] == 'healthy'
  assert detail['total_events'] == 1
  assert detail['last_success'] is not None


def test_record_expected_empty() -> None:
  _mod.register('trakt')
  _mod.record_expected_empty('trakt')
  detail = _mod.get_summary()['integrations']['trakt']
  assert detail['status'] == 'healthy'
  assert detail['last_expected_empty'] is not None


def test_record_error() -> None:
  _mod.register('bart')
  _mod.record_error('bart', 'API 503')
  detail = _mod.get_summary()['integrations']['bart']
  assert detail['status'] == 'error'
  assert detail['last_error_message'] == 'API 503'


def test_record_on_unregistered_is_noop() -> None:
  _mod.record_success('nonexistent')
  _mod.record_error('nonexistent', 'fail')
  _mod.record_expected_empty('nonexistent')
  assert _mod.get_summary()['integrations'] == {}


# ---------------------------------------------------------------------------
# Status computation — threshold-based
# ---------------------------------------------------------------------------


def test_status_unknown_no_events() -> None:
  _mod.register('weather')
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['status'] == 'unknown'


def test_status_healthy_all_successes() -> None:
  _mod.register('weather')
  for _ in range(5):
    _mod.record_success('weather')
  assert _mod.get_summary()['integrations']['weather']['status'] == 'healthy'


def test_status_healthy_mixed_success_and_expected_empty() -> None:
  _mod.register('trakt')
  _mod.record_success('trakt')
  _mod.record_expected_empty('trakt')
  _mod.record_success('trakt')
  assert _mod.get_summary()['integrations']['trakt']['status'] == 'healthy'


def test_status_degraded_errors_with_successes() -> None:
  """50% success rate (below 70% threshold) → degraded."""
  _mod.register('bart')
  _mod.record_success('bart')
  _mod.record_error('bart', 'fail')
  _mod.record_success('bart')
  _mod.record_error('bart', 'fail')
  assert _mod.get_summary()['integrations']['bart']['status'] == 'degraded'


def test_status_error_all_errors() -> None:
  _mod.register('bart')
  for _ in range(5):
    _mod.record_error('bart', 'fail')
  assert _mod.get_summary()['integrations']['bart']['status'] == 'error'


def test_status_error_single_error() -> None:
  """A single error (100% error rate) → error."""
  _mod.register('bart')
  _mod.record_error('bart', 'fail')
  assert _mod.get_summary()['integrations']['bart']['status'] == 'error'


def test_status_error_errors_and_expected_empty_only() -> None:
  """Errors + expected_empty (no successes) → degraded, because expected_empty
  doesn't count as an error."""
  _mod.register('bart')
  _mod.record_expected_empty('bart')
  _mod.record_error('bart', 'fail')
  _mod.record_error('bart', 'fail')
  assert _mod.get_summary()['integrations']['bart']['status'] == 'degraded'


def test_status_healthy_above_threshold() -> None:
  """80% success rate (above 70% threshold) → healthy."""
  _mod.register('weather')
  for _ in range(8):
    _mod.record_success('weather')
  for _ in range(2):
    _mod.record_error('weather', 'fail')
  assert _mod.get_summary()['integrations']['weather']['status'] == 'healthy'


def test_status_degraded_below_threshold() -> None:
  """60% success rate (below 70% threshold) → degraded."""
  _mod.register('weather')
  for _ in range(6):
    _mod.record_success('weather')
  for _ in range(4):
    _mod.record_error('weather', 'fail')
  assert _mod.get_summary()['integrations']['weather']['status'] == 'degraded'


def test_status_healthy_at_threshold() -> None:
  """70% success rate (exactly at threshold) → healthy."""
  _mod.register('weather')
  for _ in range(7):
    _mod.record_success('weather')
  for _ in range(3):
    _mod.record_error('weather', 'fail')
  assert _mod.get_summary()['integrations']['weather']['status'] == 'healthy'


def test_rolling_window_capped_at_20() -> None:
  _mod.register('weather')
  # Fill with errors
  for _ in range(20):
    _mod.record_error('weather', 'fail')
  assert _mod.get_summary()['integrations']['weather']['status'] == 'error'
  # Push out errors with successes
  for _ in range(20):
    _mod.record_success('weather')
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['status'] == 'healthy'
  assert detail['total_events'] == 20


# ---------------------------------------------------------------------------
# Overall status
# ---------------------------------------------------------------------------


def test_overall_healthy_all_good() -> None:
  _mod.register('weather')
  _mod.register('bart')
  _mod.record_success('weather')
  _mod.record_success('bart')
  assert _mod.overall_status() == _mod.Status.HEALTHY


def test_overall_healthy_with_unknown() -> None:
  _mod.register('weather')
  _mod.register('bart')
  _mod.record_success('weather')
  # bart has no events → unknown
  assert _mod.overall_status() == _mod.Status.HEALTHY


def test_overall_degraded_below_threshold() -> None:
  """50% success rate on bart → degraded overall."""
  _mod.register('weather')
  _mod.register('bart')
  _mod.record_success('weather')
  _mod.record_success('bart')
  _mod.record_error('bart', 'fail')
  assert _mod.overall_status() == _mod.Status.DEGRADED


def test_overall_unhealthy_any_error() -> None:
  _mod.register('weather')
  _mod.register('bart')
  _mod.record_success('weather')
  _mod.record_error('bart', 'fail')
  assert _mod.overall_status() == _mod.Status.ERROR


# ---------------------------------------------------------------------------
# Summary format
# ---------------------------------------------------------------------------


def test_summary_structure() -> None:
  _mod.register('weather')
  _mod.record_success('weather')
  summary = _mod.get_summary()
  assert 'status' in summary
  assert 'uptime_seconds' in summary
  assert isinstance(summary['uptime_seconds'], int)
  assert 'vestaboard' in summary
  assert 'integrations' in summary
  detail = summary['integrations']['weather']
  assert 'status' in detail
  assert 'last_success' in detail
  assert 'last_expected_empty' in detail
  assert 'last_error' in detail
  assert 'last_error_message' in detail
  assert 'last_locked' in detail
  assert 'locked_events' in detail
  assert 'success_rate' in detail
  assert 'total_events' in detail
  assert 'registered_at' in detail


def test_summary_is_json_serializable() -> None:
  _mod.register('weather')
  _mod.record_success('weather')
  _mod.record_error('weather', 'timeout')
  json.dumps(_mod.get_summary())


def test_success_rate_computation() -> None:
  _mod.register('bart')
  _mod.record_success('bart')
  _mod.record_success('bart')
  _mod.record_error('bart', 'fail')
  detail = _mod.get_summary()['integrations']['bart']
  assert detail['success_rate'] == pytest.approx(2 / 3)


def test_success_rate_none_when_no_events() -> None:
  _mod.register('bart')
  assert _mod.get_summary()['integrations']['bart']['success_rate'] is None


# ---------------------------------------------------------------------------
# Locked events and vestaboard target split
# ---------------------------------------------------------------------------


def test_record_locked_event() -> None:
  """record_locked() appends a LOCKED event exposed via last_locked/locked_events."""
  _mod.register(_mod.VESTABOARD_TARGET)
  _mod.record_locked(_mod.VESTABOARD_TARGET)
  detail = _mod.get_summary()['vestaboard']
  assert detail is not None
  assert detail['locked_events'] == 1
  assert detail['last_locked'] is not None
  # A lone locked event leaves the target UNKNOWN — no scored events yet.
  assert detail['status'] == 'unknown'


def test_locked_excluded_from_success_rate() -> None:
  """LOCKED events do not affect success_rate or status."""
  _mod.register(_mod.VESTABOARD_TARGET)
  for _ in range(5):
    _mod.record_success(_mod.VESTABOARD_TARGET)
  for _ in range(3):
    _mod.record_locked(_mod.VESTABOARD_TARGET)
  detail = _mod.get_summary()['vestaboard']
  assert detail['status'] == 'healthy'
  assert detail['success_rate'] == 1.0
  assert detail['total_events'] == 5  # scored events only
  assert detail['locked_events'] == 3


def test_locked_does_not_mask_errors() -> None:
  """LOCKED events don't dilute error rate — 2 success + 3 error + 10 locked = degraded."""
  _mod.register(_mod.VESTABOARD_TARGET)
  for _ in range(2):
    _mod.record_success(_mod.VESTABOARD_TARGET)
  for _ in range(3):
    _mod.record_error(_mod.VESTABOARD_TARGET, 'boom')
  for _ in range(10):
    _mod.record_locked(_mod.VESTABOARD_TARGET)
  detail = _mod.get_summary()['vestaboard']
  # 2/5 = 40% scored success → below 70% threshold → degraded
  assert detail['status'] == 'degraded'
  assert detail['total_events'] == 5
  assert detail['locked_events'] == 10


def test_vestaboard_target_registered_on_init() -> None:
  """init() auto-registers the reserved vestaboard target."""
  # _reset_health autouse fixture already called init(); assert it is present.
  summary = _mod.get_summary()
  assert 'vestaboard' in summary
  assert summary['vestaboard'] is not None
  assert summary['vestaboard']['status'] == 'unknown'
  # And it is NOT in the integrations dict.
  assert 'vestaboard' not in summary['integrations']


def test_vestaboard_errored_drives_rollup() -> None:
  """A vestaboard error propagates to top-level status even when integrations are healthy."""
  _mod.register('weather')
  _mod.record_success('weather')
  _mod.record_error(_mod.VESTABOARD_TARGET, 'HTTP 500')
  summary = _mod.get_summary()
  assert summary['status'] == 'error'
  assert summary['vestaboard']['status'] == 'error'
  assert summary['integrations']['weather']['status'] == 'healthy'


def test_vestaboard_unknown_does_not_drive_rollup() -> None:
  """vestaboard starts UNKNOWN on fresh deploy — top-level stays healthy."""
  _mod.register('weather')
  _mod.record_success('weather')
  # vestaboard registered but no events yet
  summary = _mod.get_summary()
  assert summary['status'] == 'healthy'
  assert summary['vestaboard']['status'] == 'unknown'


def test_locked_persisted_and_reloaded(tmp_path: Path) -> None:
  """LOCKED events survive a reset + init cycle."""
  _mod.register(_mod.VESTABOARD_TARGET)
  _mod.record_success(_mod.VESTABOARD_TARGET)
  _mod.record_locked(_mod.VESTABOARD_TARGET)
  _mod.record_locked(_mod.VESTABOARD_TARGET)

  log_path = _mod._LOG_PATH
  log_dir = _mod._LOG_DIR

  _mod.reset()
  _mod._LOG_PATH = log_path
  _mod._LOG_DIR = log_dir
  _mod.init()

  detail = _mod.get_summary()['vestaboard']
  assert detail['total_events'] == 1
  assert detail['locked_events'] == 2


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_recording() -> None:
  _mod.register('weather')

  def _record(n: int) -> None:
    for _ in range(n):
      _mod.record_success('weather')

  threads = [threading.Thread(target=_record, args=(100,)) for _ in range(10)]
  for t in threads:
    t.start()
  for t in threads:
    t.join()

  # All should have been recorded (deque maxlen caps at 20, but no crash)
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['total_events'] == 20
  assert detail['status'] == 'healthy'


# ---------------------------------------------------------------------------
# Periodic log
# ---------------------------------------------------------------------------


def test_log_summary(caplog: pytest.LogCaptureFixture) -> None:
  _mod.register('weather')
  _mod.record_success('weather')
  _mod.register('bart')
  _mod.record_error('bart', 'timeout')
  with caplog.at_level('INFO', logger='health'):
    _mod._log_summary()
  assert 'Health:' in caplog.text
  assert 'bart' in caplog.text


# ---------------------------------------------------------------------------
# Persistence — file I/O
# ---------------------------------------------------------------------------


def test_persistence_file_written_on_record() -> None:
  """Each record_* call appends a line to the log file."""
  _mod.register('weather')
  _mod.record_success('weather')
  _mod.record_error('weather', 'HTTP 502')

  lines = _mod._LOG_PATH.read_text().strip().splitlines()
  assert len(lines) == 2
  first = json.loads(lines[0])
  assert first['name'] == 'weather'
  assert first['event'] == 'success'
  assert 'error' not in first
  second = json.loads(lines[1])
  assert second['event'] == 'error'
  assert second['error'] == 'HTTP 502'


def test_persistence_write_and_load() -> None:
  """Events survive a reset + init cycle (simulating a restart)."""
  _mod.register('weather')
  _mod.record_success('weather')
  _mod.record_success('weather')
  _mod.record_error('weather', 'fail')

  log_path = _mod._LOG_PATH
  log_dir = _mod._LOG_DIR

  # Simulate restart: clear in-memory state, re-init from disk.
  _mod.reset()
  _mod._LOG_PATH = log_path
  _mod._LOG_DIR = log_dir
  _mod.init()

  detail = _mod.get_summary()['integrations']['weather']
  assert detail['total_events'] == 3
  assert detail['success_rate'] == pytest.approx(2 / 3)


def test_persistence_purge_old_events(tmp_path: Path) -> None:
  """Events older than _PURGE_DAYS are discarded on load."""
  log_file = tmp_path / 'health.jsonl'
  old_ts = time.time() - (_mod._PURGE_SECONDS + 3600)  # 8 days ago
  recent_ts = time.time() - 3600  # 1 hour ago

  lines = [
    json.dumps({'ts': old_ts, 'name': 'weather', 'event': 'error', 'error': 'old'}),
    json.dumps({'ts': recent_ts, 'name': 'weather', 'event': 'success'}),
  ]
  log_file.write_text('\n'.join(lines) + '\n')

  _mod.reset()
  _mod._LOG_DIR = tmp_path
  _mod._LOG_PATH = log_file
  _mod.init()

  detail = _mod.get_summary()['integrations']['weather']
  assert detail['total_events'] == 1
  assert detail['status'] == 'healthy'

  # File should be rewritten without the old event.
  remaining = log_file.read_text().strip().splitlines()
  assert len(remaining) == 1


def test_persistence_corrupt_lines_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
  """Malformed lines are skipped with a warning."""
  log_file = tmp_path / 'health.jsonl'
  recent_ts = time.time() - 60
  lines = [
    'not valid json',
    json.dumps({'ts': recent_ts, 'name': 'bart', 'event': 'success'}),
    '{bad',
  ]
  log_file.write_text('\n'.join(lines) + '\n')

  _mod.reset()
  _mod._LOG_DIR = tmp_path
  _mod._LOG_PATH = log_file
  with caplog.at_level('WARNING', logger='health'):
    _mod.init()

  assert 'skipping malformed line 1' in caplog.text
  detail = _mod.get_summary()['integrations']['bart']
  assert detail['total_events'] == 1


def test_persistence_missing_file() -> None:
  """init() with no log file starts clean (no crash)."""
  _mod.reset()
  _mod._LOG_PATH = Path('/nonexistent/health.jsonl')
  _mod._LOG_DIR = Path('/nonexistent')
  _mod.init()
  assert _mod.get_summary()['integrations'] == {}


def test_persistence_register_preserves_loaded_state() -> None:
  """register() is a no-op for integrations already loaded from disk."""
  _mod.register('weather')
  _mod.record_success('weather')
  _mod.record_success('weather')

  log_path = _mod._LOG_PATH
  log_dir = _mod._LOG_DIR

  _mod.reset()
  _mod._LOG_PATH = log_path
  _mod._LOG_DIR = log_dir
  _mod.init()

  # register() should not clobber the loaded history.
  _mod.register('weather')
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['total_events'] == 2


def test_periodic_purge_removes_stale_events(tmp_path: Path) -> None:
  """_log_summary() purges stale events from memory and rewrites the file."""
  _mod.register('weather')

  # Inject an old event directly into the deque.
  old_ts = time.time() - (_mod._PURGE_SECONDS + 3600)
  with _mod._lock:
    state = _mod._targets['weather']
    state.events.appendleft(_mod.HealthEvent(old_ts, _mod.EventType.ERROR, 'stale'))

  _mod._log_summary()

  # The old event should have been purged.
  for event in _mod._targets['weather'].events:
    assert event.timestamp > old_ts


# ---------------------------------------------------------------------------
# Overdue detection (#502)
# ---------------------------------------------------------------------------


def test_target_without_an_interval_is_never_overdue() -> None:
  """Webhook-only integrations fire on external events with no cadence."""
  _mod.init()
  _mod.register('plex')
  state = _mod._targets['plex']
  state.registered_at = time.time() - 86400 * 30

  assert state.expected_interval is None
  assert _mod._is_overdue(state, time.time()) is False
  assert _mod.get_summary()['integrations']['plex']['status'] != 'overdue'


def test_freshly_registered_target_is_not_instantly_overdue() -> None:
  """registered_at is the reference until something fires, so a restart is quiet."""
  _mod.init()
  _mod.register('bart')
  _mod.set_expected_interval('bart', 3600)

  assert _mod._is_overdue(_mod._targets['bart'], time.time()) is False


def test_target_becomes_overdue_after_two_intervals() -> None:
  _mod.init()
  _mod.register('bart')
  _mod.set_expected_interval('bart', 3600)
  state = _mod._targets['bart']

  now = time.time()
  state.registered_at = now - 3600 * 1.9
  assert _mod._is_overdue(state, now) is False, 'under 2x should not flag'

  state.registered_at = now - 3600 * 2.1
  assert _mod._is_overdue(state, now) is True


def test_a_stalled_cron_outranks_its_successful_history() -> None:
  """An integration that succeeded and then stopped firing is not healthy.

  Broader than the issue asked for: #502 scoped this to targets with no events
  at all, but a cron that ran fine and then died is the same failure and looks
  healthy under an events-only view.
  """
  _mod.init()
  _mod.register('unraid')
  _mod.set_expected_interval('unraid', 3600)
  _mod.record_success('unraid')

  state = _mod._targets['unraid']
  assert _mod._compute_status(state) == _mod.Status.HEALTHY

  state.events[-1] = _mod.HealthEvent(time.time() - 3600 * 5, _mod.EventType.SUCCESS)
  assert _mod._compute_status(state) == _mod.Status.OVERDUE


def test_overdue_drives_overall_status_and_the_503(monkeypatch: pytest.MonkeyPatch) -> None:
  _mod.init()
  _mod.register('bart')
  _mod.set_expected_interval('bart', 60)
  _mod._targets['bart'].registered_at = time.time() - 3600

  assert _mod.overall_status() == _mod.Status.OVERDUE
  assert _mod.get_summary()['status'] == 'overdue'


def test_error_still_outranks_overdue() -> None:
  _mod.init()
  _mod.register('bart')
  _mod.set_expected_interval('bart', 60)
  _mod._targets['bart'].registered_at = time.time() - 3600
  _mod.register('ynab')
  _mod.record_error('ynab', 'boom')

  assert _mod.overall_status() == _mod.Status.ERROR


def test_set_expected_interval_keeps_the_longest() -> None:
  """One integration can back several templates on different crons.

  Taking the shortest would flag an integration with both an hourly and a daily
  template every time the daily one is between runs.
  """
  _mod.init()
  _mod.register('calendar')
  _mod.set_expected_interval('calendar', 3600)
  _mod.set_expected_interval('calendar', 86400)
  _mod.set_expected_interval('calendar', 1800)

  assert _mod._targets['calendar'].expected_interval == 86400


def test_summary_exposes_interval_and_last_activity() -> None:
  _mod.init()
  _mod.register('bart')
  _mod.set_expected_interval('bart', 900)
  _mod.record_success('bart')

  detail = _mod.get_summary()['integrations']['bart']
  assert detail['expected_interval'] == 900
  assert detail['last_activity'] is not None


# ---------------------------------------------------------------------------
# Status transition watching (#504)
# ---------------------------------------------------------------------------


def _set_alert_config(monkeypatch: pytest.MonkeyPatch, **health: object) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'health': health})


def test_first_evaluation_only_establishes_a_baseline(monkeypatch: pytest.MonkeyPatch) -> None:
  """Otherwise every restart would announce a transition into its start state."""
  _set_alert_config(monkeypatch, alert_url='https://example.com/h', alert_confirm_seconds='0')
  _mod.init()
  _mod.register('bart')
  _mod.record_error('bart', 'boom')

  with patch('healthalert.notify_status_change') as notify:
    assert _mod.check_status_transition() is None
  assert not notify.called


def test_sustained_change_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
  _set_alert_config(monkeypatch, alert_url='https://example.com/h', alert_confirm_seconds='60')
  _mod.init()
  _mod.register('bart')
  _mod.record_success('bart')

  now = time.time()
  assert _mod.check_status_transition(now) is None  # baseline: healthy

  _mod.record_error('bart', 'boom')
  with patch('healthalert.notify_status_change') as notify:
    assert _mod.check_status_transition(now + 1) is None, 'first sighting starts the clock'
    assert not notify.called

    assert _mod.check_status_transition(now + 30) is None, 'still inside the window'
    assert not notify.called

    result = _mod.check_status_transition(now + 120)
    # One success then one error is a 50% rate — degraded, not error.
    assert result == ('healthy', 'degraded')
    assert notify.called


def test_a_blip_that_recovers_never_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
  """One failed run that recovers must not produce healthy→error→healthy pages."""
  _set_alert_config(monkeypatch, alert_url='https://example.com/h', alert_confirm_seconds='60')
  _mod.init()
  _mod.register('bart')
  _mod.record_success('bart')

  now = time.time()
  _mod.check_status_transition(now)  # baseline

  with patch('healthalert.notify_status_change') as notify:
    _mod.record_error('bart', 'transient')
    _mod.check_status_transition(now + 1)
    # Recovers well inside the confirmation window.
    for _ in range(8):
      _mod.record_success('bart')
    assert _mod.check_status_transition(now + 30) is None
    assert _mod.check_status_transition(now + 300) is None
    assert not notify.called


def test_recovery_is_reported_too(monkeypatch: pytest.MonkeyPatch) -> None:
  """Recovery matters as much as failure — an alert you cannot clear is noise."""
  _set_alert_config(monkeypatch, alert_url='https://example.com/h', alert_confirm_seconds='0')
  _mod.init()
  _mod.register('bart')
  _mod.record_error('bart', 'boom')

  now = time.time()
  _mod.check_status_transition(now)  # baseline: error

  for _ in range(10):
    _mod.record_success('bart')
  with patch('healthalert.notify_status_change') as notify:
    _mod.check_status_transition(now + 1)
    result = _mod.check_status_transition(now + 2)
    assert result == ('error', 'healthy')
    assert notify.called


def test_transition_is_logged_even_when_no_endpoint_is_configured(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  """The log line is useful on its own; alerting is the optional part."""
  _set_alert_config(monkeypatch, alert_confirm_seconds='0')
  _mod.init()
  _mod.register('bart')
  _mod.record_success('bart')

  now = time.time()
  _mod.check_status_transition(now)
  _mod.record_error('bart', 'boom')

  with caplog.at_level(logging.INFO, logger='health'):
    _mod.check_status_transition(now + 1)
    result = _mod.check_status_transition(now + 2)

  assert result == ('healthy', 'degraded')
  assert 'healthy → degraded' in caplog.text


def test_invalid_confirm_seconds_falls_back_to_the_default(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  _set_alert_config(monkeypatch, alert_confirm_seconds='not-a-number')
  with caplog.at_level(logging.WARNING, logger='health'):
    assert _mod._alert_confirm_seconds() == _mod._DEFAULT_ALERT_CONFIRM_SECONDS
  assert 'alert_confirm_seconds' in caplog.text
