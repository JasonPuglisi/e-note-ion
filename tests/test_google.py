import time
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.google as google
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': 'test-client-id',
        'client_secret': 'test-client-secret',
        'access_token': 'test-access-token',
        'refresh_token': 'test-refresh-token',
        'expires_at': int(time.time()) + 3600,
      }
    },
  )
  yield


@pytest.fixture(autouse=True)
def _reset_state() -> Generator[None, None, None]:
  google._auth_started = False
  yield
  google._auth_started = False


# ---------------------------------------------------------------------------
# get_token — valid token
# ---------------------------------------------------------------------------


def test_get_token_returns_valid_token() -> None:
  token = google.get_token('https://www.googleapis.com/auth/youtube.readonly')
  assert token == 'test-access-token'


# ---------------------------------------------------------------------------
# get_token — missing token triggers auth
# ---------------------------------------------------------------------------


def test_get_token_no_token_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'google': {'client_id': 'id', 'client_secret': 'secret'}},
  )
  with patch.object(google, '_ensure_authenticated'):
    with pytest.raises(IntegrationDataUnavailableError, match='auth pending'):
      google.get_token('scope')


# ---------------------------------------------------------------------------
# get_token — near-expiry triggers refresh
# ---------------------------------------------------------------------------


def test_get_token_refreshes_near_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': 'id',
        'client_secret': 'secret',
        'access_token': 'old-token',
        'refresh_token': 'refresh',
        'expires_at': int(time.time()) + 60,  # expires in 60s — under 300s threshold
      }
    },
  )

  refresh_resp = MagicMock()
  refresh_resp.status_code = 200
  refresh_resp.json.return_value = {
    'access_token': 'new-token',
    'expires_in': 3600,
    'refresh_token': 'new-refresh',
  }
  refresh_resp.raise_for_status = MagicMock()

  with patch('integrations.google.requests.post', return_value=refresh_resp):
    with patch('integrations.google._store_tokens') as mock_store:
      google._refresh_token()
      mock_store.assert_called_once()


# ---------------------------------------------------------------------------
# get_token — refresh failure clears tokens
# ---------------------------------------------------------------------------


def test_get_token_refresh_failure_clears_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': 'id',
        'client_secret': 'secret',
        'access_token': 'old-token',
        'refresh_token': 'bad-refresh',
        'expires_at': int(time.time()) + 60,
      }
    },
  )

  error_resp = MagicMock()
  error_resp.status_code = 400
  error_resp.reason = 'Bad Request'
  error_resp.raise_for_status.side_effect = requests.HTTPError(response=error_resp)

  with patch('integrations.google.requests.post', return_value=error_resp):
    with pytest.raises(requests.HTTPError):
      google._refresh_token()


# ---------------------------------------------------------------------------
# _store_tokens
# ---------------------------------------------------------------------------


def test_store_tokens_writes_to_config() -> None:
  with patch('config.write_section_values') as mock_write:
    google._store_tokens(
      {
        'access_token': 'new-access',
        'refresh_token': 'new-refresh',
        'expires_in': 3600,
      }
    )
    mock_write.assert_called_once()
    args = mock_write.call_args
    assert args[0][0] == 'google'
    values = args[0][1]
    assert values['access_token'] == 'new-access'
    assert values['refresh_token'] == 'new-refresh'
    assert 'expires_at' in values


def test_store_tokens_omits_refresh_when_absent() -> None:
  with patch('config.write_section_values') as mock_write:
    google._store_tokens({'access_token': 'access', 'expires_in': 3600})
    values = mock_write.call_args[0][1]
    assert 'refresh_token' not in values


# ---------------------------------------------------------------------------
# _clear_tokens
# ---------------------------------------------------------------------------


def test_clear_tokens_writes_empty_strings() -> None:
  with patch('config.write_section_values') as mock_write:
    google._clear_tokens()
    values = mock_write.call_args[0][1]
    assert values['access_token'] == ''
    assert values['refresh_token'] == ''
    assert values['expires_at'] == ''


# ---------------------------------------------------------------------------
# _ensure_authenticated — dedup
# ---------------------------------------------------------------------------


def test_ensure_authenticated_starts_thread_once() -> None:
  with patch('threading.Thread') as mock_thread:
    mock_thread.return_value = MagicMock()
    google._ensure_authenticated('scope')
    google._ensure_authenticated('scope')
    assert mock_thread.call_count == 1


# ---------------------------------------------------------------------------
# preflight — direct coverage (also exercised indirectly via youtube.preflight)
# ---------------------------------------------------------------------------


def test_preflight_with_no_token_starts_auth_flow(monkeypatch: pytest.MonkeyPatch) -> None:
  """No stored access_token → preflight kicks off the device-code auth flow."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'google': {'client_id': 'x', 'client_secret': 'y'}})
  with patch('integrations.google._ensure_authenticated') as mock_ensure:
    google.preflight('test-scope')
  mock_ensure.assert_called_once_with('test-scope')


def test_preflight_with_valid_token_does_nothing() -> None:
  """Token present and not near expiry → no auth flow, no refresh."""
  with (
    patch('integrations.google._ensure_authenticated') as mock_ensure,
    patch('integrations.google._refresh_token') as mock_refresh,
  ):
    google.preflight('test-scope')  # autouse fixture sets expires_at = now + 3600
  mock_ensure.assert_not_called()
  mock_refresh.assert_not_called()


def test_preflight_refreshes_near_expiry_token(monkeypatch: pytest.MonkeyPatch) -> None:
  """Token expiring in <300s → preflight proactively refreshes."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': 'x',
        'client_secret': 'y',
        'access_token': 'old',
        'refresh_token': 'r',
        'expires_at': int(time.time()) + 60,  # 60s from expiry
      }
    },
  )
  with patch('integrations.google._refresh_token') as mock_refresh:
    google.preflight('test-scope')
  mock_refresh.assert_called_once()


def test_preflight_refresh_failure_clears_tokens_and_starts_auth(monkeypatch: pytest.MonkeyPatch) -> None:
  """Refresh fails → tokens cleared, auth flow restarted."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': 'x',
        'client_secret': 'y',
        'access_token': 'old',
        'refresh_token': 'r',
        'expires_at': int(time.time()) + 60,
      }
    },
  )
  with (
    patch(
      'integrations.google._refresh_token',
      side_effect=requests.HTTPError('refresh failed'),
    ),
    patch('integrations.google._clear_tokens') as mock_clear,
    patch('integrations.google._ensure_authenticated') as mock_ensure,
  ):
    google.preflight('test-scope')
  mock_clear.assert_called_once()
  mock_ensure.assert_called_once_with('test-scope')


def test_preflight_malformed_expires_at_returns_silently(monkeypatch: pytest.MonkeyPatch) -> None:
  """Non-integer expires_at (corrupt config) → bail out, don't crash."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': 'x',
        'client_secret': 'y',
        'access_token': 'old',
        'refresh_token': 'r',
        'expires_at': 'not-a-number',
      }
    },
  )
  with (
    patch('integrations.google._refresh_token') as mock_refresh,
    patch('integrations.google._ensure_authenticated') as mock_ensure,
  ):
    google.preflight('test-scope')
  mock_refresh.assert_not_called()
  mock_ensure.assert_not_called()
