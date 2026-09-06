import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import scheduler as _mod


def _run_main_with_log_level(
  monkeypatch: pytest.MonkeyPatch,
  config: dict[str, Any],
) -> None:
  """Run main() with the given config, patching away all side effects."""
  import config as _config_mod

  monkeypatch.setattr('sys.argv', ['e-note-ion'])
  monkeypatch.setattr(_config_mod, '_config', config)
  mock_sched = MagicMock()
  mock_sched.get_jobs.return_value = []
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
    patch('health.start_periodic_log'),
    patch('health.start_status_watch'),
    patch('health.stop_periodic_log'),
    patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
    patch('time.sleep', side_effect=KeyboardInterrupt),
  ):
    _mod.main()


def test_log_level_debug_sets_root_level(monkeypatch: pytest.MonkeyPatch) -> None:
  _run_main_with_log_level(monkeypatch, {'scheduler': {'log_level': 'DEBUG'}})
  assert logging.root.level == logging.DEBUG


def test_log_level_warning_sets_root_level(monkeypatch: pytest.MonkeyPatch) -> None:
  _run_main_with_log_level(monkeypatch, {'scheduler': {'log_level': 'WARNING'}})
  assert logging.root.level == logging.WARNING


def test_log_level_default_is_info(monkeypatch: pytest.MonkeyPatch) -> None:
  _run_main_with_log_level(monkeypatch, {})
  assert logging.root.level == logging.INFO


def test_log_level_invalid_defaults_to_info(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
  _run_main_with_log_level(monkeypatch, {'scheduler': {'log_level': 'VERBOSE'}})
  assert logging.root.level == logging.INFO
  assert 'VERBOSE' in caplog.text


# --- caldav event-data diff is never logged (follow-up to #598) ---


def test_ical_diff_is_dropped_at_every_level() -> None:
  """caldav prints a unified diff of real event data to justify normalising
  whitespace. That is personal data, so it is filtered regardless of log_level.

  The first fix only silenced it above DEBUG. Production runs at DEBUG, so the
  escape hatch put the calendar contents straight back into the Docker log.
  """
  import io
  import logging as _logging

  import scheduler as _sched

  for level in (_logging.DEBUG, _logging.INFO, _logging.WARNING):
    buf = io.StringIO()
    handler = _logging.StreamHandler(buf)
    handler.addFilter(_sched._DropIcalDiff())
    handler.setLevel(_logging.DEBUG)

    logger = _logging.getLogger(f'caldav.test.{level}')
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(_logging.DEBUG)

    logger.warning('Ical data was modified to avoid compatibility issues\n-DESCRIPTION:Instructor: xixi')
    assert 'DESCRIPTION' not in buf.getvalue(), f'event data leaked at level {level}'


def test_other_caldav_warnings_are_kept() -> None:
  """Silencing the diff must not silence caldav entirely — real warnings matter."""
  import io
  import logging as _logging

  import scheduler as _sched

  buf = io.StringIO()
  handler = _logging.StreamHandler(buf)
  handler.addFilter(_sched._DropIcalDiff())
  logger = _logging.getLogger('caldav.test.keep')
  logger.handlers = [handler]
  logger.propagate = False
  logger.setLevel(_logging.DEBUG)

  logger.warning('could not reach the CalDAV server')
  assert 'could not reach' in buf.getvalue()


def test_caldav_is_no_longer_blanket_silenced() -> None:
  """The diff is handled by a filter now, so caldav keeps its normal level."""
  import scheduler as _sched

  assert 'caldav' not in _sched._THIRD_PARTY_LOG_FLOORS
  assert 'quic' in _sched._THIRD_PARTY_LOG_FLOORS
