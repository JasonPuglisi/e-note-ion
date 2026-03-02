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
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', config)
  mock_sched = MagicMock()
  mock_sched.get_jobs.return_value = []
  with (
    patch.object(_mod, '_validate_startup'),
    patch('config.load_config'),
    patch.object(_mod, 'load_content'),
    patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
    patch('threading.Thread'),
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
