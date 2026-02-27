from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.bart as bart
import integrations.vestaboard as vb
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def reset_caches() -> Generator[None, None, None]:
  """Reset module-level caches before each test."""
  bart._dest_color_cache = None
  bart._departures_cache = None
  yield
  bart._dest_color_cache = None  # type: ignore[assignment]
  bart._departures_cache = None


@pytest.fixture()
def bart_config(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config to provide BART settings without a real config file."""
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'bart': {'api_key': 'test-bart-key', 'station': 'MLPT', 'line1_dest': 'DALY'}},
  )


def _mock_routes_empty() -> MagicMock:
  """Routes API returning no routes (simplifies etd-only tests)."""
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {'root': {'routes': {'route': []}}}
  return mock


def _mock_etd(dest: str = 'DALY', minutes: str = '5', color: str = 'GREEN') -> MagicMock:
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {
    'root': {
      'station': [
        {
          'name': 'Milpitas',
          'etd': [{'abbreviation': dest, 'estimate': [{'minutes': minutes, 'color': color}]}],
        }
      ]
    }
  }
  return mock


# --- _format_minutes ---


def test_format_minutes_single_digit_zero_padded() -> None:
  assert bart._format_minutes('5') == '05'


def test_format_minutes_double_digit_unchanged() -> None:
  assert bart._format_minutes('12') == '12'


def test_format_minutes_leaving_returns_zero() -> None:
  assert bart._format_minutes('Leaving') == '00'


def test_format_minutes_zero_returns_zero() -> None:
  assert bart._format_minutes('0') == '00'


def test_format_minutes_non_numeric_passthrough() -> None:
  assert bart._format_minutes('unknown') == 'unknown'


# --- get_variables ---


def test_get_variables_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  def _raise(section: str, key: str) -> str:
    if section == 'bart' and key == 'api_key':
      raise ValueError('Missing required config key [bart].api_key in config.toml')
    return 'value'

  monkeypatch.setattr(_cfg, 'get', _raise)
  with pytest.raises(ValueError, match='api_key'):
    bart.get_variables()


def test_get_variables_missing_station(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  def _raise(section: str, key: str) -> str:
    if section == 'bart' and key == 'station':
      raise ValueError('Missing required config key [bart].station in config.toml')
    return 'value'

  monkeypatch.setattr(_cfg, 'get', _raise)
  with pytest.raises(ValueError, match='station'):
    bart.get_variables()


def test_get_variables_returns_station_and_line1(bart_config: None) -> None:
  with patch('integrations.bart.fetch_with_retry', side_effect=[_mock_routes_empty(), _mock_etd()]):
    result = bart.get_variables()
  assert 'station' in result
  assert 'line1' in result
  assert result['station'][0][0] == 'Milpitas'


def test_get_variables_no_service_when_dest_not_in_etd(bart_config: None) -> None:
  mock_etd = MagicMock()
  mock_etd.raise_for_status.return_value = None
  mock_etd.json.return_value = {'root': {'station': [{'name': 'Milpitas', 'etd': []}]}}
  with patch('integrations.bart.fetch_with_retry', side_effect=[_mock_routes_empty(), mock_etd]):
    result = bart.get_variables()
  assert 'NO SERVICE' in result['line1'][0][0]


def test_get_variables_line1_fits_display(bart_config: None) -> None:
  with patch('integrations.bart.fetch_with_retry', side_effect=[_mock_routes_empty(), _mock_etd()]):
    result = bart.get_variables()
  line = result['line1'][0][0]
  assert vb.display_len(line) <= vb.model.cols


def test_get_variables_http_error_does_not_leak_key(bart_config: None) -> None:
  """Error on departures fetch must not include the API key in the error message."""
  err_resp = MagicMock()
  err_resp.status_code = 503
  err_resp.reason = 'Service Unavailable'

  with patch(
    'integrations.bart.fetch_with_retry',
    side_effect=[_mock_routes_empty(), requests.HTTPError(response=err_resp)],
  ):
    with pytest.raises(IntegrationDataUnavailableError) as exc_info:
      bart.get_variables()
  assert 'test-bart-key' not in str(exc_info.value)


# --- departures cache ---


def test_departures_cache_hit_within_ttl_returns_cached_value(bart_config: None) -> None:
  """On API failure within TTL, cached departures are returned instead of raising."""
  with patch('integrations.bart.fetch_with_retry', side_effect=[_mock_routes_empty(), _mock_etd(minutes='05')]):
    bart.get_variables()

  with patch('integrations.bart.fetch_with_retry', side_effect=[requests.ConnectionError()]):
    result = bart.get_variables()

  assert '05' in result['line1'][0][0]


def test_departures_cache_expired_raises_unavailable(bart_config: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """On API failure with an expired cache, raises IntegrationDataUnavailableError."""
  import time

  with patch('integrations.bart.fetch_with_retry', side_effect=[_mock_routes_empty(), _mock_etd()]):
    bart.get_variables()

  assert bart._departures_cache is not None
  monkeypatch.setattr(bart._departures_cache, 'cached_at', time.monotonic() - bart._DEPARTURES_CACHE_TTL - 1)

  with patch('integrations.bart.fetch_with_retry', side_effect=[requests.ConnectionError()]):
    with pytest.raises(IntegrationDataUnavailableError):
      bart.get_variables()


def test_departures_cache_cold_start_raises_unavailable(bart_config: None) -> None:
  """With no cache and API down, raises IntegrationDataUnavailableError."""
  with patch(
    'integrations.bart.fetch_with_retry',
    side_effect=[_mock_routes_empty(), requests.ConnectionError()],
  ):
    with pytest.raises(IntegrationDataUnavailableError):
      bart.get_variables()


def test_departures_cache_updated_on_success(bart_config: None) -> None:
  """Successful fetch writes to the departures cache."""
  assert bart._departures_cache is None
  with patch('integrations.bart.fetch_with_retry', side_effect=[_mock_routes_empty(), _mock_etd()]):
    bart.get_variables()
  assert bart._departures_cache is not None
