"""Integration tests for integrations/weather.py — call the real Open-Meteo API.

Run with: uv run pytest -m integration

No API key required. Open-Meteo is free for non-commercial use.
"""

import pytest

import config as _cfg
import integrations.vestaboard as vb
import integrations.weather as weather


@pytest.mark.integration
def test_get_variables_returns_expected_keys(monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns a valid variables dict from the live Open-Meteo API."""
  monkeypatch.setattr(
    _cfg,
    '_config',
    {'weather': {'city': 'San Francisco', 'units': 'imperial'}},
  )
  weather._geocode_cache = None

  result = weather.get_variables()

  expected_keys = {'city', 'condition', 'temp', 'feels_like', 'high', 'low', 'wind', 'precip'}
  assert set(result.keys()) == expected_keys

  for key in expected_keys:
    assert len(result[key]) == 1, f'{key}: expected 1 option'
    assert len(result[key][0]) == 1, f'{key}: expected 1 line'
    assert result[key][0][0], f'{key}: value is empty'

  # City name should be canonical (not the raw config value).
  assert result['city'][0][0] == 'San Francisco'

  # Condition should start with a color tag and fit Note cols.
  condition = result['condition'][0][0]
  assert condition[0] == '[', f'condition missing color tag: {condition!r}'
  assert vb.display_len(condition) <= vb.VestaboardModel.NOTE.cols, f'condition exceeds Note cols: {condition!r}'

  # Temperature should end with 'F' (imperial).
  assert result['temp'][0][0].endswith('F'), f'temp should end with F: {result["temp"][0][0]!r}'
