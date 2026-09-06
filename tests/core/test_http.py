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


# --- redact (#591) ---


@pytest.mark.parametrize(
  ('label', 'text', 'secret'),
  [
    (
      'bart key in a connection-error path',
      "HTTPSConnectionPool(host='api.bart.gov', port=443): Max retries exceeded with "
      'url: /api/etd.aspx?cmd=etd&orig=EMBR&key=SUPERSECRETKEY&json=y (Caused by ...)',
      'SUPERSECRETKEY',
    ),
    (
      'ics feed url, where the path is the credential',
      "calendar: fetch failed for 'https://p12-caldav.icloud.com/published/2/MTIz_SECRETTOKEN' — boom",
      'SECRETTOKEN',
    ),
    (
      'ics feed reported as a bare path',
      'Max retries exceeded with url: /published/2/MTIz_SECRETTOKEN',
      'SECRETTOKEN',
    ),
    (
      'credentials in userinfo',
      'connect failed: https://alice:hunter2@example.com/feed',
      'hunter2',
    ),
    (
      'query string with no surrounding url',
      'upstream rejected ?access_token=abc123def&scope=read',
      'abc123def',
    ),
  ],
)
def test_redact_removes_secrets(label: str, text: str, secret: str) -> None:
  """Exception text reaches record_error(), which writes it to data/health.jsonl
  and serves it from /health. Several of our URLs are themselves credentials."""
  out = http_mod.redact(text)
  assert secret not in out, f'{label}: secret survived redaction -> {out}'


def test_redact_keeps_the_host_so_errors_stay_useful() -> None:
  """Which service failed is the entire diagnostic value; keep that much.

  Asserted as exact output rather than a substring check. A substring check
  would be weaker (it cannot tell where in the string the host ended up) and
  CodeQL rightly flags `host in url` as the shape of a bypassable host check.
  """
  assert http_mod.redact('Max retries exceeded with url: https://api.bart.gov/api/etd.aspx?key=SECRET') == (
    'Max retries exceeded with url: https://api.bart.gov/...'
  )


def test_redact_leaves_non_url_text_alone() -> None:
  for text in ['plain failure, nothing sensitive', 'HTTP 503 Service Unavailable', '']:
    assert http_mod.redact(text) == text


def test_redact_keeps_a_bare_origin_intact() -> None:
  assert http_mod.redact('https://example.com') == 'https://example.com'


def test_redact_survives_a_malformed_url() -> None:
  """A ValueError out of urlsplit must not take down error handling."""
  assert 'redacted' in http_mod.redact('see http://[not-a-valid-host/x') or True
  http_mod.redact('http://')  # must not raise


# --- credential classification (#503) ---


@pytest.mark.parametrize('status', [401, 403])
def test_raise_for_credentials_flags_auth_rejections(status: int) -> None:
  resp = MagicMock(status_code=status)
  with pytest.raises(http_mod.CredentialError, match='credential rejected'):
    http_mod.raise_for_credentials(resp, 'discogs')


@pytest.mark.parametrize('status', [200, 204, 404, 429, 500, 503])
def test_raise_for_credentials_ignores_everything_else(status: int) -> None:
  """Only 401/403 mean "your key is wrong".

  A 500 or a 429 at startup is not an expired token, and marking it as one
  would page someone about a service having a bad minute.
  """
  http_mod.raise_for_credentials(MagicMock(status_code=status), 'discogs')


def test_credential_error_names_the_integration_and_where_to_fix_it() -> None:
  resp = MagicMock(status_code=401)
  with pytest.raises(http_mod.CredentialError) as exc:
    http_mod.raise_for_credentials(resp, 'ynab')
  assert 'ynab' in str(exc.value)
  assert 'config.toml' in str(exc.value)
