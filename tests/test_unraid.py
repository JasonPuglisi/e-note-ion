"""Unit tests for integrations/unraid.py."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

import integrations.unraid as unraid
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graphql_response(data: dict | None = None, errors: list | None = None) -> MagicMock:
  """Build a mock GraphQL response."""
  body: dict = {}
  if data is not None:
    body['data'] = data
  if errors is not None:
    body['errors'] = errors
  resp = MagicMock()
  resp.json.return_value = body
  resp.raise_for_status = MagicMock()
  return resp


def _boot_timestamp(seconds_ago: int) -> str:
  """Return an ISO 8601 UTC timestamp for N seconds ago (matches Unraid API)."""
  boot = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
  return boot.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _normal_data(
  *,
  uptime_secs: int = 300_000,
  state: str = 'STARTED',
  used_kb: str = '14200000000',
  total_kb: str = '20000000000',
) -> dict:
  """Build a normal Unraid GraphQL data payload."""
  return {
    'info': {'os': {'uptime': _boot_timestamp(uptime_secs)}},
    'array': {
      'state': state,
      'capacity': {'kilobytes': {'used': used_kb, 'total': total_kb}},
    },
  }


def _patched_config() -> dict:
  return {
    'unraid': {
      'url': 'http://192.168.1.10',
      'api_key': 'test-key',
    }
  }


# ---------------------------------------------------------------------------
# _fmt_size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'size_bytes,expected',
  [
    (0, ('0', 'GB')),
    (500 * 10**9, ('500', 'GB')),
    (10**12, ('1', 'TB')),
    (int(1.2 * 10**12), ('1.2', 'TB')),
    (int(14.0 * 10**12), ('14', 'TB')),
    (20 * 10**12, ('20', 'TB')),
  ],
)
def test_fmt_size(size_bytes: int, expected: tuple[str, str]) -> None:
  assert unraid._fmt_size(size_bytes) == expected


# ---------------------------------------------------------------------------
# _fmt_uptime
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'seconds,expected',
  [
    (0, 'UP 0H'),
    (3600, 'UP 1H'),
    (23 * 3600, 'UP 23H'),
    (24 * 3600, 'UP 1D'),
    (25 * 3600, 'UP 1D 1H'),
    (3 * 24 * 3600 + 8 * 3600, 'UP 3D 8H'),
    (30 * 24 * 3600, 'UP 1M'),
    (30 * 24 * 3600 + 2 * 24 * 3600 + 8 * 3600, 'UP 1M 2D 8H'),
    (90 * 24 * 3600, 'UP 3M'),
    (11 * 30 * 24 * 3600 + 29 * 24 * 3600 + 23 * 3600, 'UP 11M 29D 23H'),
    (30 * 24 * 3600 + 5 * 3600, 'UP 1M 5H'),
  ],
)
def test_fmt_uptime(seconds: int, expected: str) -> None:
  assert unraid._fmt_uptime(seconds) == expected


# ---------------------------------------------------------------------------
# get_variables
# ---------------------------------------------------------------------------


def test_get_variables_normal(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  data = _normal_data(uptime_secs=3 * 30 * 24 * 3600 + 2 * 24 * 3600 + 8 * 3600)
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)):
    result = unraid.get_variables()

  assert result['header'] == [['[O] UNRAID']]
  assert result['capacity'] == [['14.2 / 20 TB']]
  # Uptime computed from boot timestamp delta — allow ±1 hour of rounding.
  uptime_str = result['uptime'][0][0]
  assert uptime_str.startswith('UP 3M 2D')
  unraid._cache = None


def test_get_variables_array_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  data = _normal_data(state='STOPPED')
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)):
    result = unraid.get_variables()

  assert result['capacity'] == [['[R] STOPPED']]
  unraid._cache = None


def test_get_variables_array_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  data = _normal_data(state='DEGRADED')
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)):
    result = unraid.get_variables()

  assert result['capacity'] == [['[R] DEGRADED']]
  unraid._cache = None


def test_get_variables_graphql_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  resp = _graphql_response(errors=[{'message': 'not authorized'}])
  with patch('integrations.unraid.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError, match='GraphQL error'):
      unraid.get_variables()

  unraid._cache = None


def test_get_variables_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  with patch(
    'integrations.unraid.fetch_with_retry',
    side_effect=_requests.ConnectionError('refused'),
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='API request failed'):
      unraid.get_variables()

  unraid._cache = None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  cached_value: dict = {
    'header': [['[O] UNRAID']],
    'capacity': [['14 / 20 TB']],
    'uptime': [['UP 3D 8H']],
  }
  unraid._cache = CacheEntry(cached_value)

  with patch('integrations.unraid.fetch_with_retry') as mock_fetch:
    result = unraid.get_variables()
    mock_fetch.assert_not_called()

  assert result == cached_value
  unraid._cache = None


def test_stale_cache_served_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  stale_value: dict = {
    'header': [['[O] UNRAID']],
    'capacity': [['10 / 20 TB']],
    'uptime': [['UP 1M']],
  }
  unraid._cache = CacheEntry(stale_value)
  unraid._cache.cached_at = time.monotonic() - unraid._CACHE_TTL - 1

  with patch(
    'integrations.unraid.fetch_with_retry',
    side_effect=_requests.ConnectionError('refused'),
  ):
    result = unraid.get_variables()

  assert result == stale_value
  unraid._cache = None


# ---------------------------------------------------------------------------
# verify_tls
# ---------------------------------------------------------------------------


def test_verify_tls_false_passes_verify(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  cfg = _patched_config()
  cfg['unraid']['verify_tls'] = False
  monkeypatch.setattr(_cfg, '_config', cfg)
  unraid._cache = None

  data = _normal_data()
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)) as mock_fetch:
    unraid.get_variables()

  assert mock_fetch.call_args.kwargs['verify'] is False
  unraid._cache = None


def test_verify_tls_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  data = _normal_data()
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)) as mock_fetch:
    unraid.get_variables()

  assert mock_fetch.call_args.kwargs['verify'] is True
  unraid._cache = None


# ---------------------------------------------------------------------------
# Uptime ISO timestamp parsing
# ---------------------------------------------------------------------------


def test_uptime_iso_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
  """The API returns a boot timestamp, not seconds — verify parsing works."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  data = _normal_data(uptime_secs=3600)
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)):
    result = unraid.get_variables()

  uptime_str = result['uptime'][0][0]
  assert uptime_str.startswith('UP ')
  assert 'H' in uptime_str
  unraid._cache = None


def test_uptime_unparseable_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
  """Unparseable uptime value should produce an empty line, not crash."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  unraid._cache = None

  data = {
    'info': {'os': {'uptime': 'not-a-timestamp'}},
    'array': {
      'state': 'STARTED',
      'capacity': {'kilobytes': {'used': '1000000000', 'total': '2000000000'}},
    },
  }
  with patch('integrations.unraid.fetch_with_retry', return_value=_graphql_response(data)):
    result = unraid.get_variables()

  assert result['uptime'] == [['']]
  unraid._cache = None
