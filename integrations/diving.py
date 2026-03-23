# integrations/diving.py
#
# Scuba diving conditions integration.
#
# Fetches wave height, swell period, wind speed/direction, and water
# temperature for a configured dive site. The header row shows a color square
# reflecting subjective dive condition quality (green/yellow/red) scored from
# wave height and wind speed with a swell period modifier.
#
# Also provides a days-since-last-dive display (get_variables_last_dive) driven
# by a webhook that accepts {"dived_on": "YYYY-MM-DD"} and persists the date
# to config.toml. The last_dive template fires once daily and is silently
# skipped until a date has been recorded.
#
# Primary source: NOAA NDBC (National Data Buoy Center) — real measured buoy
# data, no API key, no quota. Recommended for US coastal dive sites. Find the
# nearest station at https://www.ndbc.noaa.gov/
#
# Fallback source: Open-Meteo Marine + Forecast APIs — free, no key, global,
# lat/lon configurable. Open-Meteo's own docs warn that accuracy near complex
# coastlines is limited (8 km grid). Use NDBC where a nearby buoy exists.
#
# Required config.toml keys ([diving]):
#   ndbc_station_id  — NDBC buoy station ID (e.g. "46042" for Monterey);
#                      required unless latitude and longitude are set instead.
#   latitude         — decimal latitude for Open-Meteo fallback
#   longitude        — decimal longitude for Open-Meteo fallback
#
# Optional config.toml keys:
#   units         — "imperial" (ft, °F, default) or "metric" (m, °C).
#                   Wind is always displayed in knots regardless of this setting.
#   last_dived_on — YYYY-MM-DD date of the most recent dive, written by the
#                   webhook handler. Can also be set manually.

import logging
from datetime import date, datetime, timezone
from typing import Any

import requests

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, user_agent
from scheduler import WebhookMessage

logger = logging.getLogger(__name__)

_NDBC_BASE_URL = 'https://www.ndbc.noaa.gov/data/realtime2'
_MARINE_URL = 'https://marine-api.open-meteo.com/v1/marine'
_FORECAST_URL = 'https://api.open-meteo.com/v1/forecast'

# TTL matches NDBC's ~hourly update cadence.
_CACHE_TTL = 3600

_cache: CacheEntry | None = None

# Condition scoring thresholds based on recreational dive operator consensus.
# Wave height in feet, wind speed in knots.
_WAVE_GREEN_FT = 2.0
_WAVE_YELLOW_FT = 4.0
_WIND_GREEN_KT = 10.0
_WIND_YELLOW_KT = 20.0

# Period-to-height ratio below which a choppy sea bumps the rating one step
# worse (e.g. 3 ft waves at 5s period: ratio 1.7 < 2.0 → bump to red).
_PERIOD_RATIO_THRESHOLD = 2.0

_CARDINALS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']


def _degrees_to_cardinal(deg: float) -> str:
  """Convert a compass bearing in degrees to an 8-point cardinal abbreviation."""
  return _CARDINALS[round(deg / 45) % 8]


def _fmt_wave(m: float, units: str) -> str:
  """Format wave/swell height with unit suffix.

  Uses one decimal place below 10 and no decimal at or above 10 to keep the
  string within 6 characters (e.g. '9.9FT', '10FT', '1.2M', '10M').
  """
  if units == 'imperial':
    ft = m * 3.28084
    return f'{ft:.0f}FT' if ft >= 10 else f'{ft:.1f}FT'
  return f'{m:.0f}M' if m >= 10 else f'{m:.1f}M'


def _fmt_temp(c: float, units: str) -> str:
  """Format water temperature with unit suffix."""
  if units == 'imperial':
    return f'{round(c * 9 / 5 + 32)}F'
  return f'{round(c)}C'


def _fmt_wind_kt(ms: float) -> str:
  """Convert m/s wind speed to knots and format with KT suffix."""
  return f'{round(ms * 1.94384)}KT'


def _condition_color(
  wave_m: float | None,
  wind_ms: float | None,
  period_s: float | None,
) -> str:
  """Return a color tag reflecting dive condition quality.

  Scores wave height and wind speed independently, takes the worst result,
  then applies a period modifier: if swell period / wave height (ft) < 2,
  the rating is bumped one step worse (choppy, short-period seas are harder
  than the height alone suggests).

  Returns '[G]' (green), '[Y]' (yellow), or '[R]' (red). Falls back to
  '[Y]' when key data fields are unavailable.
  """
  if wave_m is None or wind_ms is None:
    return '[Y]'

  wave_ft = wave_m * 3.28084
  wind_kt = wind_ms * 1.94384

  wave_score = 0 if wave_ft <= _WAVE_GREEN_FT else (1 if wave_ft <= _WAVE_YELLOW_FT else 2)
  wind_score = 0 if wind_kt <= _WIND_GREEN_KT else (1 if wind_kt <= _WIND_YELLOW_KT else 2)
  score = max(wave_score, wind_score)

  if period_s is not None and wave_ft > 0 and period_s / wave_ft < _PERIOD_RATIO_THRESHOLD:
    score = min(score + 1, 2)

  return ('[G]', '[Y]', '[R]')[score]


