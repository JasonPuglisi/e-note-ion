"""Unit tests for integrations/tmdb.py."""

from unittest.mock import MagicMock, patch

import pytest

import integrations.tmdb as tmdb


@pytest.fixture(autouse=True)
def _clear_lru_caches() -> None:
  """Clear LRU caches before each test to avoid cross-test contamination."""
  tmdb.get_show_title.cache_clear()
  tmdb.get_movie_title.cache_clear()
  tmdb.find_episode_by_tvdb_id.cache_clear()


# --- is_configured ---


def test_is_configured_true(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  assert tmdb.is_configured() is True


def test_is_configured_false_missing_section(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {})
  assert tmdb.is_configured() is False


def test_is_configured_false_empty_token(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': ''}})
  assert tmdb.is_configured() is False


# --- get_show_title ---


def test_get_show_title_returns_name(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {'name': 'Attack on Titan', 'id': 1429}

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.get_show_title(1429)

  assert result == 'Attack on Titan'


def test_get_show_title_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  with patch('integrations.tmdb.fetch_with_retry', side_effect=Exception('timeout')):
    result = tmdb.get_show_title(9999)

  assert result is None


def test_get_show_title_returns_none_on_missing_name(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {}

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.get_show_title(1429)

  assert result is None


# --- get_movie_title ---


def test_get_movie_title_returns_title(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {'title': 'Inception', 'id': 27205}

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.get_movie_title(27205)

  assert result == 'Inception'


def test_get_movie_title_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  with patch('integrations.tmdb.fetch_with_retry', side_effect=Exception('timeout')):
    result = tmdb.get_movie_title(27205)

  assert result is None


# --- find_episode_by_tvdb_id ---


def test_find_episode_by_tvdb_id_returns_season_episode_title(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {
    'tv_episode_results': [{'season_number': 4, 'episode_number': 16, 'name': 'Above and Below'}]
  }

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.find_episode_by_tvdb_id(8765432)

  assert result == (4, 16, 'Above and Below')


def test_find_episode_by_tvdb_id_returns_none_on_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {'tv_episode_results': []}

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.find_episode_by_tvdb_id(9999999)

  assert result is None


def test_find_episode_by_tvdb_id_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  with patch('integrations.tmdb.fetch_with_retry', side_effect=Exception('timeout')):
    result = tmdb.find_episode_by_tvdb_id(8765432)

  assert result is None


def test_find_episode_by_tvdb_id_empty_title_returns_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {'tv_episode_results': [{'season_number': 2, 'episode_number': 3, 'name': None}]}

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.find_episode_by_tvdb_id(1111111)

  assert result == (2, 3, '')
