# integrations/morning.py
#
# Morning visual integration — weather-based 7×3 color grid.
#
# Returns a 7-cell-wide color grid for each of the 3 display rows,
# reflecting current weather conditions when the weather integration is
# configured. Falls back to a default sunrise visual when weather is
# unavailable or not configured.
#
# The grid occupies the left 7 columns across all 3 rows, matching the
# good_night moon layout convention (visual left, text right).
#
# No config.toml keys required. When [weather] is present, the current
# WMO weather code drives the visual; otherwise the sunrise grid is used.

import logging

logger = logging.getLogger(__name__)

# 7×3 [color] grids for each weather condition group.
# Each entry is (r1, r2, r3) — three 7-cell color-tag strings.
_GRIDS: dict[str, tuple[str, str, str]] = {
  'CLEAR': (
    '[K][K][K][Y][K][K][K]',
    '[K][O][Y][Y][Y][O][K]',
    '[O][Y][Y][Y][Y][Y][O]',
  ),
  'PARTLY': (
    '[W][W][K][K][K][W][W]',
    '[K][O][Y][Y][Y][O][K]',
    '[O][Y][Y][Y][Y][Y][O]',
  ),
  'CLOUDY': (
    '[K][W][W][W][W][W][K]',
    '[W][W][W][W][W][W][W]',
    '[W][W][W][W][W][W][W]',
  ),
  'RAIN_LIGHT': (
    '[B][K][B][K][B][K][B]',
    '[K][B][K][B][K][B][K]',
    '[B][K][B][K][B][K][B]',
  ),
  'RAIN_HEAVY': (
    '[B][B][K][B][B][K][B]',
    '[B][K][B][B][K][B][B]',
    '[K][B][B][K][B][B][K]',
  ),
  'SNOW': (
    '[W][K][W][K][W][K][W]',
    '[K][W][K][W][K][W][K]',
    '[W][K][W][K][W][K][W]',
  ),
  'STORM': (
    '[R][R][K][R][K][R][R]',
    '[R][K][R][R][R][K][R]',
    '[K][R][R][K][R][R][K]',
  ),
}

_DEFAULT = 'CLEAR'

# Maps condition strings (as returned by integrations.weather._WMO_CONDITIONS)
# to grid keys. Covers all known WMO condition strings.
_CONDITION_MAP: dict[str, str] = {
  'CLEAR': 'CLEAR',
  'MOSTLY CLEAR': 'CLEAR',
  'PARTLY CLOUDY': 'PARTLY',
  'OVERCAST': 'CLOUDY',
  'FOG': 'CLOUDY',
  'RIME FOG': 'CLOUDY',
  'LIGHT DRIZZLE': 'RAIN_LIGHT',
  'DRIZZLE': 'RAIN_LIGHT',
  'HEAVY DRIZZLE': 'RAIN_HEAVY',
  'FRZ DRIZZLE': 'RAIN_LIGHT',
  'HVY FRZ DRZL': 'RAIN_HEAVY',
  'LIGHT RAIN': 'RAIN_LIGHT',
  'RAIN': 'RAIN_HEAVY',
  'HEAVY RAIN': 'RAIN_HEAVY',
  'FRZ RAIN': 'RAIN_LIGHT',
  'HVY FRZ RAIN': 'RAIN_HEAVY',
  'LIGHT SNOW': 'SNOW',
  'SNOW': 'SNOW',
  'HEAVY SNOW': 'SNOW',
  'SNOW GRAINS': 'SNOW',
  'LIGHT SHOWERS': 'RAIN_LIGHT',
  'SHOWERS': 'RAIN_HEAVY',
  'HEAVY SHOWERS': 'RAIN_HEAVY',
  'SNOW SHOWERS': 'SNOW',
  'HVY SNOW SHWR': 'SNOW',
  'THUNDERSTORM': 'STORM',
  'STORM + HAIL': 'STORM',
}


def _grid_key_from_weather() -> str:
  """Return a grid key derived from the current weather condition.

  Imports and calls integrations.weather.get_variables() at call time so
  the weather module's process-level forecast cache is reused if already
  populated. Returns _DEFAULT on any failure (unavailable data, missing
  config section, import error).
  """
  try:
    import integrations.weather as weather_mod

    variables = weather_mod.get_variables()
    condition = variables['condition'][0][0]  # e.g. '[Y] CLEAR'
    # Strip the 3-char color tag and the following space: '[Y] CLEAR' → 'CLEAR'
    condition_str = condition[4:] if len(condition) > 4 else ''
    return _CONDITION_MAP.get(condition_str, _DEFAULT)
  except Exception:
    logger.debug('Morning: weather unavailable, using sunrise fallback')
    return _DEFAULT


def get_variables() -> dict[str, list[list[str]]]:
  key = _grid_key_from_weather()
  r1, r2, r3 = _GRIDS[key]
  return {
    'morning_r1': [[r1]],
    'morning_r2': [[r2]],
    'morning_r3': [[r3]],
  }