def _parse_ndbc(text: str) -> dict[str, float | None]:
  """Parse NOAA NDBC standard meteorological text file.

  Extracts the most recent data row (last non-comment line). Fields with
  value 'MM' (NDBC's missing-data sentinel) are returned as None.

  Returns a dict with keys: wave_height_m, period_s, wind_speed_ms,
  wind_dir_deg, water_temp_c.
  """
  headers: list[str] | None = None
  last_row: list[str] | None = None

  for line in text.splitlines():
    if line.startswith('#'):
      if headers is None:
        # First comment line contains column names; strip the leading '#'.
        headers = line[1:].split()
      # Second comment line is units — skip.
      continue
    if line.strip():
      last_row = line.split()

  if headers is None or last_row is None:
    raise IntegrationDataUnavailableError('Dive conditions: could not parse NDBC data — unexpected file format')

  row = dict(zip(headers, last_row))

  def _field(key: str) -> float | None:
    val = row.get(key, 'MM')
    if val == 'MM':
      return None
    try:
      return float(val)
    except ValueError:
      return None

  return {
    'wave_height_m': _field('WVHT'),
    'period_s': _field('DPD'),
    'wind_speed_ms': _field('WSPD'),
    'wind_dir_deg': _field('WDIR'),
    'water_temp_c': _field('WTMP'),
  }


def _fetch_ndbc(station_id: str) -> dict[str, float | None]:
  """Fetch and parse the NDBC standard meteorological file for a station."""
  url = f'{_NDBC_BASE_URL}/{station_id}.txt'
  try:
    r = fetch_with_retry('GET', url, headers={'User-Agent': user_agent()}, timeout=10)
    r.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'Dive conditions: NDBC request failed — {e}') from None
  logger.debug('Dive conditions: fetched NDBC station %s', station_id)
  return _parse_ndbc(r.text)


