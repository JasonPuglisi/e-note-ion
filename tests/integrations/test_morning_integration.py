"""Integration tests for integrations/morning.py — calls the real Open-Meteo API
via the weather integration.

Run with: uv run pytest -m integration

No API key required. Open-Meteo is free for non-commercial use.
The morning integration has no env vars of its own; it reuses [weather] config.
"""

import pytest

import config as _cfg
import integrations.morning as morning
import integrations.weather as weather

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


@pytest.mark.integration
def test_get_variables_live(monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns a valid 7-wide visual using live Open-Meteo data."""
  monkeypatch.setattr(_cfg, '_config', {'weather': {'city': 'San Francisco', 'units': 'imperial'}})
  weather._geocode_cache = None
  weather._forecast_cache = None

  result = morning.get_variables()

  assert set(result.keys()) == {'morning_r1', 'morning_r2', 'morning_r3'}
  for key in ('morning_r1', 'morning_r2', 'morning_r3'):
    assert len(result[key]) == 1
    assert len(result[key][0]) == 1
    row = result[key][0][0]
    assert isinstance(row, str)
    width = _count_visual_width(row)
    assert width == 7, f'{key}: expected 7-wide row, got {row!r}'
