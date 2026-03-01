"""Integration tests for integrations/trakt.py — call the real Trakt API.

Run with: uv run pytest -m integration

Required env vars:
  TRAKT_CLIENT_ID     — Trakt OAuth client ID
  TRAKT_CLIENT_SECRET — Trakt OAuth client secret
  TRAKT_ACCESS_TOKEN  — pre-authorised access token (run auth flow manually first)
"""

import os
import time

import pytest

import config as _cfg
import integrations.trakt as trakt
from exceptions import IntegrationDataUnavailableError


def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
  """Inject real API credentials from env into the in-memory config."""
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': os.environ['TRAKT_CLIENT_ID'],
        'client_secret': os.environ['TRAKT_CLIENT_SECRET'],
        'access_token': os.environ['TRAKT_ACCESS_TOKEN'],
        'expires_at': int(time.time()) + 10000,  # assume valid for test run
      }
    },
  )


@pytest.mark.integration
@pytest.mark.require_env('TRAKT_CLIENT_ID', 'TRAKT_CLIENT_SECRET', 'TRAKT_ACCESS_TOKEN')
def test_get_variables_calendar_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables_calendar() returns a valid variables dict from the live Trakt API."""
  _patch_config(monkeypatch)
  trakt._auth_started = False

  try:
    result = trakt.get_variables_calendar()
  except IntegrationDataUnavailableError:
    pytest.skip('Calendar is empty for the lookahead window — valid outcome')

  assert 'show_name' in result
  assert 'episode_ref' in result
  assert 'air_day' in result
  assert 'air_time' in result
  assert 'episode_title' in result

  for key in result:
    assert len(result[key]) == 1
    assert len(result[key][0]) == 1
    assert isinstance(result[key][0][0], str)


@pytest.mark.integration
@pytest.mark.require_env('TRAKT_CLIENT_ID', 'TRAKT_CLIENT_SECRET', 'TRAKT_ACCESS_TOKEN')
def test_get_variables_watching_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables_watching() returns valid vars or raises DataUnavailable — both are correct."""
  _patch_config(monkeypatch)
  trakt._auth_started = False

  try:
    result = trakt.get_variables_watching()
    assert 'show_name' in result
    assert 'episode_ref' in result
    assert 'episode_title' in result
  except IntegrationDataUnavailableError:
    pass  # nothing playing — valid outcome


@pytest.mark.integration
@pytest.mark.require_env('TRAKT_CLIENT_ID', 'TRAKT_CLIENT_SECRET', 'TRAKT_ACCESS_TOKEN')
def test_get_variables_next_up_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables_next_up() returns valid vars or raises DataUnavailable — both are correct."""
  _patch_config(monkeypatch)
  trakt._auth_started = False
  trakt._next_up_cache = None

  try:
    result = trakt.get_variables_next_up()
    assert 'show_name' in result
    assert 'episode_ref' in result
    assert 'episode_title' in result
    for key in result:
      assert len(result[key]) == 1
      assert len(result[key][0]) == 1
      assert isinstance(result[key][0][0], str)
  except IntegrationDataUnavailableError:
    pass  # user may have no shows in progress — valid outcome