def _fetch_openmeteo(lat: float, lon: float) -> dict[str, float | None]:
  """Fetch wave, temp, and wind data from Open-Meteo (fallback for non-NDBC).

  Makes two requests: Marine API for wave height, period, and sea surface
  temperature; Forecast API for wind speed and direction.
  """

  def _opt(data: dict[str, Any], key: str) -> float | None:
    val = data.get(key)
    return float(val) if val is not None else None

  try:
    r_marine = fetch_with_retry(
      'GET',
      _MARINE_URL,
      headers={'User-Agent': user_agent()},
      params={
        'latitude': lat,
        'longitude': lon,
        'current': 'wave_height,wave_period,sea_surface_temperature',
        'cell_selection': 'sea',
      },
      timeout=10,
    )
    r_marine.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'Dive conditions: Open-Meteo marine request failed — {e}') from None

  try:
    r_forecast = fetch_with_retry(
      'GET',
      _FORECAST_URL,
      headers={'User-Agent': user_agent()},
      params={
        'latitude': lat,
        'longitude': lon,
        'current': 'wind_speed_10m,wind_direction_10m',
        # Default wind unit is km/h; convert to m/s in _fmt_wind_kt.
      },
      timeout=10,
    )
    r_forecast.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'Dive conditions: Open-Meteo forecast request failed — {e}') from None

  marine = r_marine.json().get('current', {})
  forecast = r_forecast.json().get('current', {})

  # Open-Meteo returns wind in km/h by default; convert to m/s.
  wind_kmh = _opt(forecast, 'wind_speed_10m')
  wind_ms = wind_kmh / 3.6 if wind_kmh is not None else None

  logger.debug('Dive conditions: fetched Open-Meteo data at (%.4f, %.4f)', lat, lon)
  return {
    'wave_height_m': _opt(marine, 'wave_height'),
    'period_s': _opt(marine, 'wave_period'),
    'wind_speed_ms': wind_ms,
    'wind_dir_deg': _opt(forecast, 'wind_direction_10m'),
    'water_temp_c': _opt(marine, 'sea_surface_temperature'),
  }


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch dive conditions and return a variables dict for template rendering.

  Returns keys: header, swell, wind.
    header — color square (condition quality) + water temperature
    swell  — wave height and dominant period
    wind   — wind speed (kt) and direction

  Uses NDBC if ndbc_station_id is configured, otherwise Open-Meteo (lat/lon).
  Results are cached for _CACHE_TTL seconds. On transient failure, the last
  cached result is returned if still valid; otherwise raises
  IntegrationDataUnavailableError.
  """
  global _cache

  import config as _config_mod

  station_id = _config_mod.get_optional('diving', 'ndbc_station_id')
  units = _config_mod.get_optional('diving', 'units') or 'imperial'

  if _cache is not None and _cache.is_valid(_CACHE_TTL):
    logger.debug('Dive conditions: cache hit')
    return _cache.value

  try:
    if station_id:
      data = _fetch_ndbc(station_id)
    else:
      try:
        lat = float(_config_mod.get('diving', 'latitude'))
        lon = float(_config_mod.get('diving', 'longitude'))
      except ValueError, KeyError:
        raise IntegrationDataUnavailableError(
          'Dive conditions: set ndbc_station_id or latitude/longitude in config.toml'
        )
      data = _fetch_openmeteo(lat, lon)
  except IntegrationDataUnavailableError:
    if _cache is not None:
      logger.warning('Dive conditions: fetch failed — serving stale cache')
      return _cache.value
    raise

  color = _condition_color(data['wave_height_m'], data['wind_speed_ms'], data['period_s'])

  # Header row: condition color + water temperature.
  temp = _fmt_temp(data['water_temp_c'], units) if data['water_temp_c'] is not None else '--'
  header = f'{color} WATER {temp}'

  # Swell row: wave height and dominant period.
  wave = _fmt_wave(data['wave_height_m'], units) if data['wave_height_m'] is not None else '--'
  period = f'{round(data["period_s"])}S' if data['period_s'] is not None else '--'
  swell = f'SWELL {wave} {period}'

  # Wind row: speed in knots and cardinal direction.
  wind_spd = _fmt_wind_kt(data['wind_speed_ms']) if data['wind_speed_ms'] is not None else '--'
  wind_dir = _degrees_to_cardinal(data['wind_dir_deg']) if data['wind_dir_deg'] is not None else '--'
  wind = f'WIND {wind_spd} {wind_dir}'

  result: dict[str, list[list[str]]] = {
    'header': [[header]],
    'swell': [[swell]],
    'wind': [[wind]],
  }
  _cache = CacheEntry(result)
  return result


def get_variables_last_dive() -> dict[str, list[list[str]]]:
  """Return days since the last dive for template rendering.

  Reads last_dived_on (YYYY-MM-DD) from [diving] in config.toml.
  Raises IntegrationDataUnavailableError if the key is absent or malformed,
  which causes the cron slot to be silently skipped until a date is recorded.

  Returns key: days_ago — 'TODAY', '1 DAY AGO', or 'N DAYS AGO'.
  """
  import config as _config_mod

  raw = _config_mod.get_optional('diving', 'last_dived_on')
  if not raw:
    raise IntegrationDataUnavailableError('Dive conditions: last_dived_on not set — send a webhook to record a dive')

  try:
    last_dive = date.fromisoformat(raw)
  except ValueError:
    raise IntegrationDataUnavailableError(f'Dive conditions: last_dived_on {raw!r} is not a valid YYYY-MM-DD date')

  today = datetime.now(timezone.utc).date()
  days = max(0, (today - last_dive).days)

  if days == 0:
    days_ago = 'TODAY'
  elif days == 1:
    days_ago = '1 DAY AGO'
  else:
    days_ago = f'{days} DAYS AGO'

  return {'days_ago': [[days_ago]]}


def handle_webhook(payload: dict[str, Any], credential_name: str | None = None) -> WebhookMessage | None:
  """Record the date of the most recent dive from a webhook payload.

  Accepts {"dived_on": "YYYY-MM-DD"}. Persists the date to config.toml under
  [diving] last_dived_on. Returns None — display is driven by the
  daily cron template, not the webhook itself.
  """
  import config as _config_mod

  dived_on = payload.get('dived_on')
  if not dived_on or not isinstance(dived_on, str):
    logger.warning('Dive conditions webhook: missing or non-string dived_on field')
    return None

  try:
    date.fromisoformat(dived_on)
  except ValueError:
    logger.warning('Dive conditions webhook: invalid dived_on %r — expected YYYY-MM-DD', dived_on)
    return None

  _config_mod.write_config_section('diving', {'last_dived_on': dived_on})
  logger.info('Dive conditions: recorded last dive date %s (credential=%r)', dived_on, credential_name)
  return None
