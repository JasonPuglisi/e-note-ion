from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.discogs as discogs
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def reset_caches() -> Generator[None, None, None]:
  """Reset module-level caches before each test."""
  discogs._username_cache = None
  discogs._collection_cache = None
  yield
  discogs._username_cache = None
  discogs._collection_cache = None


@pytest.fixture()
def discogs_config(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with Discogs settings."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'discogs': {'token': 'test-token'}})


def _mock_identity(username: str = 'testuser') -> MagicMock:
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {'username': username, 'id': 1}
  return mock


def _mock_page(
  releases: list[dict[str, Any]],
  total: int,
  page: int = 1,
  pages: int = 1,
) -> MagicMock:
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {
    'pagination': {'items': total, 'pages': pages, 'page': page, 'per_page': 50},
    'releases': releases,
  }
  return mock


def _release(title: str = 'Test Album', artist: str = 'Test Artist') -> dict[str, Any]:
  return {
    'basic_information': {
      'title': title,
      'artists': [{'name': artist}],
    }
  }


# --- get_variables: basic structure ---


def test_get_variables_returns_expected_keys(discogs_config: None) -> None:
  release = _release()
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      result = discogs.get_variables()
  assert set(result.keys()) == {'album', 'artist'}


# --- identity resolution ---


def test_username_resolved_from_identity(discogs_config: None) -> None:
  release = _release()
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity('vinyl_fan'), _mock_page([release], total=1)],
    ) as mock_fetch:
      discogs.get_variables()
  # Second call should use the resolved username in the URL.
  collection_url = mock_fetch.call_args_list[1].args[1]
  assert 'vinyl_fan' in collection_url


def test_username_cached_after_first_call(discogs_config: None) -> None:
  """Identity endpoint should only be called once across multiple get_variables() calls."""
  release = _release()
  page = _mock_page([release], total=1)
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), page, page],
    ) as mock_fetch:
      discogs.get_variables()
      discogs.get_variables()
  # First call: identity + page1. Second call: page1 only.
  assert mock_fetch.call_count == 3


def test_identity_failure_raises_unavailable(discogs_config: None) -> None:
  with patch('integrations.discogs.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError):
      discogs.get_variables()


# --- artist formatting ---


def test_artist_strips_leading_the(discogs_config: None) -> None:
  release = _release(artist='The Talking Heads')
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      result = discogs.get_variables()
  assert result['artist'][0][0] == 'TALKING HEADS'


def test_artist_strips_disambiguator(discogs_config: None) -> None:
  release = _release(artist='David Bowie (2)')
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      result = discogs.get_variables()
  assert result['artist'][0][0] == 'DAVID BOWIE'


def test_artist_uses_first_only_for_multi_artist(discogs_config: None) -> None:
  release: dict[str, Any] = {
    'basic_information': {
      'title': 'Collab Album',
      'artists': [{'name': 'Artist A'}, {'name': 'Artist B'}],
    }
  }
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      result = discogs.get_variables()
  assert result['artist'][0][0] == 'ARTIST A'


# --- album formatting ---


def test_album_strips_leading_the(discogs_config: None) -> None:
  release = _release(title='The Wall')
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      result = discogs.get_variables()
  assert result['album'][0][0] == 'WALL'


def test_album_no_year(discogs_config: None) -> None:
  """Album variable must not include the year."""
  release: dict[str, Any] = {
    'basic_information': {
      'title': 'Some Album',
      'year': 1980,
      'artists': [{'name': 'Some Artist'}],
    }
  }
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      result = discogs.get_variables()
  assert '1980' not in result['album'][0][0]


# --- page selection ---


def test_page1_reused_when_offset_on_page1(discogs_config: None) -> None:
  """When random offset < per_page, only 1 collection call is made (page 1 reused)."""
  releases = [_release()] * 50
  with patch('integrations.discogs.random.randint', return_value=5):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page(releases, total=100, pages=2)],
    ) as mock_fetch:
      discogs.get_variables()
  assert mock_fetch.call_count == 2  # identity + page1


def test_page2_fetched_when_offset_beyond_page1(discogs_config: None) -> None:
  """When random offset >= per_page, a second collection call is made for page 2."""
  releases = [_release()] * 50
  with patch('integrations.discogs.random.randint', return_value=55):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[
        _mock_identity(),
        _mock_page(releases, total=100, pages=2),
        _mock_page(releases, total=100, page=2, pages=2),
      ],
    ) as mock_fetch:
      discogs.get_variables()
  assert mock_fetch.call_count == 3  # identity + page1 + page2


# --- empty collection ---


def test_empty_collection_raises_unavailable(discogs_config: None) -> None:
  with patch(
    'integrations.discogs.fetch_with_retry',
    side_effect=[_mock_identity(), _mock_page([], total=0)],
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='empty'):
      discogs.get_variables()


# --- cache behaviour ---


def test_cache_updated_on_success(discogs_config: None) -> None:
  assert discogs._collection_cache is None
  release = _release(title='Remain In Light')
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      discogs.get_variables()
  assert discogs._collection_cache is not None
  assert discogs._collection_cache.value['album'][0][0] == 'REMAIN IN LIGHT'


def test_api_error_returns_cache_within_ttl(discogs_config: None) -> None:
  release = _release(title='Kind Of Blue')
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      discogs.get_variables()
  with patch('integrations.discogs.fetch_with_retry', side_effect=requests.ConnectionError()):
    result = discogs.get_variables()
  assert result['album'][0][0] == 'KIND OF BLUE'


def test_api_error_expired_cache_raises_unavailable(discogs_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
  import time

  release = _release()
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ):
      discogs.get_variables()

  assert discogs._collection_cache is not None
  monkeypatch.setattr(discogs._collection_cache, 'cached_at', time.monotonic() - discogs._COLLECTION_CACHE_TTL - 1)

  with patch('integrations.discogs.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError):
      discogs.get_variables()


def test_api_error_cold_start_raises_unavailable(discogs_config: None) -> None:
  # Identity succeeds, collection fails — no cache yet.
  with patch(
    'integrations.discogs.fetch_with_retry',
    side_effect=[_mock_identity(), requests.ConnectionError()],
  ):
    with pytest.raises(IntegrationDataUnavailableError):
      discogs.get_variables()


# --- User-Agent ---


def test_user_agent_header_sent(discogs_config: None) -> None:
  release = _release()
  with patch('integrations.discogs.random.randint', return_value=0):
    with patch(
      'integrations.discogs.fetch_with_retry',
      side_effect=[_mock_identity(), _mock_page([release], total=1)],
    ) as mock_fetch:
      discogs.get_variables()
  # Check all calls include the User-Agent header.
  for call in mock_fetch.call_args_list:
    headers = call.kwargs['headers']
    assert 'User-Agent' in headers
    assert headers['User-Agent'].startswith('e-note-ion/')
