# integrations/weather.py
#
# Current weather conditions integration via Open-Meteo.
#
# Fetches current weather for a configured city using the Open-Meteo forecast
# API (no API key required). The city is forward-geocoded to coordinates on
# first call using the Open-Meteo geocoding API; both the coordinates and the
# canonical city name from the API response are cached for the process lifetime.
#
# Required config.toml keys ([weather]):
#   city   — City name (e.g. "San Francisco"); geocoded on first call
#
# Optional config.toml keys:
#   units  — "imperial" (°F, mph, default) or "metric" (°C, km/h)

from typing import Any

import requests

from exceptions import IntegrationDataUnavailableError

_GEOCODING_URL = 'https://geocoding-api.open-meteo.com/v1/search'
_FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'

# WMO weather interpretation codes → (condition string, color tag).
# Condition strings are kept ≤ 13 chars so they fit within Note's 15 cols
# after a leading color tag and space (e.g. "[Y] MOSTLY CLEAR" = 16 chars —
# see note below; color tags render as 1 display char).
_WMO_CONDITIONS: dict[int, tuple[str, str]] = {
  0: ('CLEAR', '[Y]'),
  1: ('MOSTLY CLEAR', '[Y]'),
  2: ('PARTLY CLOUDY', '[O]'),
  3: ('OVERCAST', '[W]'),
  45: ('FOG', '[W]'),
  48: ('RIME FOG', '[W]'),
  51: ('LIGHT DRIZZLE', '[B]'),
  53: ('DRIZZLE', '[B]'),
  55: ('HEAVY DRIZZLE', '[B]'),
  56: ('FRZ DRIZZLE', '[V]'),
  57: ('HVY FRZ DRZL', '[V]'),
  61: ('LIGHT RAIN', '[B]'),
  63: ('RAIN', '[B]'),
  65: ('HEAVY RAIN', '[B]'),
  66: ('FRZ RAIN', '[V]'),
  67: ('HVY FRZ RAIN', '[V]'),
  71: ('LIGHT SNOW', '[W]'),
  73: ('SNOW', '[W]'),
  75: ('HEAVY SNOW', '[W]'),
  77: ('SNOW GRAINS', '[W]'),
  80: ('LIGHT SHOWERS', '[B]'),
  81: ('SHOWERS', '[B]'),
  82: ('HEAVY SHOWERS', '[B]'),
  85: ('SNOW SHOWERS', '[W]'),
  86: ('HVY SNOW SHWR', '[W]'),
  95: ('THUNDERSTORM', '[R]'),
  96: ('STORM + HAIL', '[R]'),
  99: ('STORM + HAIL', '[R]'),
}

# Module-level geocoding cache: (latitude, longitude, canonical_city_name).
# None = not yet populated.
_geocode_cache: tuple[float, float, str] | None = None


def _geocode(city: str) -> tuple[float, float, str]:
  """Resolve a city name to (latitude, longitude, canonical_name).

  Uses the Open-Meteo geocoding API. Raises IntegrationDataUnavailableError
  if the city cannot be resolved.
  """
  r = requests.get(
    _GEOCODING_URL,
    params={'name': city, 'count': 1, 'format': 'json'},
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise IntegrationDataUnavailableError(
      f'Weather geocoding error: {e.response.status_code} {e.response.reason}'
    ) from None

  results = r.json().get('results', [])
  if not results:
    raise IntegrationDataUnavailableError('Weather: city not found — check the [weather] city setting in config.toml')

  loc = results[0]
  return float(loc['latitude']), float(loc['longitude']), str(loc['name'])


def _wmo_condition(code: int) -> tuple[str, str]:
  """Return (condition_string, color_tag) for a WMO weather code.

  Falls back to ('UNKNOWN', '[K]') for unrecognised codes.
  """
  return _WMO_CONDITIONS.get(code, ('UNKNOWN', '[K]'))


def _fmt_temp(value: float, units: str) -> str:
  """Format a temperature value with its unit suffix."""
  suffix = 'F' if units == 'imperial' else 'C'
  return f'{round(value)}{suffix}'


def _fmt_wind(value: float, units: str) -> str:
  """Format a wind speed value with its unit suffix."""
  suffix = 'MPH' if units == 'imperial' else 'KMH'
  return f'{round(value)}{suffix}'


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch current weather and return a variables dict for template rendering.

  Returns keys: city, condition, temp, feels_like, high, low, wind, precip.
  Each value is a single-option list (no randomness — data is always current).

  The geocoding result is cached in-process on first call. The canonical city
  name from the API response is always used for the {city} variable, regardless
  of what was typed in config.toml.
  """
  global _geocode_cache

  import config as _config_mod

  city_config = _config_mod.get('weather', 'city')
  units = _config_mod.get_optional('weather', 'units') or 'imperial'

  if _geocode_cache is None:
    lat, lon, canonical_city = _geocode(city_config)
    _geocode_cache = (lat, lon, canonical_city)
  else:
    lat, lon, canonical_city = _geocode_cache

  # Select unit system parameters for Open-Meteo.
  if units == 'imperial':
    temp_unit = 'fahrenheit'
    wind_unit = 'mph'
  else:
    temp_unit = 'celsius'
    wind_unit = 'kmh'

  r = requests.get(
    _FORECAST_URL,
    params={
      'latitude': lat,
      'longitude': lon,
      'current': ','.join(
        [
          'temperature_2m',
          'apparent_temperature',
          'weather_code',
          'wind_speed_10m',
          'precipitation_probability',
        ]
      ),
      'daily': 'temperature_2m_max,temperature_2m_min',
      'temperature_unit': temp_unit,
      'wind_speed_unit': wind_unit,
      'forecast_days': 1,
      'timezone': 'auto',
    },
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Weather forecast error: {e.response.status_code} {e.response.reason}') from None

  data = r.json()
  current: dict[str, Any] = data['current']
  daily: dict[str, Any] = data['daily']

  wmo_code = int(current['weather_code'])
  condition_str, color_tag = _wmo_condition(wmo_code)
  condition = f'{color_tag} {condition_str}'

  precip_raw = current.get('precipitation_probability')
  precip = f'{round(precip_raw)}%' if precip_raw is not None else '0%'

  return {
    'city': [[canonical_city]],
    'condition': [[condition]],
    'temp': [[_fmt_temp(current['temperature_2m'], units)]],
    'feels_like': [[_fmt_temp(current['apparent_temperature'], units)]],
    'high': [[_fmt_temp(daily['temperature_2m_max'][0], units)]],
    'low': [[_fmt_temp(daily['temperature_2m_min'][0], units)]],
    'wind': [[_fmt_wind(current['wind_speed_10m'], units)]],
    'precip': [[precip]],
  }
