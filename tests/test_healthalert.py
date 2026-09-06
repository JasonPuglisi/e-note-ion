from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import config as _config_mod
import healthalert as _mod


def _configure(monkeypatch: pytest.MonkeyPatch, **health: Any) -> None:
  monkeypatch.setattr(_config_mod, '_config', {'health': health} if health else {})


def test_unconfigured_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
  """An absent [health] section must not attempt any network call."""
  _configure(monkeypatch)
  with patch('threading.Thread') as thread:
    _mod.notify_status_change('healthy', 'error', {})
  assert not thread.called
  assert _mod.is_configured() is False


def test_section_without_a_url_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
  _configure(monkeypatch, alert_confirm_seconds='60')
  with patch('threading.Thread') as thread:
    _mod.notify_status_change('healthy', 'error', {})
  assert not thread.called
  assert _mod.is_configured() is False


def test_payload_carries_only_unhealthy_targets(monkeypatch: pytest.MonkeyPatch) -> None:
  """A chat notification should stay readable regardless of integration count."""
  _configure(monkeypatch, alert_url='https://example.com/hook')
  summary = {
    'uptime_seconds': 99,
    'integrations': {
      'bart': {'status': 'error'},
      'ynab': {'status': 'healthy'},
      'plex': {'status': 'unknown'},
      'trakt': {'status': 'overdue'},
    },
  }
  fake = MagicMock(status_code=200)
  with patch('integrations.http.fetch_with_retry', return_value=fake) as send:
    _mod._send('https://example.com/hook', '', 'healthy', 'error', summary)

  payload = send.call_args.kwargs['json']
  assert payload['previous'] == 'healthy'
  assert payload['current'] == 'error'
  assert sorted(payload['unhealthy']) == ['bart', 'trakt']


def test_secret_is_sent_as_a_header_and_never_logged(
  monkeypatch: pytest.MonkeyPatch,
  caplog: pytest.LogCaptureFixture,
) -> None:
  _configure(monkeypatch, alert_url='https://example.com/hook', alert_secret='top-secret-value')
  fake = MagicMock(status_code=200)
  with caplog.at_level('DEBUG'):
    with patch('integrations.http.fetch_with_retry', return_value=fake) as send:
      _mod._send('https://example.com/hook', 'top-secret-value', 'healthy', 'error', {})

  assert send.call_args.kwargs['headers']['X-Webhook-Secret'] == 'top-secret-value'
  assert 'top-secret-value' not in caplog.text


def test_transport_failure_is_swallowed_and_redacted(caplog: pytest.LogCaptureFixture) -> None:
  """An alerting outage must never affect the scheduler, and must not leak the URL."""
  boom = Exception('failed posting to https://example.com/hook?token=SECRETTOKEN')
  with caplog.at_level('WARNING'):
    with patch('integrations.http.fetch_with_retry', side_effect=boom):
      _mod._send('https://example.com/hook?token=SECRETTOKEN', '', 'healthy', 'error', {})

  assert 'SECRETTOKEN' not in caplog.text
  assert 'Health alert failed' in caplog.text


def test_http_error_status_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
  fake = MagicMock(status_code=500)
  with caplog.at_level('WARNING'):
    with patch('integrations.http.fetch_with_retry', return_value=fake):
      _mod._send('https://example.com/hook', '', 'healthy', 'error', {})
  assert 'HTTP 500' in caplog.text
