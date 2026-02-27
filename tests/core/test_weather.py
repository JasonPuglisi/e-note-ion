from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.vestaboard as vb
import integrations.weather as weather
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def reset_geocode_cache() -> Generator[None, None, None]:
  """Reset the module-level geocode cache before each test."""
  weather._geocode_cache = None
  yield
  weather._geocode_cache = None  # type: ignore[assignment]


@pytest.fixture()
def weather_config_imperial(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with imperial weather settings."""
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'weather': {'city': 'san francisco', 'units': 'imperial'}},
  )


@pytest.fixture()
def weather_config_metric(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with metric weather settings."""
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'weather': {'city': 'London', 'units': 'metric'}},
  )


@pytest.fixture()
def weather_config_no_units(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with no units key (should default to imperial)."""
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'weather': {'city': 'Chicago'}},
  )


def _mock_geocode(name: str = 'San Francisco') -> MagicMock:
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {'results': [{'latitude': 37.7749, 'longitude': -122.4194, 'name': name}]}
  return mock


def _mock_forecast(
  temp: float = 72.0,
  feels_like: float = 70.0,
  wmo_code: int = 0,
  wind: float = 10.0,
  precip: float = 5.0,
  high: float = 75.0,
  low: float = 60.0,
) -> MagicMock:
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {
    'current': {
      'temperature_2m': temp,
      'apparent_temperature': feels_like,
      'weather_code': wmo_code,
      'wind_speed_10m': wind,
      'precipitation_probability': precip,
    },
    'daily': {
      'temperature_2m_max': [high],
      'temperature_2m_min': [low],
    },
  }
  return mock


# --- get_variables: imperial ---


def test_get_variables_imperial_returns_expected_keys(weather_config_imperial: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast()]):
    result = weather.get_variables()
  assert set(result.keys()) == {'city', 'condition', 'temp', 'feels_like', 'high', 'low', 'wind', 'precip'}


def test_get_variables_imperial_temp_uses_f_suffix(weather_config_imperial: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast(temp=72.4)]):
    result = weather.get_variables()
  assert result['temp'][0][0] == '72F'


def test_get_variables_imperial_wind_uses_mph(weather_config_imperial: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast(wind=12.0)]):
    result = weather.get_variables()
  assert result['wind'][0][0] == '12MPH'


def test_get_variables_imperial_high_low_format(weather_config_imperial: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast(high=75.0, low=59.0)]):
    result = weather.get_variables()
  assert result['high'][0][0] == '75'
  assert result['low'][0][0] == '59'


# --- get_variables: metric ---


def test_get_variables_metric_temp_uses_c_suffix(weather_config_metric: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode('London'), _mock_forecast(temp=22.0)]):
    result = weather.get_variables()
  assert result['temp'][0][0] == '22C'


def test_get_variables_metric_wind_uses_kmh(weather_config_metric: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode('London'), _mock_forecast(wind=20.0)]):
    result = weather.get_variables()
  assert result['wind'][0][0] == '20KMH'


# --- get_variables: default units ---


def test_get_variables_default_units_is_imperial(weather_config_no_units: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode('Chicago'), _mock_forecast(temp=68.0)]):
    result = weather.get_variables()
  assert result['temp'][0][0].endswith('F')


# --- canonical city name ---


def test_city_name_uses_api_canonical_name(weather_config_imperial: None) -> None:
  """Config has lowercase/typo city; {city} variable must use the API's canonical name."""
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode('San Francisco'), _mock_forecast()]):
    result = weather.get_variables()
  assert result['city'][0][0] == 'San Francisco'


# --- _parse_city_config ---


def test_parse_city_config_bare_city() -> None:
  assert weather._parse_city_config('San Francisco') == ('San Francisco', None)


def test_parse_city_config_us_state_code() -> None:
  city, cc = weather._parse_city_config('Santa Clara, CA')
  assert city == 'Santa Clara'
  assert cc == 'US'


def test_parse_city_config_iso_country_code() -> None:
  city, cc = weather._parse_city_config('Paris, FR')
  assert city == 'Paris'
  assert cc == 'FR'


def test_parse_city_config_unrecognised_suffix_passthrough() -> None:
  city, cc = weather._parse_city_config('Paris, France')
  assert city == 'Paris, France'
  assert cc is None


# --- geocoding cache ---


def test_geocoding_cached_after_first_call(weather_config_imperial: None) -> None:
  """Geocoding endpoint should only be called once across multiple get_variables() calls."""
  with patch(
    'integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast(), _mock_forecast()]
  ) as mock_get:
    weather.get_variables()
    weather.get_variables()
  # First call: 2 requests (geocode + forecast). Second call: 1 request (forecast only).
  assert mock_get.call_count == 3


