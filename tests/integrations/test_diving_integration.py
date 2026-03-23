"""Integration tests for integrations/diving.py.

All data sources are keyless — safe to run in CI on every main push.

Run with: uv run pytest -m integration

Required env vars:
  DIVING_NDBC_STATION  — NDBC station ID (e.g. "46042" for Monterey)
  DIVING_LAT           — decimal latitude for Open-Meteo fallback test
  DIVING_LON           — decimal longitude for Open-Meteo fallback test
"""

import os

import pytest

import config as _config_mod
import integrations.diving as dc


@pytest.mark.integration
@pytest.mark.require_env('DIVING_NDBC_STATION')
def test_ndbc_get_variables(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid variables from the live NDBC API."""
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'diving': {
        'ndbc_station_id': os.environ['DIVING_NDBC_STATION'],
        'units': 'imperial',
      }
    },
  )
  dc._cache = None

  result = dc.get_variables()

  assert set(result.keys()) == {'header', 'swell', 'wind'}
  for key in ('header', 'swell', 'wind'):
    assert len(result[key]) == 1, f'{key}: expected 1 option'
    assert len(result[key][0]) == 1, f'{key}: expected 1 line'
    assert result[key][0][0], f'{key}: value is empty'

  header = result['header'][0][0]
  assert header.startswith('['), f'header missing color tag: {header!r}'
  assert 'WATER' in header, f'header missing WATER label: {header!r}'

  swell = result['swell'][0][0]
  assert swell.startswith('SWELL'), f'unexpected swell format: {swell!r}'

  wind = result['wind'][0][0]
  assert wind.startswith('WIND'), f'unexpected wind format: {wind!r}'
  # Some buoys lack anemometers — wind fields may be '--'; only assert KT when data present.
  if '--' not in wind:
    assert 'KT' in wind, f'wind not in knots: {wind!r}'

  dc._cache = None


@pytest.mark.integration
@pytest.mark.require_env('DIVING_LAT', 'DIVING_LON')
def test_openmeteo_fallback_get_variables(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid variables from the live Open-Meteo APIs."""
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'diving': {
        'latitude': os.environ['DIVING_LAT'],
        'longitude': os.environ['DIVING_LON'],
        'units': 'imperial',
      }
    },
  )
  dc._cache = None

  result = dc.get_variables()

  assert set(result.keys()) == {'header', 'swell', 'wind'}
  for key in ('header', 'swell', 'wind'):
    assert result[key][0][0], f'{key}: value is empty'

  assert 'KT' in result['wind'][0][0], 'wind not in knots'

  dc._cache = None
