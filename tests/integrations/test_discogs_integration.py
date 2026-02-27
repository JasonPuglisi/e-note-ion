"""Integration tests for integrations/discogs.py — call the real Discogs API.

Run with: uv run pytest -m integration

Required env vars:
  DISCOGS_TOKEN  — personal access token (read-only)
"""

import os
from typing import Generator

import pytest

import config as _cfg
import integrations.discogs as discogs


@pytest.fixture(autouse=True)
def reset_caches() -> Generator[None, None, None]:
  discogs._username_cache = None
  discogs._collection_cache = None
  yield
  discogs._username_cache = None
  discogs._collection_cache = None


@pytest.mark.integration
@pytest.mark.require_env('DISCOGS_TOKEN')
def test_get_variables_returns_artist_and_album(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns a valid variables dict from the live Discogs API."""
  monkeypatch.setattr(_cfg, '_config', {'discogs': {'token': os.environ['DISCOGS_TOKEN']}})

  result = discogs.get_variables()

  assert set(result.keys()) == {'album', 'artist'}

  for key in ('album', 'artist'):
    assert len(result[key]) == 1, f'{key}: expected 1 option'
    assert len(result[key][0]) == 1, f'{key}: expected 1 line'
    assert result[key][0][0], f'{key}: value is empty'
