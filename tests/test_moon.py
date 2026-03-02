from datetime import datetime

import pytest

from integrations.moon import _GRIDS, _phase, get_variables

# Reference dates chosen to fall clearly within each phase bucket.
# Verified against public lunar calendar data.
_PHASE_CASES = [
  # (iso_date_utc, expected_phase_name)
  # Dates chosen to fall clearly mid-bucket; actual new moon was 2024-01-11 11:57 UTC.
  ('2024-01-13T12:00:00Z', 'NEW MOON'),
  ('2024-01-16T12:00:00Z', 'WAXING CRESCENT'),
  ('2024-01-20T12:00:00Z', 'FIRST QUARTER'),
  ('2024-01-23T12:00:00Z', 'WAXING GIBBOUS'),
  ('2024-01-27T12:00:00Z', 'FULL MOON'),
  ('2024-01-31T12:00:00Z', 'WANING GIBBOUS'),
  ('2024-02-04T12:00:00Z', 'LAST QUARTER'),
  ('2024-02-08T12:00:00Z', 'WANING CRESCENT'),
]

_COLOR_TAGS = {'[W]', '[K]', '[R]', '[O]', '[Y]', '[G]', '[B]', '[V]'}
_TAG_LEN = 3  # each tag is exactly 3 characters


def _count_visual_width(row: str) -> int:
  """Count the number of color-tag characters in a moon row string."""
  count = 0
  i = 0
  while i < len(row):
    if row[i] == '[' and row[i : i + _TAG_LEN] in _COLOR_TAGS:
      count += 1
      i += _TAG_LEN
    else:
      i += 1
  return count


@pytest.mark.parametrize('iso_date,expected_phase', _PHASE_CASES)
def test_phase_name(iso_date: str, expected_phase: str) -> None:
  dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
  phase_name, _ = _phase(dt)
  assert phase_name == expected_phase


@pytest.mark.parametrize('iso_date,_', _PHASE_CASES)
def test_illumination_range(iso_date: str, _: str) -> None:
  dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
  _, illumination = _phase(dt)
  assert 0.0 <= illumination <= 1.0


def test_all_phases_have_grids() -> None:
  expected = {
    'NEW MOON',
    'WAXING CRESCENT',
    'FIRST QUARTER',
    'WAXING GIBBOUS',
    'FULL MOON',
    'WANING GIBBOUS',
    'LAST QUARTER',
    'WANING CRESCENT',
  }
  assert set(_GRIDS.keys()) == expected


@pytest.mark.parametrize('phase_name,rows', _GRIDS.items())
def test_grid_rows_are_five_wide(phase_name: str, rows: tuple[str, str, str]) -> None:
  for row in rows:
    width = _count_visual_width(row)
    assert width == 5, f'{phase_name}: row {row!r} has width {width}, expected 5'


def test_waxing_phases_are_right_lit() -> None:
  # Waxing crescent: rightmost column of row 2 should be [W] (or center-right)
  # We check that the last [W] in row 2 is in the right half (cols 3–5)
  for phase in ('WAXING CRESCENT', 'FIRST QUARTER', 'WAXING GIBBOUS'):
    row2 = _GRIDS[phase][1]
    tags = []
    i = 0
    while i < len(row2):
      if row2[i] == '[' and row2[i : i + _TAG_LEN] in _COLOR_TAGS:
        tags.append(row2[i : i + _TAG_LEN])
        i += _TAG_LEN
      else:
        i += 1
    # The rightmost white square should be in position 3, 4, or 5 (1-indexed)
    white_positions = [idx + 1 for idx, t in enumerate(tags) if t == '[W]']
    assert white_positions, f'{phase}: no white squares in row 2'
    assert max(white_positions) >= 3, f'{phase}: rightmost white at col {max(white_positions)}, expected >= 3'


def test_waning_phases_are_left_lit() -> None:
  for phase in ('WANING CRESCENT', 'LAST QUARTER', 'WANING GIBBOUS'):
    row2 = _GRIDS[phase][1]
    tags = []
    i = 0
    while i < len(row2):
      if row2[i] == '[' and row2[i : i + _TAG_LEN] in _COLOR_TAGS:
        tags.append(row2[i : i + _TAG_LEN])
        i += _TAG_LEN
      else:
        i += 1
    # The leftmost white square should be in position 1, 2, or 3 (1-indexed)
    white_positions = [idx + 1 for idx, t in enumerate(tags) if t == '[W]']
    assert white_positions, f'{phase}: no white squares in row 2'
    assert min(white_positions) <= 3, f'{phase}: leftmost white at col {min(white_positions)}, expected <= 3'


def test_get_variables_shape() -> None:
  result = get_variables()
  assert set(result.keys()) == {'moon_row1', 'moon_row2', 'moon_row3', 'phase_name', 'illumination'}
  for key in ('moon_row1', 'moon_row2', 'moon_row3'):
    assert isinstance(result[key], list)
    assert len(result[key]) == 1
    assert len(result[key][0]) == 1
    assert isinstance(result[key][0][0], str)


def test_get_variables_illumination_format() -> None:
  result = get_variables()
  illumination = result['illumination'][0][0]
  assert illumination.endswith('%')
  value = int(illumination[:-1])
  assert 0 <= value <= 100
