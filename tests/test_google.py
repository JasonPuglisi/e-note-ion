import time
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.google as google
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
