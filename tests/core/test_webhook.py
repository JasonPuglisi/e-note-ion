import http.client
import json
import socketserver
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest

import config as _config_mod  # noqa: E402
import health as _health_mod  # noqa: E402
import scheduler as _mod  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Test credential — plaintext used in _post(), hash stored in config for auth.
_CRED_SECRET = 'test-credential-secret'

try:
  from argon2 import PasswordHasher as _PH

  _CRED_HASH: str | None = _PH().hash(_CRED_SECRET)
except ImportError:
  _CRED_HASH = None

# Boundary used by multipart helper; must be ASCII-safe.
_BOUNDARY = 'TestBoundary1234'


def _cred_config(integration: str = 'bart') -> dict[str, Any]:
  """Return a config dict with a test named credential scoped to the given integration."""
  return {
    'webhook': {
      'credentials': {
        'test': {'secret_hash': _CRED_HASH, 'webhooks': [integration]},
      },
    },
  }


def _start_test_server() -> tuple[ThreadingHTTPServer, int]:
  """Start a webhook server on an OS-assigned port and return (server, port)."""
  handler = _mod._make_webhook_handler()
  server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
  port = server.server_address[1]
  threading.Thread(target=server.serve_forever, daemon=True).start()
  return server, port


def _stop_test_server(server: ThreadingHTTPServer) -> None:
  """Stop the serve_forever loop and close the listening socket.

  shutdown() alone leaves the listening socket open, which surfaces as a
  ResourceWarning when the server is garbage-collected; server_close()
  releases it. Always pair them in teardown.
  """
  server.shutdown()
  server.server_close()


def _post(
  port: int,
  path: str,
  body: dict[str, Any] | None = None,
  secret: str = _CRED_SECRET,
) -> tuple[int, str]:
  """POST to the test server and return (status_code, response_body)."""
  encoded = json.dumps(body or {}).encode()
  conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
  headers: dict[str, str] = {
    'Content-Type': 'application/json',
    'Content-Length': str(len(encoded)),
  }
  if secret:
    headers['X-Webhook-Secret'] = secret
  conn.request('POST', path, body=encoded, headers=headers)
  resp = conn.getresponse()
  return resp.status, resp.read().decode()


def _multipart_body(payload_json: str) -> bytes:
  """Build a minimal multipart/form-data body with a single 'payload' field."""
  return (
    f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="payload"\r\n\r\n{payload_json}\r\n--{_BOUNDARY}--\r\n'
  ).encode()


def _post_multipart(
  port: int,
  path: str,
  payload_json: str,
  secret: str = _CRED_SECRET,
) -> tuple[int, str]:
  body = _multipart_body(payload_json)
  conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
  conn.request(
    'POST',
    path,
    body=body,
    headers={
      'Content-Type': f'multipart/form-data; boundary={_BOUNDARY}',
      'Content-Length': str(len(body)),
      'X-Webhook-Secret': secret,
    },
  )
  resp = conn.getresponse()
  return resp.status, resp.read().decode()


@pytest.fixture(autouse=True)
def reset_hold_interrupt() -> Generator[None, None, None]:
  """Clear the hold interrupt event and current hold state before and after each test."""
  _mod._hold_interrupt.clear()
  with _mod._current_hold_lock:
    _mod._current_hold_supersede_tag = ''
    _mod._current_hold_priority = None
  yield
  _mod._hold_interrupt.clear()
  with _mod._current_hold_lock:
    _mod._current_hold_supersede_tag = ''
    _mod._current_hold_priority = None


# ---------------------------------------------------------------------------
# Server startup behaviour
# ---------------------------------------------------------------------------


