from unittest.mock import patch

import pytest

import integrations.weather as weather_mod
from exceptions import IntegrationDataUnavailableError
from integrations.morning import _CONDITION_MAP, _DEFAULT, _GRIDS, _grid_key_from_weather, get_variables

_COLOR_TAGS = {'[W]', '[K]', '[R]', '[O]', '[Y]', '[G]', '[B]', '[V]'}
_TAG_LEN = 3


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


# --- Grid shape ---


def test_all_grids_are_seven_wide() -> None:
  for key, (r1, r2, r3) in _GRIDS.items():
    for row in (r1, r2, r3):
      width = _count_visual_width(row)
      assert width == 7, f'{key}: row {row!r} has width {width}, expected 7'


def test_default_grid_exists() -> None:
  assert _DEFAULT in _GRIDS


def test_all_condition_map_values_are_valid_grid_keys() -> None:
  for condition, key in _CONDITION_MAP.items():
    assert key in _GRIDS, f'{condition!r} maps to unknown grid key {key!r}'


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
