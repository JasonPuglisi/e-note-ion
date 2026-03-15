import http.client
import json
import sys
import threading
import time
from http.server import HTTPServer
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest

import config as _cfg  # noqa: E402
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


def _start_test_server() -> tuple[HTTPServer, int]:
  """Start a webhook server on an OS-assigned port and return (server, port)."""
  handler = _mod._make_webhook_handler()
  server = HTTPServer(('127.0.0.1', 0), handler)
  port = server.server_address[1]
  threading.Thread(target=server.serve_forever, daemon=True).start()
  return server, port


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
  monkeypatch.setattr(_cfg, '_config', {})
  mock_sched = MagicMock()
  mock_sched.get_jobs.return_value = []
  with patch.object(_mod, '_start_webhook_server') as mock_start:
    with (
      patch.object(_mod, '_validate_startup'),
      patch('config.load_config'),
      patch.object(_mod, 'load_content'),
      patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
      patch('threading.Thread'),
      patch('apscheduler.schedulers.background.BackgroundScheduler', return_value=mock_sched),
      patch('time.sleep', side_effect=KeyboardInterrupt),
    ):
      monkeypatch.setattr(sys, 'argv', ['scheduler.py'])
      _mod.main()
  mock_start.assert_not_called()


def test_webhook_server_started_when_section_present(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(_cfg, '_config', {'webhook': {'port': '8080'}})
  mock_sched = MagicMock()
  mock_sched.get_jobs.return_value = []
  with patch.object(_mod, '_start_webhook_server') as mock_start:
    with (
      patch.object(_mod, '_validate_startup'),
      patch('config.load_config'),
      patch.object(_mod, 'load_content'),
      patch('integrations.vestaboard.get_state', return_value=MagicMock(__str__=lambda s: '')),
      patch('threading.Thread'),
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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post(port, '/webhook/bart')
        assert status == 200
      finally:
        server.shutdown()


def test_wrong_secret_returns_401() -> None:
  server, port = _start_test_server()
  try:
    status, body = _post(port, '/webhook/bart', secret='wrong-secret')
    assert status == 401
    assert 'Unauthorized' in body
  finally:
    server.shutdown()


def test_missing_secret_returns_401() -> None:
  handler = _mod._make_webhook_handler()
  server = HTTPServer(('127.0.0.1', 0), handler)
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
    server.shutdown()


def test_query_param_secret_accepted() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_cfg._config, _cred_config('bart')):
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
        server.shutdown()


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
    server.shutdown()


def test_header_takes_precedence_over_query_param() -> None:
  """When both are present, the header is used (and must be correct)."""
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_cfg._config, _cred_config('bart')):
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
        server.shutdown()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def test_unknown_integration_returns_404() -> None:
  with patch.dict(_cfg._config, _cred_config('notareal')):
    server, port = _start_test_server()
    try:
      status, _ = _post(port, '/webhook/notareal')
      assert status == 404
    finally:
      server.shutdown()


def test_bad_path_returns_404() -> None:
  # Path check fires before auth — no credential needed.
  server, port = _start_test_server()
  try:
    status, _ = _post(port, '/notwebhook/bart')
    assert status == 404
  finally:
    server.shutdown()


def test_non_post_method_returns_501() -> None:
  # BaseHTTPRequestHandler returns 501 for methods with no do_<METHOD> handler.
  server, port = _start_test_server()
  try:
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request('GET', '/webhook/bart', headers={'X-Webhook-Secret': _CRED_SECRET})
    resp = conn.getresponse()
    assert resp.status == 501
  finally:
    server.shutdown()


def test_integration_without_handle_webhook_returns_404() -> None:
  mock_mod = MagicMock(spec=[])  # no handle_webhook attribute

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, body = _post(port, '/webhook/bart')
        assert status == 404
        assert 'does not support webhooks' in body
      finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Enqueue behaviour
# ---------------------------------------------------------------------------