def test_webhook_server_not_started_when_no_section(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(_config_mod, '_config', {})
  mock_sched = MagicMock()
  mock_sched.get_jobs.return_value = []
  with patch.object(_mod, '_start_webhook_server') as mock_start:
    with (
      patch.object(_mod, '_validate_startup'),
      patch('config.load_config'),
      patch.object(_mod, 'load_content'),
      patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
      patch('threading.Thread'),
      patch('health.start_periodic_log'),
      patch('health.stop_periodic_log'),
      patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
      patch('time.sleep', side_effect=KeyboardInterrupt),
    ):
      monkeypatch.setattr(sys, 'argv', ['scheduler.py'])
      _mod.main()
  mock_start.assert_not_called()


def test_webhook_server_started_when_section_present(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(_config_mod, '_config', {'webhook': {'port': '8080'}})
  mock_sched = MagicMock()
  mock_sched.get_jobs.return_value = []
  with patch.object(_mod, '_start_webhook_server') as mock_start:
    with (
      patch.object(_mod, '_validate_startup'),
      patch('config.load_config'),
      patch.object(_mod, 'load_content'),
      patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
      patch('threading.Thread'),
      patch('health.start_periodic_log'),
      patch('health.stop_periodic_log'),
      patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
      patch('time.sleep', side_effect=KeyboardInterrupt),
    ):
      monkeypatch.setattr(sys, 'argv', ['scheduler.py'])
      _mod.main()
  mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# Secret validation
# ---------------------------------------------------------------------------


def test_valid_secret_returns_200() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None  # discard — just testing auth

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post(port, '/webhook/bart')
        assert status == 200
      finally:
        _stop_test_server(server)


def test_wrong_secret_returns_401() -> None:
  server, port = _start_test_server()
  try:
    status, body = _post(port, '/webhook/bart', secret='wrong-secret')
    assert status == 401
    assert 'Unauthorized' in body
  finally:
    _stop_test_server(server)


def test_missing_secret_returns_401() -> None:
  handler = _mod._make_webhook_handler()
  server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
  port = server.server_address[1]
  threading.Thread(target=server.serve_forever, daemon=True).start()
  try:
    encoded = b'{}'
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request(
      'POST',
      '/webhook/bart',
      body=encoded,
      headers={'Content-Type': 'application/json', 'Content-Length': '2'},
      # deliberately no X-Webhook-Secret header
    )
    resp = conn.getresponse()
    assert resp.status == 401
  finally:
    _stop_test_server(server)


def test_query_param_secret_accepted() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        # Pass secret as ?secret= query param with no header
        encoded = b'{}'
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        conn.request(
          'POST',
          f'/webhook/bart?secret={_CRED_SECRET}',
          body=encoded,
          headers={'Content-Type': 'application/json', 'Content-Length': str(len(encoded))},
        )
        resp = conn.getresponse()
        assert resp.status == 200
      finally:
        _stop_test_server(server)


def test_query_param_wrong_secret_returns_401() -> None:
  server, port = _start_test_server()
  try:
    encoded = b'{}'
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request(
      'POST',
      '/webhook/bart?secret=wrong-secret',
      body=encoded,
      headers={'Content-Type': 'application/json', 'Content-Length': str(len(encoded))},
    )
    resp = conn.getresponse()
    assert resp.status == 401
  finally:
    _stop_test_server(server)


def test_header_takes_precedence_over_query_param() -> None:
  """When both are present, the header is used (and must be correct)."""
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        encoded = b'{}'
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        # Correct header + wrong query param → should pass (header wins)
        conn.request(
          'POST',
          '/webhook/bart?secret=wrong-secret',
          body=encoded,
          headers={
            'Content-Type': 'application/json',
            'Content-Length': str(len(encoded)),
            'X-Webhook-Secret': _CRED_SECRET,
          },
        )
        resp = conn.getresponse()
        assert resp.status == 200
      finally:
        _stop_test_server(server)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_unknown_integration_returns_404() -> None:
  with patch.dict(_config_mod._config, _cred_config('notareal')):
    server, port = _start_test_server()
    try:
      status, _ = _post(port, '/webhook/notareal')
      assert status == 404
    finally:
      _stop_test_server(server)


def test_bad_path_returns_404() -> None:
  # Path check fires before auth — no credential needed.
  server, port = _start_test_server()
  try:
    status, _ = _post(port, '/notwebhook/bart')
    assert status == 404
  finally:
    _stop_test_server(server)


def test_get_non_health_path_returns_404() -> None:
  # GET requests to paths other than /health return 404.
  server, port = _start_test_server()
  try:
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request('GET', '/webhook/bart', headers={'X-Webhook-Secret': _CRED_SECRET})
    resp = conn.getresponse()
    assert resp.status == 404
  finally:
    _stop_test_server(server)


def test_integration_without_handle_webhook_returns_404() -> None:
  mock_mod = MagicMock(spec=[])  # no handle_webhook attribute

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, body = _post(port, '/webhook/bart')
        assert status == 404
        assert 'does not support webhooks' in body
      finally:
        _stop_test_server(server)


# ---------------------------------------------------------------------------
# Enqueue behaviour
# ---------------------------------------------------------------------------


def test_handle_webhook_none_returns_200_no_enqueue() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart', {'event': 'pause'})
          assert status == 200
          assert 'Discarded' in body
          mock_enqueue.assert_not_called()
        finally:
          _stop_test_server(server)


def test_handle_webhook_result_enqueues_message() -> None:
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=7,
    hold=30,
    timeout=60,
    name='test.webhook',
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart', {'event': 'play'})
          assert status == 200
          assert 'Enqueued' in body
          mock_enqueue.assert_called_once_with(
            priority=7,
            data=wm.data,
            hold=30,
            timeout=60,
            name='test.webhook',
            indefinite=False,
            supersede_tag='',
            interrupt=False,
          )
        finally:
          _stop_test_server(server)


def test_enqueue_uses_default_name_when_blank() -> None:
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=5,
    hold=10,
    timeout=30,
    name='',  # blank — should default to webhook.<integration>
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)  # allow handler thread to complete
          call_kwargs = mock_enqueue.call_args.kwargs
          assert call_kwargs['name'] == 'webhook.bart'
        finally:
          _stop_test_server(server)


# ---------------------------------------------------------------------------
# Hold interrupt
# ---------------------------------------------------------------------------


def test_interrupt_false_does_not_set_event() -> None:
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=5,
    hold=10,
    timeout=30,
    interrupt=False,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)  # allow handler thread to complete
          assert not _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_true_sets_hold_interrupt_event() -> None:
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=9,
    hold=10,
    timeout=30,
    interrupt=True,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)  # allow handler thread to complete
          assert _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_only_sets_hold_interrupt_without_enqueue() -> None:
  wm = _mod.WebhookMessage(
    data={},
    priority=0,
    hold=0,
    timeout=0,
    interrupt_only=True,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert status == 200
          assert 'Interrupted' in body
          mock_enqueue.assert_not_called()
          assert _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_blocked_when_current_hold_is_high_priority() -> None:
  """Webhook interrupt should not fire when the current hold is at or above threshold."""
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=8,
    hold=10,
    timeout=30,
    interrupt=True,
    # no supersede_tag — different-source message respects priority threshold
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with _mod._current_hold_lock:
    _mod._current_hold_priority = 8  # active high-priority hold
    _mod._current_hold_supersede_tag = 'plex'  # different source

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert not _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_bypasses_threshold_when_same_supersede_tag() -> None:
  """Same-tag supersede interrupts even when the current hold is at or above threshold.

  Ensures Plex play→pause→stop transitions always cut through each other's
  indefinite holds regardless of the priority ceiling.
  """
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=8,
    hold=10,
    timeout=30,
    interrupt=True,
    supersede_tag='plex',
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with _mod._current_hold_lock:
    _mod._current_hold_priority = 8  # active high-priority hold — same source
    _mod._current_hold_supersede_tag = 'plex'

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_blocked_for_different_tag_high_priority_hold() -> None:
  """Different-tag message respects normal threshold when current hold is high priority."""
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=8,
    hold=10,
    timeout=30,
    interrupt=True,
    supersede_tag='trakt',
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with _mod._current_hold_lock:
    _mod._current_hold_priority = 8  # active high-priority hold — different source
    _mod._current_hold_supersede_tag = 'plex'

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert not _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_allowed_when_current_hold_is_low_priority() -> None:
  """Webhook interrupt fires when the current hold is below threshold."""
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=8,
    hold=10,
    timeout=30,
    interrupt=True,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with _mod._current_hold_lock:
    _mod._current_hold_priority = 7  # active low-priority hold

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_only_blocked_when_current_hold_is_high_priority() -> None:
  """interrupt_only should not fire the interrupt event when hold is at or above threshold."""
  wm = _mod.WebhookMessage(
    data={},
    priority=8,
    hold=0,
    timeout=0,
    interrupt_only=True,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with _mod._current_hold_lock:
    _mod._current_hold_priority = 8  # active high-priority hold

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert status == 200
          assert 'Interrupted' in body
          mock_enqueue.assert_not_called()
          assert not _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_interrupt_only_allowed_when_current_hold_is_low_priority() -> None:
  """interrupt_only fires the interrupt event when hold is below threshold."""
  wm = _mod.WebhookMessage(
    data={},
    priority=8,
    hold=0,
    timeout=0,
    interrupt_only=True,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with _mod._current_hold_lock:
    _mod._current_hold_priority = 7  # active low-priority hold

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert status == 200
          assert 'Interrupted' in body
          mock_enqueue.assert_not_called()
          assert _mod._hold_interrupt.is_set()
        finally:
          _stop_test_server(server)


def test_webhook_normal_indefinite_enqueues_with_indefinite_flag() -> None:
  wm = _mod.WebhookMessage(
    data={'templates': [], 'variables': {}, 'truncation': 'hard'},
    priority=8,
    hold=14400,
    timeout=30,
    indefinite=True,
    interrupt=True,
  )
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = wm

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart', {'event': 'play'})
          time.sleep(0.05)
          assert status == 200
          assert 'Enqueued' in body
          mock_enqueue.assert_called_once()
          call_kwargs = mock_enqueue.call_args.kwargs
          assert call_kwargs['indefinite'] is True
        finally:
          _stop_test_server(server)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_malformed_json_returns_400() -> None:
  with patch.dict(_config_mod._config, _cred_config('bart')):
    server, port = _start_test_server()
    try:
      conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
      bad_body = b'not json{'
      conn.request(
        'POST',
        '/webhook/bart',
        body=bad_body,
        headers={
          'Content-Type': 'application/json',
          'Content-Length': str(len(bad_body)),
          'X-Webhook-Secret': _CRED_SECRET,
        },
      )
      resp = conn.getresponse()
      assert resp.status == 400
    finally:
      _stop_test_server(server)


def test_handle_webhook_exception_returns_500_server_survives() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.side_effect = RuntimeError('boom')

  with patch.dict(_config_mod._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post(port, '/webhook/bart')
        assert status == 500
        # Server should still be alive after the error.
        status2, _ = _post(port, '/webhook/bart')
        assert status2 == 500  # still responding (integration still throws)
      finally:
        _stop_test_server(server)


# ---------------------------------------------------------------------------
# Per-integration credential auto-generation
# ---------------------------------------------------------------------------


def test_credential_autogenerated_when_absent(
  tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  config_file = tmp_path / 'config.toml'
  config_file.write_text('[webhook]\nport = 8080\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_config_mod, '_config', {'webhook': {'port': '8080'}})

  with patch('scheduler.ThreadingHTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  assert 'auto-generated' in caplog.text.lower()
  creds = _config_mod._config.get('webhook', {}).get('credentials', {})
  assert creds  # at least one credential was created


def test_existing_credential_not_regenerated(
  tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  existing_hash = '$argon2id$v=19$test$placeholder'
  config_file = tmp_path / 'config.toml'
  config_file.write_text(
    f'[webhook]\nport = 8080\n\n[webhook.credentials.plex]\nsecret_hash = "{existing_hash}"\nwebhooks = ["plex"]\n'
  )
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'webhook': {
        'port': '8080',
        'credentials': {'plex': {'secret_hash': existing_hash, 'webhooks': ['plex']}},
      }
    },
  )

  with patch('scheduler.ThreadingHTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  # plex should not be regenerated.
  assert "auto-generated for 'plex'" not in caplog.text
  assert _config_mod._config['webhook']['credentials']['plex']['secret_hash'] == existing_hash


def test_message_admin_autogenerated_even_when_friends_present(
  tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """message.admin is auto-generated even if friend credentials already exist."""
  config_file = tmp_path / 'config.toml'
  config_file.write_text('[webhook]\nport = 8080\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'webhook': {
        'port': '8080',
        'credentials': {
          'message': {
            'friend': {
              'alice': {'secret_hash': '$argon2id$alice', 'webhooks': ['message']},
            },
          },
        },
      }
    },
  )

  with patch('scheduler.ThreadingHTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  assert "auto-generated for 'message'" in caplog.text
  assert 'admin' in _config_mod.get_credentials('message')


def test_old_flat_message_credentials_auto_migrated_on_startup(
  tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """Old-style flat webhook.credentials.* message entries are migrated to the nested namespace on startup."""
  config_file = tmp_path / 'config.toml'
  config_file.write_text(
    '[webhook]\nport = 8080\n\n'
    '[webhook.credentials.message-admin]\nsecret_hash = "$argon2id$admin"\nwebhooks = ["message"]\n\n'
    '[webhook.credentials.alice]\nsecret_hash = "$argon2id$alice"\nwebhooks = ["message"]\n'
  )
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'webhook': {
        'port': 8080,
        'credentials': {
          'message-admin': {'secret_hash': '$argon2id$admin', 'webhooks': ['message']},
          'alice': {'secret_hash': '$argon2id$alice', 'webhooks': ['message']},
        },
      }
    },
  )

  with patch('scheduler.ThreadingHTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  assert 'Auto-migrated 2 message credential(s)' in caplog.text
  text = config_file.read_text()
  assert '[webhook.credentials.message.admin]' in text
  assert '[webhook.credentials.message.friend.alice]' in text
  assert '[webhook.credentials.message-admin]' not in text
  assert '[webhook.credentials.alice]' not in text
  assert '\n\n\n' not in text


# ---------------------------------------------------------------------------
# Multipart body parsing (Plex sends multipart/form-data, not raw JSON)
# ---------------------------------------------------------------------------


def test_multipart_payload_field_is_parsed_as_json() -> None:
  """Plex-style multipart/form-data body is unwrapped and dispatched correctly."""
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_config_mod._config, _cred_config('plex')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post_multipart(port, '/webhook/plex', '{"event": "media.play"}')
        assert status == 200
        mock_mod.handle_webhook.assert_called_once()
        call_args = mock_mod.handle_webhook.call_args
        assert call_args.args[0].get('event') == 'media.play'
      finally:
        _stop_test_server(server)


def test_multipart_missing_payload_field_returns_400() -> None:
  body = f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="other"\r\n\r\nvalue\r\n--{_BOUNDARY}--\r\n'.encode()

  with patch.dict(_config_mod._config, _cred_config('plex')):
    server, port = _start_test_server()
    try:
      conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
      conn.request(
        'POST',
        '/webhook/plex',
        body=body,
        headers={
          'Content-Type': f'multipart/form-data; boundary={_BOUNDARY}',
          'Content-Length': str(len(body)),
          'X-Webhook-Secret': _CRED_SECRET,
        },
      )
      resp = conn.getresponse()
      assert resp.status == 400
    finally:
      _stop_test_server(server)


def test_multipart_invalid_json_in_payload_returns_400() -> None:
  with patch.dict(_config_mod._config, _cred_config('plex')):
    server, port = _start_test_server()
    try:
      status, _ = _post_multipart(port, '/webhook/plex', 'not json{')
      assert status == 400
    finally:
      _stop_test_server(server)


# ---------------------------------------------------------------------------
# Named credential authentication
# ---------------------------------------------------------------------------


def test_named_credential_authenticates_scoped_endpoint() -> None:
  """A valid credential scoped to the integration authenticates successfully."""
  from argon2 import PasswordHasher

  ph = PasswordHasher()
  friend_secret = 'apple-river-bench'
  friend_hash = ph.hash(friend_secret)

  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(
    'config._config',
    {'webhook': {'credentials': {'alice': {'secret_hash': friend_hash, 'webhooks': ['message']}}}},
  ):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post(port, '/webhook/message', secret=friend_secret)
        assert status == 200
      finally:
        _stop_test_server(server)


def test_named_credential_rejected_for_wrong_endpoint() -> None:
  """A credential scoped to 'message' cannot authenticate '/webhook/notion'."""
  from argon2 import PasswordHasher

  ph = PasswordHasher()
  friend_secret = 'apple-river-bench'
  friend_hash = ph.hash(friend_secret)

  with patch.dict(
    'config._config',
    {'webhook': {'credentials': {'alice': {'secret_hash': friend_hash, 'webhooks': ['message']}}}},
  ):
    server, port = _start_test_server()
    try:
      status, _ = _post(port, '/webhook/notion', secret=friend_secret)
      assert status == 401
    finally:
      _stop_test_server(server)


def test_unknown_credential_secret_returns_401() -> None:
  """A secret that doesn't match any named credential is rejected."""
  from argon2 import PasswordHasher

  ph = PasswordHasher()
  real_hash = ph.hash('apple-river-bench')

  with patch.dict(
    'config._config',
    {'webhook': {'credentials': {'alice': {'secret_hash': real_hash, 'webhooks': ['message']}}}},
  ):
    server, port = _start_test_server()
    try:
      status, _ = _post(port, '/webhook/message', secret='wrong-secret')
      assert status == 401
    finally:
      _stop_test_server(server)


def test_credential_name_passed_to_handle_webhook() -> None:
  """When a named credential matches, credential_name is passed to handle_webhook."""
  from argon2 import PasswordHasher

  ph = PasswordHasher()
  friend_secret = 'apple-river-bench'
  friend_hash = ph.hash(friend_secret)

  import integrations.message as _msg_mod

  with patch.dict(
    'config._config',
    {'webhook': {'credentials': {'alice': {'secret_hash': friend_hash, 'webhooks': ['message']}}}},
  ):
    with patch.object(_msg_mod, 'handle_webhook', autospec=True, return_value=None) as mock_hw:
      with patch.dict(_mod._integrations, {'message': _msg_mod}):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/message', secret=friend_secret)
          time.sleep(0.05)
          mock_hw.assert_called_once()
          _, kwargs = mock_hw.call_args
          assert kwargs.get('credential_name') == 'alice'
        finally:
          _stop_test_server(server)


# ---------------------------------------------------------------------------
# GET /health endpoint
# ---------------------------------------------------------------------------


def _get(
  port: int,
  path: str,
  secret: str = _CRED_SECRET,
) -> tuple[int, str]:
  """GET from the test server and return (status_code, response_body)."""
  conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
  headers: dict[str, str] = {}
  if secret:
    headers['X-Webhook-Secret'] = secret
  conn.request('GET', path, headers=headers)
  resp = conn.getresponse()
  return resp.status, resp.read().decode()


def _head(
  port: int,
  path: str,
  secret: str = _CRED_SECRET,
) -> tuple[int, dict[str, str]]:
  """HEAD to the test server and return (status_code, response_headers)."""
  conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
  headers: dict[str, str] = {}
  if secret:
    headers['X-Webhook-Secret'] = secret
  conn.request('HEAD', path, headers=headers)
  resp = conn.getresponse()
  resp_headers = {k.lower(): v for k, v in resp.getheaders()}
  body = resp.read()
  assert body == b'', f'HEAD response must have no body, got {body!r}'
  return resp.status, resp_headers


def _health_cred_config() -> dict[str, Any]:
  """Config with a test credential scoped to health."""
  return {
    'webhook': {
      'credentials': {
        'test': {'secret_hash': _CRED_HASH, 'webhooks': ['health']},
      },
    },
  }


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_health_endpoint_returns_200_when_healthy() -> None:
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('weather')
  _health_mod.record_success('weather')

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, body = _get(port, '/health')
      assert status == 200
      data = json.loads(body)
      assert data['status'] == 'healthy'
      assert 'weather' in data['integrations']
      assert data['integrations']['weather']['status'] == 'healthy'
      # Vestaboard target is reported under its own top-level key, not under
      # integrations. It starts UNKNOWN on a fresh reset — does not drive
      # top-level status.
      assert 'vestaboard' in data
      assert data['vestaboard'] is not None
      assert 'vestaboard' not in data['integrations']
    finally:
      _stop_test_server(server)
      _health_mod.reset()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_health_endpoint_returns_503_when_vestaboard_errored() -> None:
  """A vestaboard send failure drives the endpoint to 503 even with healthy integrations."""
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('weather')
  _health_mod.record_success('weather')
  _health_mod.record_error(_health_mod.VESTABOARD_TARGET, 'HTTP 500')

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, body = _get(port, '/health')
      assert status == 503
      data = json.loads(body)
      assert data['status'] == 'error'
      assert data['vestaboard']['status'] == 'error'
      assert data['integrations']['weather']['status'] == 'healthy'
    finally:
      _stop_test_server(server)
      _health_mod.reset()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_health_endpoint_returns_503_when_unhealthy() -> None:
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('bart')
  _health_mod.record_error('bart', 'API down')

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, body = _get(port, '/health')
      assert status == 503
      data = json.loads(body)
      assert data['status'] == 'error'
    finally:
      _stop_test_server(server)
      _health_mod.reset()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_health_endpoint_401_without_secret() -> None:
  _health_mod.reset()
  _health_mod.init()

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, _ = _get(port, '/health', secret='')
      assert status == 401
    finally:
      _stop_test_server(server)
      _health_mod.reset()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_health_endpoint_401_wrong_secret() -> None:
  _health_mod.reset()
  _health_mod.init()

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, _ = _get(port, '/health', secret='wrong-secret')
      assert status == 401
    finally:
      _stop_test_server(server)
      _health_mod.reset()


def test_health_endpoint_404_wrong_path() -> None:
  with patch.dict('config._config', {}):
    server, port = _start_test_server()
    try:
      status, _ = _get(port, '/notfound', secret='')
      assert status == 404
    finally:
      _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_health_endpoint_query_param_auth() -> None:
  _health_mod.reset()
  _health_mod.init()

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, body = _get(port, f'/health?secret={_CRED_SECRET}', secret='')
      assert status == 200
      data = json.loads(body)
      assert data['status'] == 'healthy'
    finally:
      _stop_test_server(server)
      _health_mod.reset()


# ---------------------------------------------------------------------------
# HEAD /health
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_head_health_returns_200_no_body() -> None:
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('weather')
  _health_mod.record_success('weather')

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, headers = _head(port, '/health')
      assert status == 200
      assert headers['content-type'] == 'application/json'
      assert int(headers['content-length']) > 0
    finally:
      _stop_test_server(server)
      _health_mod.reset()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_head_health_returns_503_when_unhealthy() -> None:
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('bart')
  _health_mod.record_error('bart', 'API down')

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, _ = _head(port, '/health')
      assert status == 503
    finally:
      _stop_test_server(server)
      _health_mod.reset()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_head_health_401_without_secret() -> None:
  _health_mod.reset()
  _health_mod.init()

  with patch.dict('config._config', _health_cred_config()):
    server, port = _start_test_server()
    try:
      status, _ = _head(port, '/health', secret='')
      assert status == 401
    finally:
      _stop_test_server(server)
      _health_mod.reset()


# ---------------------------------------------------------------------------
# GET /state endpoint
# ---------------------------------------------------------------------------


def _state_cred_config() -> dict[str, Any]:
  """Config with a test credential scoped to the state endpoint."""
  return {
    'webhook': {
      'credentials': {
        'test': {'secret_hash': _CRED_HASH, 'webhooks': ['state']},
      },
    },
  }


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_endpoint_returns_modes() -> None:
  """/state always reports the current quiet/public toggle state."""
  with patch.dict('config._config', _state_cred_config()):
    with (
      patch.object(_mod._quiet_mod, 'is_quiet', return_value=False),
      patch.object(_mod._public_mod, 'is_public', return_value=True),
      patch.object(_mod._vb, 'get_cached_grid', return_value=(None, 0.0)),
    ):
      server, port = _start_test_server()
      try:
        status, body = _get(port, '/state')
        assert status == 200
        data = json.loads(body)
        assert data['modes'] == {'quiet': False, 'public': True}
        assert data['source'] == 'empty'
        assert data['grid'] is None
        assert data['rendered'] is None
        assert data['timestamp'] is None
      finally:
        _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_source_board_from_cache() -> None:
  """With cached content and no quiet mode, /state serves the cached grid."""
  grid = [[8, 0, 0], [0, 63, 0]]  # 'H', blank, blank / blank, red, blank
  with patch.dict('config._config', _state_cred_config()):
    with (
      patch.object(_mod._quiet_mod, 'is_quiet', return_value=False),
      patch.object(_mod._public_mod, 'is_public', return_value=False),
      patch.object(_mod._vb, 'get_cached_grid', return_value=(grid, 1_700_000_000.0)),
    ):
      server, port = _start_test_server()
      try:
        status, body = _get(port, '/state')
        assert status == 200
        data = json.loads(body)
        assert data['source'] == 'board'
        assert data['grid'] == grid
        assert data['rendered'] is not None
        assert data['timestamp'] is not None
      finally:
        _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_source_virtual_when_quiet() -> None:
  """In quiet mode, /state returns the virtual state (what wakes on the board)."""
  virtual = [[8, 9, 0], [0, 0, 0]]
  with patch.dict('config._config', _state_cred_config()):
    with (
      patch.object(_mod._quiet_mod, 'is_quiet', return_value=True),
      patch.object(_mod._quiet_mod, 'get_virtual_state', return_value=virtual),
      patch.object(_mod._public_mod, 'is_public', return_value=False),
    ):
      server, port = _start_test_server()
      try:
        status, body = _get(port, '/state')
        assert status == 200
        data = json.loads(body)
        assert data['modes']['quiet'] is True
        assert data['source'] == 'virtual'
        assert data['grid'] == virtual
      finally:
        _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_refresh_calls_get_state() -> None:
  """?refresh=true fetches authoritative board state via the Vestaboard API."""
  fetched = [[1, 2, 3], [4, 5, 6]]
  fake_state = MagicMock()
  fake_state.layout = fetched
  with patch.dict('config._config', _state_cred_config()):
    with (
      patch.object(_mod._quiet_mod, 'is_quiet', return_value=False),
      patch.object(_mod._public_mod, 'is_public', return_value=False),
      patch.object(_mod._vb, 'get_state', return_value=fake_state) as mock_get,
      patch.object(_mod._vb, 'get_cached_grid', return_value=(None, 0.0)),
    ):
      server, port = _start_test_server()
      try:
        status, body = _get(port, '/state?refresh=true')
        assert status == 200
        data = json.loads(body)
        assert data['source'] == 'board'
        assert data['grid'] == fetched
        mock_get.assert_called_once()
      finally:
        _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_refresh_error_falls_back_to_cache() -> None:
  """A failed refresh fetch falls back to the cache and still returns 200 + modes."""
  cached = [[7, 7, 7], [0, 0, 0]]
  with patch.dict('config._config', _state_cred_config()):
    with (
      patch.object(_mod._quiet_mod, 'is_quiet', return_value=False),
      patch.object(_mod._public_mod, 'is_public', return_value=False),
      patch.object(_mod._vb, 'get_state', side_effect=RuntimeError('API down')),
      patch.object(_mod._vb, 'get_cached_grid', return_value=(cached, 1_700_000_000.0)),
    ):
      server, port = _start_test_server()
      try:
        status, body = _get(port, '/state?refresh=true')
        assert status == 200
        data = json.loads(body)
        assert data['source'] == 'board'
        assert data['grid'] == cached
        assert data['refresh_error'] is True
      finally:
        _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_endpoint_401_without_secret() -> None:
  with patch.dict('config._config', _state_cred_config()):
    server, port = _start_test_server()
    try:
      status, _ = _get(port, '/state', secret='')
      assert status == 401
    finally:
      _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_endpoint_401_wrong_secret() -> None:
  with patch.dict('config._config', _state_cred_config()):
    server, port = _start_test_server()
    try:
      status, _ = _get(port, '/state', secret='wrong-secret')
      assert status == 401
    finally:
      _stop_test_server(server)


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_state_endpoint_query_param_auth() -> None:
  with patch.dict('config._config', _state_cred_config()):
    with (
      patch.object(_mod._quiet_mod, 'is_quiet', return_value=False),
      patch.object(_mod._public_mod, 'is_public', return_value=False),
      patch.object(_mod._vb, 'get_cached_grid', return_value=(None, 0.0)),
    ):
      server, port = _start_test_server()
      try:
        status, body = _get(port, f'/state?secret={_CRED_SECRET}', secret='')
        assert status == 200
        assert json.loads(body)['modes'] == {'quiet': False, 'public': False}
      finally:
        _stop_test_server(server)


# ---------------------------------------------------------------------------
# Listener hardening (#590)
# ---------------------------------------------------------------------------


def test_handler_sets_a_socket_timeout() -> None:
  """A stalled connection must not hold its handler thread forever."""
  handler = _mod._make_webhook_handler()
  assert handler.timeout == _mod._WEBHOOK_SOCKET_TIMEOUT
  assert handler.timeout > 0


def test_server_is_threaded() -> None:
  """Serial handling is what let one connection wedge the whole listener.

  Asserted against the module attribute rather than by starting a server, so
  this stays a statement about what ships rather than about test plumbing.
  """
  import http.server

  assert _mod.ThreadingHTTPServer is http.server.ThreadingHTTPServer
  assert issubclass(_mod.ThreadingHTTPServer, socketserver.ThreadingMixIn)
  assert _mod.ThreadingHTTPServer.daemon_threads is True


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_a_stalled_connection_does_not_block_other_requests(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """The core of #590.

  Open a connection, send a Content-Length that promises a body, then never
  send it. Against the single-threaded HTTPServer this parks the one and only
  handler loop inside rfile.read() and every subsequent request — webhooks,
  /health, /state — hangs until the socket dies. This test hangs on main.
  """
  import socket

  monkeypatch.setattr(_config_mod, '_config', _cred_config('bart'))
  server, port = _start_test_server()
  try:
    stalled = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
      stalled.sendall(
        b'POST /webhook/bart HTTP/1.1\r\n'
        b'Host: 127.0.0.1\r\n'
        b'X-Webhook-Secret: ' + _CRED_SECRET.encode() + b'\r\n'
        b'Content-Type: application/json\r\n'
        b'Content-Length: 5000\r\n'
        b'\r\n'
        b'{'  # promised 5000 bytes, sending 1, then stalling
      )

      # A normal request must still be served promptly.
      started = time.monotonic()
      status, _ = _post(port, '/webhook/bart', {'ok': True})
      elapsed = time.monotonic() - started

      assert status in (200, 204, 400, 404), f'unexpected status {status}'
      assert elapsed < 5, f'second request waited {elapsed:.1f}s behind a stalled connection'
    finally:
      stalled.close()
  finally:
    _stop_test_server(server)


def test_empty_secret_is_rejected_without_touching_argon2(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Unauthenticated probes overwhelmingly send no secret at all.

  Each argon2id verification costs ~64 MiB and tens of milliseconds, and runs
  once per configured credential. Refusing an empty secret up front makes the
  common probe free instead of the most expensive request the server serves.
  """
  monkeypatch.setattr(_config_mod, '_config', _cred_config('bart'))
  before = _mod._auth_semaphore._value  # type: ignore[attr-defined]

  assert _mod._authenticate_webhook('', 'bart') is None

  # Never acquired a slot, so never ran a verification.
  assert _mod._auth_semaphore._value == before  # type: ignore[attr-defined]


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_auth_sheds_load_when_no_slot_frees_up(monkeypatch: pytest.MonkeyPatch) -> None:
  """With every slot held, a further request is shed rather than queued.

  Bounds both the memory ceiling (concurrency * 64 MiB) and the queue an
  attacker can build up.
  """
  monkeypatch.setattr(_config_mod, '_config', _cred_config('bart'))
  monkeypatch.setattr(_mod, '_AUTH_WAIT_SECONDS', 0.05)

  held = [_mod._auth_semaphore.acquire(timeout=1) for _ in range(_mod._AUTH_CONCURRENCY)]
  try:
    assert all(held)
    with pytest.raises(_mod._AuthCapacityError):
      _mod._authenticate_webhook('anything', 'bart')
  finally:
    for ok in held:
      if ok:
        _mod._auth_semaphore.release()


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_auth_releases_its_slot_on_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
  """A leaked slot would wedge authentication permanently after a few requests."""
  monkeypatch.setattr(_config_mod, '_config', _cred_config('bart'))
  baseline = _mod._auth_semaphore._value  # type: ignore[attr-defined]

  assert _mod._authenticate_webhook(_CRED_SECRET, 'bart') == 'test'  # success path
  assert _mod._auth_semaphore._value == baseline  # type: ignore[attr-defined]

  assert _mod._authenticate_webhook('wrong-secret', 'bart') is None  # mismatch path
  assert _mod._auth_semaphore._value == baseline  # type: ignore[attr-defined]


@pytest.mark.skipif(_CRED_HASH is None, reason='argon2-cffi not installed')
def test_shed_request_returns_503_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
  """Load shedding is a 503, not an unhandled exception."""
  monkeypatch.setattr(_config_mod, '_config', _cred_config('bart'))
  server, port = _start_test_server()
  try:
    with patch.object(_mod, '_authenticate_webhook', side_effect=_mod._AuthCapacityError):
      status, body = _post(port, '/webhook/bart', {'ok': True})
    assert status == 503
    assert 'busy' in body.lower()
  finally:
    _stop_test_server(server)
