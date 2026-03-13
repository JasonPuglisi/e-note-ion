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
  tmdb._get_type6_group_id.cache_clear()  # noqa: SLF001
  tmdb._get_episode_group.cache_clear()  # noqa: SLF001


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
    'tv_episode_results': [
      {'id': 99001, 'season_number': 4, 'episode_number': 16, 'name': 'Above and Below', 'show_id': 1429}
    ]
  }

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.find_episode_by_tvdb_id(8765432)

  assert result == (4, 16, 'Above and Below', 1429, 99001)


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
  mock_r.json.return_value = {
    'tv_episode_results': [{'id': 77002, 'season_number': 2, 'episode_number': 3, 'name': None, 'show_id': 999}]
  }

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.find_episode_by_tvdb_id(1111111)

  assert result == (2, 3, '', 999, 77002)


# --- find_episode_by_imdb_id ---


def test_find_episode_by_imdb_id_returns_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {
    'tv_episode_results': [
      {'id': 6827061, 'season_number': 1, 'episode_number': 51, 'name': 'Perfect Preparation', 'show_id': 95479}
    ]
  }

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r) as mock_fetch:
    result = tmdb.find_episode_by_imdb_id('tt39370459')

  assert result == (1, 51, 'Perfect Preparation', 95479, 6827061)
  call_args = mock_fetch.call_args
  assert call_args.args[1].endswith('/find/tt39370459')
  assert call_args.kwargs['params'] == {'external_source': 'imdb_id'}


def test_find_episode_by_imdb_id_returns_none_on_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  mock_r = MagicMock()
  mock_r.status_code = 200
  mock_r.json.return_value = {'tv_episode_results': []}

  with patch('integrations.tmdb.fetch_with_retry', return_value=mock_r):
    result = tmdb.find_episode_by_imdb_id('tt0000000')

  assert result is None


def test_find_episode_by_imdb_id_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  with patch('integrations.tmdb.fetch_with_retry', side_effect=Exception('timeout')):
    result = tmdb.find_episode_by_imdb_id('tt99999999')

  assert result is None


# --- get_episode_group_position ---


def test_get_episode_group_position_returns_correct_season_episode(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  groups_r = MagicMock()
  groups_r.status_code = 200
  groups_r.json.return_value = {'results': [{'id': 'grp-abc', 'type': 6}]}

  group_r = MagicMock()
  group_r.status_code = 200
  group_r.json.return_value = {
    'groups': [
      {'order': 0, 'name': 'Specials', 'episodes': [{'id': 9001, 'order': 0}]},
      {'order': 1, 'name': 'Season 1', 'episodes': [{'id': 9002, 'order': 0}, {'id': 9003, 'order': 1}]},
      {'order': 2, 'name': 'Season 2', 'episodes': [{'id': 9004, 'order': 0}, {'id': 9005, 'order': 7}]},
    ]
  }

  with patch('integrations.tmdb.fetch_with_retry', side_effect=[groups_r, group_r]):
    result = tmdb.get_episode_group_position(209867, 9005)

  assert result == (2, 8)


def test_get_episode_group_position_skips_specials_group(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  groups_r = MagicMock()
  groups_r.status_code = 200
  groups_r.json.return_value = {'results': [{'id': 'grp-abc', 'type': 6}]}

  group_r = MagicMock()
  group_r.status_code = 200
  group_r.json.return_value = {
    'groups': [
      {'order': 0, 'name': 'Specials', 'episodes': [{'id': 9001, 'order': 0}]},
    ]
  }

  with patch('integrations.tmdb.fetch_with_retry', side_effect=[groups_r, group_r]):
    result = tmdb.get_episode_group_position(209867, 9001)

  assert result is None


def test_get_episode_group_position_no_type6_group(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  groups_r = MagicMock()
  groups_r.status_code = 200
  groups_r.json.return_value = {'results': [{'id': 'grp-xyz', 'type': 5}]}

  with patch('integrations.tmdb.fetch_with_retry', return_value=groups_r):
    result = tmdb.get_episode_group_position(12345, 9999)

  assert result is None


def test_get_episode_group_position_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  with patch('integrations.tmdb.fetch_with_retry', side_effect=Exception('timeout')):
    result = tmdb.get_episode_group_position(209867, 9005)

  assert result is None
