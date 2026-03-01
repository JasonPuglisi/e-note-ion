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
#   city   — City name, optionally with a state or country suffix to
#             disambiguate (e.g. "Santa Clara, CA" or "Paris, FR").
#             US state abbreviations (CA, NY, TX, …) narrow results to the
#             United States; ISO 3166-1 alpha-2 country codes narrow results
#             to that country. The suffix is stripped before querying the API.
#
# Optional config.toml keys:
#   units  — "imperial" (°F, mph, default) or "metric" (°C, km/h)

from typing import Any

import requests

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, user_agent

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

# US state/territory abbreviations — used to detect "City, ST" notation and
# narrow geocoding requests to the United States.
_US_STATE_CODES: frozenset[str] = frozenset(
  [
    'AL',
    'AK',
    'AZ',
    'AR',
    'CA',
    'CO',
    'CT',
    'DE',
    'FL',
    'GA',
    'HI',
    'ID',
    'IL',
    'IN',
    'IA',
    'KS',
    'KY',
    'LA',
    'ME',
    'MD',
    'MA',
    'MI',
    'MN',
    'MS',
    'MO',
    'MT',
    'NE',
    'NV',
    'NH',
    'NJ',
    'NM',
    'NY',
    'NC',
    'ND',
    'OH',
    'OK',
    'OR',
    'PA',
    'RI',
    'SC',
    'SD',
    'TN',
    'TX',
    'UT',
    'VT',
    'VA',
    'WA',
    'WV',
    'WI',
    'WY',
    'DC',
  ]
)

# Module-level geocoding cache: (latitude, longitude, canonical_city_name).
# None = not yet populated.
_geocode_cache: tuple[float, float, str] | None = None

# Last-known-good cache for forecast data. Served on transient API failures
# if the entry is within _FORECAST_CACHE_TTL seconds of its fetch time.
_forecast_cache: CacheEntry | None = None
_FORECAST_CACHE_TTL = 4 * 3600  # 4 hours


def _parse_city_config(city_config: str) -> tuple[str, str | None]:
  """Parse a city config string into (query_name, country_code).

  Handles "City, ST" (US state) and "City, CC" (ISO country code) notation.
  The suffix is stripped from the query sent to the geocoding API; the country
  code (if any) is passed separately to narrow results.

  Examples:
    "Santa Clara, CA" -> ("Santa Clara", "US")
    "Paris, FR"       -> ("Paris", "FR")
    "London"          -> ("London", None)
  """
  if ',' not in city_config:
    return city_config.strip(), None
  city, suffix = city_config.split(',', 1)
  suffix = suffix.strip().upper()
  if suffix in _US_STATE_CODES:
    return city.strip(), 'US'
  if len(suffix) == 2:
    # Treat as an ISO 3166-1 alpha-2 country code.
    return city.strip(), suffix
  # Unrecognised suffix (e.g. full country name) — pass through as-is.
  return city_config.strip(), None


def _geocode(city_query: str, country_code: str | None) -> tuple[float, float, str]:
  """Resolve a city name to (latitude, longitude, canonical_name).

  Uses the Open-Meteo geocoding API. Raises IntegrationDataUnavailableError
  if the city cannot be resolved or the request fails.

  When country_code is provided, count=2 is used instead of count=1 to work
  around an Open-Meteo API bug where count=1 + countryCode sometimes returns
  empty results even when a match exists.
  """
  count = 2 if country_code else 1
  params: dict[str, str | int] = {'name': city_query, 'count': count, 'format': 'json'}
  if country_code:
    params['countryCode'] = country_code
  try:
    r = fetch_with_retry('GET', _GEOCODING_URL, params=params, headers={'User-Agent': user_agent()}, timeout=10)
  except requests.RequestException as e:
    print(f'Weather: geocoding request failed — {e}')
    raise IntegrationDataUnavailableError(f'Weather: geocoding request failed — {e}') from None
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    print(f'Weather: geocoding error {e.response.status_code} {e.response.reason}')
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

  On transient API failure, returns the last-known-good forecast if it is
  within _FORECAST_CACHE_TTL. Raises IntegrationDataUnavailableError on cold
  start or when the cache has expired.
  """
  global _geocode_cache, _forecast_cache

  import config as _config_mod

  city_config = _config_mod.get('weather', 'city')
  units = _config_mod.get_optional('weather', 'units') or 'imperial'

  if _geocode_cache is None:
    city_query, country_code = _parse_city_config(city_config)
    lat, lon, canonical_city = _geocode(city_query, country_code)
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

  try:
    r = fetch_with_retry(
      'GET',
      _FORECAST_URL,
      headers={'User-Agent': user_agent()},
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
    r.raise_for_status()
  except requests.RequestException as e:
    print(f'Weather: forecast error — {e}')
    if _forecast_cache is not None and _forecast_cache.is_valid(_FORECAST_CACHE_TTL):
      return _forecast_cache.value
    raise IntegrationDataUnavailableError(f'Weather: forecast error — {e}') from None

  data = r.json()
  current: dict[str, Any] = data['current']
  daily: dict[str, Any] = data['daily']

  wmo_code = int(current['weather_code'])
  condition_str, color_tag = _wmo_condition(wmo_code)
  condition = f'{color_tag} {condition_str}'

  precip_raw = current.get('precipitation_probability')
  precip = f'{round(precip_raw)}%' if precip_raw is not None else '0%'

  result = {
    'city': [[canonical_city]],
    'condition': [[condition]],
    'temp': [[_fmt_temp(current['temperature_2m'], units)]],
    'feels_like': [[_fmt_temp(current['apparent_temperature'], units)]],
    'high': [[str(round(daily['temperature_2m_max'][0]))]],
    'low': [[str(round(daily['temperature_2m_min'][0]))]],
    'wind': [[_fmt_wind(current['wind_speed_10m'], units)]],
    'precip': [[precip]],
  }
  _forecast_cache = CacheEntry(result)
  return result
