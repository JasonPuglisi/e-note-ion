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

import datetime
import logging
import random

from exceptions import IntegrationDataUnavailableError

logger = logging.getLogger(__name__)

_GRID_COLS = 7
_GRID_ROWS = 3
_GRID_CELLS = _GRID_COLS * _GRID_ROWS

# 7×3 [color] grids for static weather condition groups.
# Each entry is (r1, r2, r3) — three 7-cell color-tag strings.
_GRIDS: dict[str, tuple[str, str, str]] = {
  'CLEAR': (
    '[K][K][O][O][O][K][K]',
    '[K][O][Y][Y][Y][O][K]',
    '[O][Y][Y][Y][Y][Y][O]',
  ),
  'PARTLY': (
    '[W][W][O][O][O][W][W]',
    '[K][O][Y][Y][Y][O][K]',
    '[O][Y][Y][Y][Y][Y][O]',
  ),
  'CLOUDY': (
    '[K][W][W][W][W][W][K]',
    '[W][W][W][W][W][W][W]',
    '[K][W][W][W][W][W][K]',
  ),
}

# Random scatter grid config: (color_tag, density, min_cells).
# Each call to get_variables() generates a fresh random pattern.
_RANDOM_GRIDS: dict[str, tuple[str, float, int]] = {
  'RAIN_LIGHT': ('B', 0.30, 4),
  'RAIN_HEAVY': ('B', 0.55, 8),
  'SNOW': ('W', 0.25, 4),
  'STORM': ('R', 0.50, 7),
}


def _random_grid(
  color: str,
  density: float,
  min_cells: int,
) -> tuple[str, str, str]:
  """Generate a random 7×3 scatter grid with a minimum cell count."""
  cells = [random.random() < density for _ in range(_GRID_CELLS)]  # nosec B311
  filled = sum(cells)
  if filled < min_cells:
    empty = [i for i, c in enumerate(cells) if not c]
    random.shuffle(empty)
    for i in empty[: min_cells - filled]:
      cells[i] = True
  rows: list[str] = []
  for r in range(_GRID_ROWS):
    row = ''.join(f'[{color}]' if cells[r * _GRID_COLS + c] else '[K]' for c in range(_GRID_COLS))
    rows.append(row)
  return (rows[0], rows[1], rows[2])


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
  today = datetime.date.today()
  if today.month == 9 and today.day == 21:
    raise IntegrationDataUnavailableError('Sep 21 uses static template', expected=True)
  key = _grid_key_from_weather()
  if key in _RANDOM_GRIDS:
    color, density, min_cells = _RANDOM_GRIDS[key]
    r1, r2, r3 = _random_grid(color, density, min_cells)
  else:
    r1, r2, r3 = _GRIDS[key]
  return {
    'morning_r1': [[r1]],
    'morning_r2': [[r2]],
    'morning_r3': [[r3]],
  }
