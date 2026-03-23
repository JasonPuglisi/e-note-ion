"""Integration tests for integrations/youtube.py — call the real YouTube API.

Run with: uv run pytest -m integration

Required env vars:
  GOOGLE_CLIENT_ID      — Google OAuth client ID
  GOOGLE_CLIENT_SECRET  — Google OAuth client secret
  GOOGLE_REFRESH_TOKEN  — refresh token (stable; does not rotate like Trakt)

Unlike Trakt (whose access tokens last ~90 days and can be stored directly),
Google access tokens expire after ~1 hour. Instead of storing a short-lived
access token, we store the refresh token and obtain a fresh access token at
test time. In Production mode (recommended), the refresh token does not expire
or rotate, so the CI secret only needs updating if access is revoked.
"""

import os
import time

import pytest
import requests

import config as _config_mod
import integrations.google as google
import integrations.youtube as youtube
from exceptions import IntegrationDataUnavailableError
from integrations.http import user_agent


def _obtain_access_token() -> str:
  """Exchange the refresh token for a fresh access token."""
  r = requests.post(
    'https://oauth2.googleapis.com/token',
    data={
      'client_id': os.environ['GOOGLE_CLIENT_ID'],
      'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
      'refresh_token': os.environ['GOOGLE_REFRESH_TOKEN'],
      'grant_type': 'refresh_token',
    },
    headers={'User-Agent': user_agent()},
    timeout=10,
  )
  r.raise_for_status()
  return r.json()['access_token']


def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
  """Inject real API credentials from env into the in-memory config."""
  access_token = _obtain_access_token()
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'google': {
        'client_id': os.environ['GOOGLE_CLIENT_ID'],
        'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
        'access_token': access_token,
        'refresh_token': os.environ['GOOGLE_REFRESH_TOKEN'],
        'expires_at': int(time.time()) + 3600,
      }
    },
  )
  monkeypatch.setattr(_config_mod, 'write_section_values', lambda section, values: None)


@pytest.mark.integration
@pytest.mark.require_env('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REFRESH_TOKEN')
def test_get_variables_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid vars or raises DataUnavailable — both are correct."""
  _patch_config(monkeypatch)
  google._auth_started = False
  youtube._sub_cache = None
  youtube._vars_cache = None

  try:
    result = youtube.get_variables()
    assert 'channel' in result
    assert 'title' in result
    for key in result:
      assert len(result[key]) == 1
      assert len(result[key][0]) == 1
      assert isinstance(result[key][0][0], str)
      assert result[key][0][0] == result[key][0][0].upper()
  except IntegrationDataUnavailableError:
    pass  # nothing live — valid outcome
  finally:
    youtube._sub_cache = None
    youtube._vars_cache = None
