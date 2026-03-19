from unittest.mock import patch

import pytest

import integrations.weather as weather_mod
from exceptions import IntegrationDataUnavailableError
from integrations.morning import (
  _CONDITION_MAP,
  _DEFAULT,
  _GRIDS,
  _RANDOM_GRIDS,
  _grid_key_from_weather,
  _random_grid,
  get_variables,
)

_COLOR_TAGS = {'[W]', '[K]', '[R]', '[O]', '[Y]', '[G]', '[B]', '[V]'}
_TAG_LEN = 3

_ALL_GRID_KEYS = set(_GRIDS) | set(_RANDOM_GRIDS)


def _count_visual_width(row: str) -> int:
  count = 0
  i = 0
  while i < len(row):
    if row[i] == '[' and row[i : i + _TAG_LEN] in _COLOR_TAGS:
      count += 1
      i += _TAG_LEN
    else:
      i += 1
  return count


def _count_color_cells(grid: tuple[str, str, str], color: str) -> int:
  tag = f'[{color}]'
  return sum(row.count(tag) for row in grid)


# --- Grid shape ---


def test_all_static_grids_are_seven_wide() -> None:
  for key, (r1, r2, r3) in _GRIDS.items():
    for row in (r1, r2, r3):
      width = _count_visual_width(row)
      assert width == 7, f'{key}: row {row!r} has width {width}, expected 7'


def test_default_grid_exists() -> None:
  assert _DEFAULT in _ALL_GRID_KEYS


def test_all_condition_map_values_are_valid_grid_keys() -> None:
  for condition, key in _CONDITION_MAP.items():
    assert key in _ALL_GRID_KEYS, f'{condition!r} maps to unknown grid key {key!r}'


# --- Random grid generation ---


@pytest.mark.parametrize('key,config', _RANDOM_GRIDS.items())
def test_random_grid_is_seven_wide(key: str, config: tuple[str, float, int]) -> None:
  color, density, min_cells = config
  grid = _random_grid(color, density, min_cells)
  for row in grid:
    width = _count_visual_width(row)
    assert width == 7, f'{key}: row {row!r} has width {width}, expected 7'


@pytest.mark.parametrize('key,config', _RANDOM_GRIDS.items())
def test_random_grid_meets_minimum_cells(key: str, config: tuple[str, float, int]) -> None:
  color, density, min_cells = config
  for _ in range(20):
    grid = _random_grid(color, density, min_cells)
    count = _count_color_cells(grid, color)
    assert count >= min_cells, f'{key}: got {count} cells, expected >= {min_cells}'


def test_random_grid_uses_only_specified_color_and_black() -> None:
  grid = _random_grid('B', 0.5, 4)
  for row in grid:
    stripped = row.replace('[B]', '').replace('[K]', '')
    assert stripped == '', f'unexpected tags in row: {row!r}'


# --- Condition mapping ---


@pytest.mark.parametrize(
  'condition,expected_key',
  [
    ('[Y] CLEAR', 'CLEAR'),
    ('[Y] MOSTLY CLEAR', 'CLEAR'),
    ('[O] PARTLY CLOUDY', 'PARTLY'),
    ('[W] OVERCAST', 'CLOUDY'),
    ('[W] FOG', 'CLOUDY'),
    ('[W] RIME FOG', 'CLOUDY'),
    ('[B] LIGHT DRIZZLE', 'RAIN_LIGHT'),
    ('[B] DRIZZLE', 'RAIN_LIGHT'),
    ('[B] LIGHT RAIN', 'RAIN_LIGHT'),
    ('[B] RAIN', 'RAIN_HEAVY'),
    ('[B] HEAVY RAIN', 'RAIN_HEAVY'),
    ('[W] LIGHT SNOW', 'SNOW'),
    ('[W] SNOW', 'SNOW'),
    ('[W] HEAVY SNOW', 'SNOW'),
    ('[R] THUNDERSTORM', 'STORM'),
    ('[R] STORM + HAIL', 'STORM'),
  ],
)
def test_grid_key_from_weather_conditions(condition: str, expected_key: str) -> None:
  with patch.object(weather_mod, 'get_variables', return_value={'condition': [[condition]]}):
    assert _grid_key_from_weather() == expected_key


def test_grid_key_fallback_on_data_unavailable() -> None:
  with patch.object(weather_mod, 'get_variables', side_effect=IntegrationDataUnavailableError('no data')):
    assert _grid_key_from_weather() == _DEFAULT


def test_grid_key_fallback_on_general_exception() -> None:
  with patch.object(weather_mod, 'get_variables', side_effect=Exception('config missing')):
    assert _grid_key_from_weather() == _DEFAULT


def test_grid_key_fallback_on_unknown_condition() -> None:
  with patch.object(weather_mod, 'get_variables', return_value={'condition': [['[K] UNKNOWN CONDITION']]}):
    assert _grid_key_from_weather() == _DEFAULT


# --- get_variables shape ---


def test_get_variables_shape() -> None:
  result = get_variables()
  assert set(result.keys()) == {'morning_r1', 'morning_r2', 'morning_r3'}
  for key in ('morning_r1', 'morning_r2', 'morning_r3'):
    assert isinstance(result[key], list)
    assert len(result[key]) == 1
    assert len(result[key][0]) == 1
    assert isinstance(result[key][0][0], str)


def test_get_variables_rows_are_seven_wide() -> None:
  result = get_variables()
  for key in ('morning_r1', 'morning_r2', 'morning_r3'):
    row = result[key][0][0]
    width = _count_visual_width(row)
    assert width == 7, f'{key}: expected 7-wide row, got {row!r}'
