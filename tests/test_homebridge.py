from unittest.mock import MagicMock, patch

import config as _config_mod
import homebridge as _mod

# ---------------------------------------------------------------------------
# notify_mode_change — config gating + dispatch
# ---------------------------------------------------------------------------


def test_notify_noop_when_no_section() -> None:
  """No [homebridge] section → nothing is dispatched."""
  with patch.object(_config_mod, '_config', {}):
    with patch.object(_mod, '_dispatch') as mock_dispatch:
      _mod.notify_mode_change('quiet', True)
      mock_dispatch.assert_not_called()


def test_notify_noop_when_no_url() -> None:
  """[homebridge] present but without a url → nothing is dispatched."""
  with patch.object(_config_mod, '_config', {'homebridge': {'secret': 's'}}):
    with patch.object(_mod, '_dispatch') as mock_dispatch:
      _mod.notify_mode_change('public', False)
      mock_dispatch.assert_not_called()


def test_notify_dispatches_when_configured() -> None:
  with patch.object(_config_mod, '_config', {'homebridge': {'url': 'http://hb:51828/x', 'secret': 'sec'}}):
    with patch.object(_mod, '_dispatch') as mock_dispatch:
      _mod.notify_mode_change('quiet', True)
      mock_dispatch.assert_called_once_with('http://hb:51828/x', 'sec', 'quiet', True)


def test_notify_dispatches_without_secret() -> None:
  with patch.object(_config_mod, '_config', {'homebridge': {'url': 'http://hb:51828/x'}}):
    with patch.object(_mod, '_dispatch') as mock_dispatch:
      _mod.notify_mode_change('public', True)
      mock_dispatch.assert_called_once_with('http://hb:51828/x', '', 'public', True)


# ---------------------------------------------------------------------------
# _send — outbound HTTP behaviour (runs synchronously in tests)
# ---------------------------------------------------------------------------


def test_send_posts_payload_with_secret_header() -> None:
  with patch('integrations.http.fetch_with_retry') as mock_fetch:
    mock_fetch.return_value = MagicMock(status_code=200)
    _mod._send('http://hb/x', 'shh', 'quiet', True)
    mock_fetch.assert_called_once()
    args, kwargs = mock_fetch.call_args
    assert args[0] == 'POST'
    assert args[1] == 'http://hb/x'
    assert kwargs['json'] == {'characteristic': 'quiet', 'value': True}
    assert kwargs['headers']['X-Webhook-Secret'] == 'shh'
    assert kwargs['timeout'] == _mod._TIMEOUT


def test_send_omits_secret_header_when_absent() -> None:
  with patch('integrations.http.fetch_with_retry') as mock_fetch:
    mock_fetch.return_value = MagicMock(status_code=200)
    _mod._send('http://hb/x', '', 'public', False)
    _, kwargs = mock_fetch.call_args
    assert 'X-Webhook-Secret' not in kwargs['headers']


def test_send_swallows_exception() -> None:
  """A transport error must never propagate (toggling must not break)."""
  with patch('integrations.http.fetch_with_retry', side_effect=RuntimeError('boom')):
    _mod._send('http://hb/x', 'shh', 'quiet', True)  # must not raise


def test_send_warns_on_error_status() -> None:
  import logging

  with patch('integrations.http.fetch_with_retry') as mock_fetch:
    mock_fetch.return_value = MagicMock(status_code=401)
    with patch.object(_mod.logger, 'warning') as mock_warn:
      _mod._send('http://hb/x', 'shh', 'quiet', True)
      mock_warn.assert_called_once()
      assert logging  # keep import used


def test_send_never_logs_secret(caplog) -> None:  # type: ignore[no-untyped-def]
  secret = 'super-secret-value-123'
  with patch('integrations.http.fetch_with_retry', side_effect=RuntimeError('network down')):
    with caplog.at_level('DEBUG'):
      _mod._send('http://hb/x', secret, 'quiet', True)
  assert secret not in caplog.text


def test_dispatch_starts_daemon_thread() -> None:
  with patch('threading.Thread') as mock_thread:
    instance = MagicMock()
    mock_thread.return_value = instance
    _mod._dispatch('http://hb/x', 'sec', 'quiet', True)
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs['daemon'] is True
    instance.start.assert_called_once()