def test_geocoding_uses_count2_with_country_code(monkeypatch: pytest.MonkeyPatch) -> None:
  """count=2 must be sent when a country code is present (Open-Meteo count=1+countryCode bug)."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'weather': {'city': 'Santa Clara, CA'}})
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast()]) as mock_get:
    weather.get_variables()
  geocode_call = mock_get.call_args_list[0]
  assert geocode_call.kwargs['params']['count'] == 2
  assert geocode_call.kwargs['params']['countryCode'] == 'US'


def test_geocoding_uses_count1_without_country_code(weather_config_imperial: None) -> None:
  """count=1 must be used when no country code suffix is present."""
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), _mock_forecast()]) as mock_get:
    weather.get_variables()
  geocode_call = mock_get.call_args_list[0]
  assert geocode_call.kwargs['params']['count'] == 1
  assert 'countryCode' not in geocode_call.kwargs['params']


def test_geocoding_not_found_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'weather': {'city': 'Notaplace12345'}})
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.json.return_value = {'results': []}
  with patch('integrations.weather.requests.get', return_value=mock):
    with pytest.raises(IntegrationDataUnavailableError):
      weather.get_variables()


def test_geocoding_http_error_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'weather': {'city': 'San Francisco'}})
  err_resp = MagicMock()
  err_resp.status_code = 500
  err_resp.reason = 'Internal Server Error'
  mock = MagicMock()
  mock.raise_for_status.side_effect = requests.HTTPError(response=err_resp)
  with patch('integrations.weather.requests.get', return_value=mock):
    with pytest.raises(IntegrationDataUnavailableError):
      weather.get_variables()


# --- error safety ---


def test_forecast_http_error_raises_unavailable(weather_config_imperial: None) -> None:
  """Forecast HTTP error must raise IntegrationDataUnavailableError (not leak requests.HTTPError)."""
  err_resp = MagicMock()
  err_resp.status_code = 503
  err_resp.reason = 'Service Unavailable'
  mock_forecast = MagicMock()
  mock_forecast.raise_for_status.side_effect = requests.HTTPError(response=err_resp)

  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), mock_forecast]):
    with pytest.raises(IntegrationDataUnavailableError) as exc_info:
      weather.get_variables()
  assert 'san francisco' not in str(exc_info.value).lower()
  assert 'San Francisco' not in str(exc_info.value)


def test_geocoding_timeout_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'weather': {'city': 'San Francisco'}})
  with patch('integrations.weather.requests.get', side_effect=requests.Timeout):
    with pytest.raises(IntegrationDataUnavailableError):
      weather.get_variables()


def test_forecast_timeout_raises_unavailable(weather_config_imperial: None) -> None:
  with patch('integrations.weather.requests.get', side_effect=[_mock_geocode(), requests.Timeout]):
    with pytest.raises(IntegrationDataUnavailableError):
      weather.get_variables()


# --- WMO condition mapping ---


def test_get_variables_metric_high_low_no_unit(weather_config_metric: None) -> None:
  with patch(
    'integrations.weather.requests.get', side_effect=[_mock_geocode('London'), _mock_forecast(high=28.0, low=13.0)]
  ):
    result = weather.get_variables()
  assert result['high'][0][0] == '28'
  assert result['low'][0][0] == '13'


def test_get_variables_high_low_three_digit_fits_note_cols(weather_config_imperial: None) -> None:
  """Three-digit high/low temps must fit within Note's column width after rendering."""
  with patch(
    'integrations.weather.requests.get',
    side_effect=[_mock_geocode(), _mock_forecast(temp=102.0, high=108.0, low=85.0)],
  ):
    result = weather.get_variables()
  temp = result['temp'][0][0]
  high = result['high'][0][0]
  low = result['low'][0][0]
  rendered = f'{temp} H:{high} L:{low}'
  assert vb.display_len(rendered) <= vb.VestaboardModel.NOTE.cols, (
    f'{rendered!r} is {vb.display_len(rendered)} chars, exceeds {vb.VestaboardModel.NOTE.cols}'
  )


def test_wmo_condition_clear() -> None:
  condition, tag = weather._wmo_condition(0)
  assert condition == 'CLEAR'
  assert tag == '[Y]'


def test_wmo_condition_rain() -> None:
  condition, tag = weather._wmo_condition(63)
  assert condition == 'RAIN'
  assert tag == '[B]'


def test_wmo_condition_thunderstorm() -> None:
  condition, tag = weather._wmo_condition(95)
  assert condition == 'THUNDERSTORM'
  assert tag == '[R]'


def test_wmo_condition_snow() -> None:
  condition, tag = weather._wmo_condition(73)
  assert condition == 'SNOW'
  assert tag == '[W]'


def test_wmo_condition_unknown_code() -> None:
  condition, tag = weather._wmo_condition(999)
  assert condition == 'UNKNOWN'
  assert tag == '[K]'


def test_condition_string_fits_note_cols() -> None:
  """All condition strings, when prefixed with a color tag, must fit within Note's 15 cols."""
  for code, (condition_str, color_tag) in weather._WMO_CONDITIONS.items():
    full = f'{color_tag} {condition_str}'
    length = vb.display_len(full)
    assert length <= vb.VestaboardModel.NOTE.cols, (
      f'WMO {code}: {full!r} is {length} display chars, exceeds {vb.VestaboardModel.NOTE.cols}'
    )
