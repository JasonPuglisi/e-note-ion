from unittest.mock import MagicMock, call, patch

import pytest
import requests

import integrations.http as http_mod
from integrations.http import CacheEntry, fetch_with_retry, user_agent


@pytest.fixture(autouse=True)
def reset_ua_cache() -> None:
  """Reset the user_agent cache so each test starts clean."""
  http_mod._ua_cache = None


def _mock_response(status_code: int, reason: str = '') -> MagicMock:
  mock = MagicMock(spec=requests.Response)
  mock.status_code = status_code
  mock.reason = reason
  return mock


# --- fetch_with_retry ---


def test_fetch_with_retry_success_first_attempt() -> None:
  resp = _mock_response(200)
  with patch('integrations.http.requests.request', return_value=resp) as mock_req:
    result = fetch_with_retry('GET', 'https://example.com', timeout=5)
  assert result is resp
  mock_req.assert_called_once_with('GET', 'https://example.com', timeout=5)


def test_fetch_with_retry_retries_on_503_then_succeeds() -> None:
  fail = _mock_response(503, 'Service Unavailable')
  ok = _mock_response(200)
  with patch('integrations.http.requests.request', side_effect=[fail, ok]) as mock_req:
    with patch('integrations.http.time.sleep') as mock_sleep:
      result = fetch_with_retry('GET', 'https://example.com', retries=3, backoff=1.0)
  assert result is ok
  assert mock_req.call_count == 2
  mock_sleep.assert_called_once_with(1.0)  # backoff * 2**0


def test_fetch_with_retry_does_not_retry_on_404() -> None:
  resp = _mock_response(404, 'Not Found')
  with patch('integrations.http.requests.request', return_value=resp) as mock_req:
    result = fetch_with_retry('GET', 'https://example.com')
  assert result is resp
  mock_req.assert_called_once()


def test_fetch_with_retry_retries_on_timeout() -> None:
  ok = _mock_response(200)
  with patch(
    'integrations.http.requests.request',
    side_effect=[requests.Timeout(), ok],
  ) as mock_req:
    with patch('integrations.http.time.sleep'):
      result = fetch_with_retry('GET', 'https://example.com', retries=3, backoff=1.0)
  assert result is ok
  assert mock_req.call_count == 2


def test_fetch_with_retry_retries_on_connection_error() -> None:
  ok = _mock_response(200)
  with patch(
    'integrations.http.requests.request',
    side_effect=[requests.ConnectionError(), ok],
  ) as mock_req:
    with patch('integrations.http.time.sleep'):
      result = fetch_with_retry('GET', 'https://example.com', retries=3, backoff=1.0)
  assert result is ok
  assert mock_req.call_count == 2


def test_fetch_with_retry_raises_after_exhausting_retries_5xx() -> None:
  fail = _mock_response(502, 'Bad Gateway')
  with patch('integrations.http.requests.request', return_value=fail):
    with patch('integrations.http.time.sleep'):
      with pytest.raises(requests.HTTPError):
        fetch_with_retry('GET', 'https://example.com', retries=2, backoff=1.0)


def test_fetch_with_retry_raises_after_exhausting_retries_timeout() -> None:
  with patch('integrations.http.requests.request', side_effect=requests.Timeout()):
    with patch('integrations.http.time.sleep'):
      with pytest.raises(requests.Timeout):
        fetch_with_retry('GET', 'https://example.com', retries=2, backoff=1.0)


def test_fetch_with_retry_exponential_backoff() -> None:
  fail = _mock_response(503)
  ok = _mock_response(200)
  with patch('integrations.http.requests.request', side_effect=[fail, fail, ok]):
    with patch('integrations.http.time.sleep') as mock_sleep:
      fetch_with_retry('GET', 'https://example.com', retries=3, backoff=2.0)
  assert mock_sleep.call_args_list == [call(2.0), call(4.0)]  # 2**0, 2**1


def test_fetch_with_retry_passes_kwargs() -> None:
  resp = _mock_response(200)
  with patch('integrations.http.requests.request', return_value=resp) as mock_req:
    fetch_with_retry(
      'POST',
      'https://example.com/api',
      timeout=15,
      headers={'X-Key': 'abc'},
      json={'foo': 'bar'},
    )
  mock_req.assert_called_once_with(
    'POST',
    'https://example.com/api',
    timeout=15,
    headers={'X-Key': 'abc'},
    json={'foo': 'bar'},
  )


# --- CacheEntry ---


def test_cache_entry_is_valid_within_ttl() -> None:
  entry = CacheEntry({'x': [['v']]})
  assert entry.is_valid(60)


def test_cache_entry_is_invalid_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
  entry = CacheEntry({'x': [['v']]})
  monkeypatch.setattr('integrations.http.time.monotonic', lambda: entry.cached_at + 61)
  assert not entry.is_valid(60)


# --- user_agent ---


def test_user_agent_format() -> None:
  with patch('integrations.http.importlib.metadata.version', return_value='1.2.3'):
    result = user_agent()
  assert result == 'e-note-ion/1.2.3'


def test_user_agent_dev_fallback() -> None:
  import importlib.metadata as _meta

  with patch('integrations.http.importlib.metadata.version', side_effect=_meta.PackageNotFoundError):
    result = user_agent()
  assert result == 'e-note-ion/dev'


def test_user_agent_cached() -> None:
  with patch('integrations.http.importlib.metadata.version', return_value='1.0.0') as mock_ver:
    user_agent()
    user_agent()
  mock_ver.assert_called_once()
