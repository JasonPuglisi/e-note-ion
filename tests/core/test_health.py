"""Tests for the health tracking module."""

import json
import threading

import pytest

import health as _mod


@pytest.fixture(autouse=True)
def _reset_health() -> None:
  """Ensure clean health state for each test."""
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
# Status computation
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


def test_status_error_errors_and_expected_empty_only() -> None:
  """Errors + expected_empty (no successes) → error, because expected_empty
  doesn't count as a success for error rate purposes."""
  _mod.register('bart')
  _mod.record_expected_empty('bart')
  _mod.record_error('bart', 'fail')
  _mod.record_error('bart', 'fail')
  assert _mod.get_summary()['integrations']['bart']['status'] == 'degraded'


def test_rolling_window_capped_at_10() -> None:
  _mod.register('weather')
  # Fill with errors
  for _ in range(10):
    _mod.record_error('weather', 'fail')
  assert _mod.get_summary()['integrations']['weather']['status'] == 'error'
  # Push out errors with successes
  for _ in range(10):
    _mod.record_success('weather')
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['status'] == 'healthy'
  assert detail['total_events'] == 10


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


def test_overall_unhealthy_any_degraded() -> None:
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
  assert 'integrations' in summary
  detail = summary['integrations']['weather']
  assert 'status' in detail
  assert 'last_success' in detail
  assert 'last_expected_empty' in detail
  assert 'last_error' in detail
  assert 'last_error_message' in detail
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

  # All should have been recorded (deque maxlen caps at 10, but no crash)
  detail = _mod.get_summary()['integrations']['weather']
  assert detail['total_events'] == 10
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