def test_handle_webhook_none_returns_200_no_enqueue() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.return_value = None

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          status, body = _post(port, '/webhook/bart', {'event': 'pause'})
          assert status == 200
          assert 'Discarded' in body
          mock_enqueue.assert_not_called()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
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
          )
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue') as mock_enqueue:
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)  # allow handler thread to complete
          call_kwargs = mock_enqueue.call_args.kwargs
          assert call_kwargs['name'] == 'webhook.bart'
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)  # allow handler thread to complete
          assert not _mod._hold_interrupt.is_set()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)  # allow handler thread to complete
          assert _mod._hold_interrupt.is_set()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
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
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert not _mod._hold_interrupt.is_set()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert _mod._hold_interrupt.is_set()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert not _mod._hold_interrupt.is_set()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      with patch.object(_mod, 'enqueue'):
        server, port = _start_test_server()
        try:
          _post(port, '/webhook/bart')
          time.sleep(0.05)
          assert _mod._hold_interrupt.is_set()
        finally:
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
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
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
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
          server.shutdown()


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

  with patch.dict(_cfg._config, _cred_config('bart')):
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
          server.shutdown()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_malformed_json_returns_400() -> None:
  with patch.dict(_cfg._config, _cred_config('bart')):
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
      server.shutdown()


def test_handle_webhook_exception_returns_500_server_survives() -> None:
  mock_mod = MagicMock()
  mock_mod.handle_webhook.side_effect = RuntimeError('boom')

  with patch.dict(_cfg._config, _cred_config('bart')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post(port, '/webhook/bart')
        assert status == 500
        # Server should still be alive after the error.
        status2, _ = _post(port, '/webhook/bart')
        assert status2 == 500  # still responding (integration still throws)
      finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Per-integration credential auto-generation
# ---------------------------------------------------------------------------


def test_credential_autogenerated_when_absent(
  tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  config_file = tmp_path / 'config.toml'
  config_file.write_text('[webhook]\nport = 8080\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_cfg, '_config', {'webhook': {'port': '8080'}})

  with patch('scheduler.HTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  assert 'auto-generated' in caplog.text.lower()
  creds = _cfg._config.get('webhook', {}).get('credentials', {})
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
    _cfg,
    '_config',
    {
      'webhook': {
        'port': '8080',
        'credentials': {'plex': {'secret_hash': existing_hash, 'webhooks': ['plex']}},
      }
    },
  )

  with patch('scheduler.HTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  # plex should not be regenerated.
  assert "auto-generated for 'plex'" not in caplog.text
  assert _cfg._config['webhook']['credentials']['plex']['secret_hash'] == existing_hash


def test_message_admin_autogenerated_even_when_friends_present(
  tmp_path: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  """message.admin is auto-generated even if friend credentials already exist."""
  config_file = tmp_path / 'config.toml'
  config_file.write_text('[webhook]\nport = 8080\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(
    _cfg,
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

  with patch('scheduler.HTTPServer') as mock_http:
    mock_http.return_value = MagicMock()
    with patch('threading.Thread'):
      _mod._start_webhook_server()

  assert "auto-generated for 'message'" in caplog.text
  assert 'admin' in _cfg.get_credentials('message')


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
    _cfg,
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

  with patch('scheduler.HTTPServer') as mock_http:
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

  with patch.dict(_cfg._config, _cred_config('plex')):
    with patch.object(_mod, '_get_integration', return_value=mock_mod):
      server, port = _start_test_server()
      try:
        status, _ = _post_multipart(port, '/webhook/plex', '{"event": "media.play"}')
        assert status == 200
        mock_mod.handle_webhook.assert_called_once()
        call_args = mock_mod.handle_webhook.call_args
        assert call_args.args[0].get('event') == 'media.play'
      finally:
        server.shutdown()


def test_multipart_missing_payload_field_returns_400() -> None:
  body = f'--{_BOUNDARY}\r\nContent-Disposition: form-data; name="other"\r\n\r\nvalue\r\n--{_BOUNDARY}--\r\n'.encode()

  with patch.dict(_cfg._config, _cred_config('plex')):
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
      server.shutdown()


def test_multipart_invalid_json_in_payload_returns_400() -> None:
  with patch.dict(_cfg._config, _cred_config('plex')):
    server, port = _start_test_server()
    try:
      status, _ = _post_multipart(port, '/webhook/plex', 'not json{')
      assert status == 400
    finally:
      server.shutdown()


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
        server.shutdown()


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
      server.shutdown()


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
      server.shutdown()


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
          server.shutdown()
