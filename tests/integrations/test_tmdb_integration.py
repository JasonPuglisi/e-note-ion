"""Integration tests for integrations/tmdb.py — call the real TMDb API.

Run with: uv run pytest -m integration

Required env vars:
  TMDB_API_READ_ACCESS_TOKEN — read access token from https://www.themoviedb.org/settings/api
"""

import os

import pytest

import config as _cfg
import integrations.tmdb as tmdb


def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
  """Inject real API credentials from env into the in-memory config."""
  monkeypatch.setattr(
    _cfg,
    '_config',
    {'tmdb': {'api_read_access_token': os.environ['TMDB_API_READ_ACCESS_TOKEN']}},
  )


@pytest.fixture(autouse=True)
def _clear_lru_caches() -> None:
  tmdb.get_show_title.cache_clear()
  tmdb.get_movie_title.cache_clear()
  tmdb.find_episode_by_tvdb_id.cache_clear()


@pytest.mark.integration
@pytest.mark.require_env('TMDB_API_READ_ACCESS_TOKEN')
def test_get_show_title_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_show_title() returns the canonical name for a known TMDb show ID."""
  _patch_config(monkeypatch)
  # TMDb ID 1429 = Attack on Titan
  result = tmdb.get_show_title(1429)
  assert result is not None
  assert isinstance(result, str)
  assert len(result) > 0


@pytest.mark.integration
@pytest.mark.require_env('TMDB_API_READ_ACCESS_TOKEN')
def test_get_movie_title_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_movie_title() returns the canonical title for a known TMDb movie ID."""
  _patch_config(monkeypatch)
  # TMDb ID 27205 = Inception
  result = tmdb.get_movie_title(27205)
  assert result == 'Inception'


@pytest.mark.integration
@pytest.mark.require_env('TMDB_API_READ_ACCESS_TOKEN')
def test_find_episode_by_tvdb_id_live(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """find_episode_by_tvdb_id() returns a valid (season, episode, title, show_id) tuple."""
  _patch_config(monkeypatch)
  # TVDb episode ID 5073066 = Attack on Titan S4E16 in TMDb ordering
  result = tmdb.find_episode_by_tvdb_id(5073066)
  if result is not None:
    season, episode, title, show_id = result
    assert isinstance(season, int) and season > 0
    assert isinstance(episode, int) and episode > 0
    assert isinstance(title, str)
    assert isinstance(show_id, int) and show_id > 0
