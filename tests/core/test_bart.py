from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.bart as bart
import integrations.vestaboard as vb


@pytest.fixture(autouse=True)
def reset_color_cache() -> Generator[None, None, None]:
  """Reset the module-level route color cache before each test."""
  bart._dest_color_cache = None
  yield
  bart._dest_color_cache = None  # type: ignore[assignment]


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
  with patch('integrations.bart.requests.get', side_effect=[_mock_routes_empty(), _mock_etd()]):
    result = bart.get_variables()
  assert 'station' in result
  assert 'line1' in result
  assert result['station'][0][0] == 'Milpitas'


def test_get_variables_no_service_when_dest_not_in_etd(bart_config: None) -> None:
  mock_etd = MagicMock()
  mock_etd.raise_for_status.return_value = None
  mock_etd.json.return_value = {'root': {'station': [{'name': 'Milpitas', 'etd': []}]}}
  with patch('integrations.bart.requests.get', side_effect=[_mock_routes_empty(), mock_etd]):
    result = bart.get_variables()
  assert 'NO SERVICE' in result['line1'][0][0]


def test_get_variables_line1_fits_display(bart_config: None) -> None:
  with patch('integrations.bart.requests.get', side_effect=[_mock_routes_empty(), _mock_etd()]):
    result = bart.get_variables()
  line = result['line1'][0][0]
  assert vb.display_len(line) <= vb.model.cols


def test_get_variables_http_error_does_not_leak_key(bart_config: None) -> None:
  """HTTPError re-raise must not include the API key in the error message."""
  err_resp = MagicMock()
  err_resp.status_code = 503
  err_resp.reason = 'Service Unavailable'
  mock_routes = _mock_routes_empty()
  mock_etd = MagicMock()
  mock_etd.raise_for_status.side_effect = requests.HTTPError(response=err_resp)

  with patch('integrations.bart.requests.get', side_effect=[mock_routes, mock_etd]):
    with pytest.raises(requests.HTTPError) as exc_info:
      bart.get_variables()
  assert 'test-bart-key' not in str(exc_info.value)
