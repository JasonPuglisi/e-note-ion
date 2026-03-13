"""Unit tests for integrations/dive_conditions.py."""

from unittest.mock import MagicMock, patch

import pytest

import integrations.dive_conditions as dc
from exceptions import IntegrationDataUnavailableError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NDBC_SAMPLE = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2024 01 15 10 00  280  4.0  5.1   1.2  14.0   8.5 290 1015.2  12.5  12.8  10.2   MM   MM    MM
2024 01 15 11 00  270  5.1  6.2   1.5  14.0   8.5 280 1015.5  12.3  13.0  10.1   MM   MM    MM
"""

_NDBC_MISSING_FIELDS = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2024 01 15 12 00  270   MM   MM    MM    MM    MM  MM 1015.2  12.5    MM  10.2   MM   MM    MM
"""

_NDBC_ALL_MISSING = """\
#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
"""


# ---------------------------------------------------------------------------
# _degrees_to_cardinal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'deg,expected',
  [
    (0, 'N'),
    (45, 'NE'),
    (90, 'E'),
    (135, 'SE'),
    (180, 'S'),
    (225, 'SW'),
    (270, 'W'),
    (315, 'NW'),
    (360, 'N'),
    (23, 'NE'),  # rounds to NE (45); boundary is 22.5°
    (337, 'NW'),  # rounds to NW (315)
  ],
)
def test_degrees_to_cardinal(deg: float, expected: str) -> None:
  assert dc._degrees_to_cardinal(deg) == expected


# ---------------------------------------------------------------------------
# _fmt_wave
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'meters,units,expected',
  [
    (0.5, 'imperial', '1.6FT'),
    (1.5, 'imperial', '4.9FT'),
    (3.05, 'imperial', '10FT'),  # >= 10ft → no decimal
    (0.5, 'metric', '0.5M'),
    (9.5, 'metric', '9.5M'),
    (10.0, 'metric', '10M'),  # >= 10m → no decimal
  ],
)
def test_fmt_wave(meters: float, units: str, expected: str) -> None:
  assert dc._fmt_wave(meters, units) == expected


# ---------------------------------------------------------------------------
# _fmt_temp
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'celsius,units,expected',
  [
    (13.0, 'imperial', '55F'),
    (0.0, 'imperial', '32F'),
    (13.0, 'metric', '13C'),
    (13.6, 'metric', '14C'),
  ],
)
def test_fmt_temp(celsius: float, units: str, expected: str) -> None:
  assert dc._fmt_temp(celsius, units) == expected


# ---------------------------------------------------------------------------
# _fmt_wind_kt
# ---------------------------------------------------------------------------


def test_fmt_wind_kt_converts_from_ms() -> None:
  # 5.144 m/s ≈ 10 kt
  result = dc._fmt_wind_kt(5.144)
  assert result == '10KT'


def test_fmt_wind_kt_rounds() -> None:
  result = dc._fmt_wind_kt(10.0)
  assert result.endswith('KT')
  assert result[:-2].isdigit()


# ---------------------------------------------------------------------------
# _condition_color
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'wave_m,wind_ms,period_s,expected',
  [
    # Both clearly green
    (0.3, 2.0, 14.0, '[G]'),  # 1ft wave, 4kt wind
    # Wave marginal, wind green → yellow
    (0.9, 2.0, 14.0, '[Y]'),  # 3ft wave, 4kt wind
    # Wind marginal, wave green → yellow
    (0.3, 7.7, 14.0, '[Y]'),  # 1ft wave, 15kt wind
    # Both marginal → yellow
    (0.9, 7.7, 14.0, '[Y]'),  # 3ft wave, 15kt wind
    # Wave red → red
    (1.8, 2.0, 14.0, '[R]'),  # 6ft wave, 4kt wind
    # Wind red → red
    (0.3, 12.9, 14.0, '[R]'),  # 1ft wave, 25kt wind
    # Both red → red
    (1.8, 12.9, 14.0, '[R]'),
    # Period modifier: green wave+wind but choppy period → yellow
    # 0.45m ≈ 1.5ft; period 2.0s; ratio 2.0/1.5 = 1.33 < 2.0 → bump green→yellow
    (0.45, 2.0, 2.0, '[Y]'),
    # Period modifier: yellow → red
    # 0.9m ≈ 3ft; period 5s; ratio 5/3 ≈ 1.67 < 2.0 → bump yellow→red
    (0.9, 2.0, 5.0, '[R]'),
    # Period modifier: already red → stays red (no over-bump)
    (1.8, 2.0, 2.0, '[R]'),
    # Period ratio exactly at threshold (= 2.0) → no bump
    # 0.60m ≈ 1.97ft; period 4s; ratio 4/1.97 ≈ 2.03, not < 2.0 → no bump (green)
    (0.60, 2.0, 4.0, '[G]'),
    # Missing wave data → yellow
    (None, 5.0, 14.0, '[Y]'),
    # Missing wind data → yellow
    (0.5, None, 14.0, '[Y]'),
    # Missing period → no modifier applied
    (0.3, 2.0, None, '[G]'),
  ],
)
def test_condition_color(
  wave_m: float | None,
  wind_ms: float | None,
  period_s: float | None,
  expected: str,
) -> None:
  assert dc._condition_color(wave_m, wind_ms, period_s) == expected


# ---------------------------------------------------------------------------
# _parse_ndbc
# ---------------------------------------------------------------------------


def test_parse_ndbc_extracts_most_recent_row() -> None:
  """Most recent (last) data row is returned."""
  result = dc._parse_ndbc(_NDBC_SAMPLE)
  # Last row: WVHT=1.5, DPD=14.0, WSPD=5.1, WDIR=270, WTMP=13.0
  assert result['wave_height_m'] == pytest.approx(1.5)
  assert result['period_s'] == pytest.approx(14.0)
  assert result['wind_speed_ms'] == pytest.approx(5.1)
  assert result['wind_dir_deg'] == pytest.approx(270.0)
  assert result['water_temp_c'] == pytest.approx(13.0)


def test_parse_ndbc_mm_fields_return_none() -> None:
  result = dc._parse_ndbc(_NDBC_MISSING_FIELDS)
  assert result['wave_height_m'] is None
  assert result['period_s'] is None
  assert result['wind_speed_ms'] is None
  assert result['water_temp_c'] is None
  # WDIR is not MM in this sample
  assert result['wind_dir_deg'] == pytest.approx(270.0)


def test_parse_ndbc_no_data_rows_raises() -> None:
  with pytest.raises(IntegrationDataUnavailableError, match='could not parse NDBC data'):
    dc._parse_ndbc(_NDBC_ALL_MISSING)


def test_parse_ndbc_empty_raises() -> None:
  with pytest.raises(IntegrationDataUnavailableError):
    dc._parse_ndbc('')


# ---------------------------------------------------------------------------
# _fetch_ndbc (mocked HTTP)
# ---------------------------------------------------------------------------


def test_fetch_ndbc_success(monkeypatch: pytest.MonkeyPatch) -> None:
  mock_resp = MagicMock()
  mock_resp.text = _NDBC_SAMPLE
  mock_resp.raise_for_status = MagicMock()

  with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp):
    result = dc._fetch_ndbc('46042')

  assert result['wave_height_m'] == pytest.approx(1.5)


def test_fetch_ndbc_http_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  import requests as _requests

  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=_requests.RequestException('timeout'),
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='NDBC request failed'):
      dc._fetch_ndbc('46042')


# ---------------------------------------------------------------------------
# _fetch_openmeteo (mocked HTTP)
# ---------------------------------------------------------------------------


def _marine_response(wave_height: float = 1.5, wave_period: float = 12.0, sst: float = 13.0) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = {
    'current': {
      'wave_height': wave_height,
      'wave_period': wave_period,
      'sea_surface_temperature': sst,
    }
  }
  resp.raise_for_status = MagicMock()
  return resp


def _forecast_response(wind_kmh: float = 18.0, wind_dir: float = 270.0) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = {
    'current': {
      'wind_speed_10m': wind_kmh,
      'wind_direction_10m': wind_dir,
    }
  }
  resp.raise_for_status = MagicMock()
  return resp


def test_fetch_openmeteo_success() -> None:
  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=[_marine_response(), _forecast_response()],
  ):
    result = dc._fetch_openmeteo(36.6, -121.9)

  assert result['wave_height_m'] == pytest.approx(1.5)
  assert result['period_s'] == pytest.approx(12.0)
  assert result['water_temp_c'] == pytest.approx(13.0)
  # 18 km/h → 5.0 m/s
  assert result['wind_speed_ms'] == pytest.approx(18.0 / 3.6)
  assert result['wind_dir_deg'] == pytest.approx(270.0)


def test_fetch_openmeteo_marine_failure_raises() -> None:
  import requests as _requests

  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=_requests.RequestException('timeout'),
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='marine request failed'):
      dc._fetch_openmeteo(36.6, -121.9)


def test_fetch_openmeteo_forecast_failure_raises() -> None:
  import requests as _requests

  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=[_marine_response(), _requests.RequestException('timeout')],
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='forecast request failed'):
      dc._fetch_openmeteo(36.6, -121.9)


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def _patched_config(station_id: str = '46042', units: str = 'imperial') -> dict:
  return {'dive_conditions': {'ndbc_station_id': station_id, 'units': units}}


def test_cache_hit_avoids_fetch(monkeypatch: pytest.MonkeyPatch) -> None:

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  cached_value: dict = {'header': [['[G] WATER 55F']], 'swell': [['SWELL 1.5FT 14S']], 'wind': [['WIND 10KT W']]}
  dc._cache = dc.CacheEntry(cached_value)  # type: ignore[attr-defined]

  with patch('integrations.dive_conditions.fetch_with_retry') as mock_fetch:
    result = dc.get_variables()
    mock_fetch.assert_not_called()

  assert result == cached_value
  dc._cache = None


def test_cache_miss_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  dc._cache = None

  mock_resp = MagicMock()
  mock_resp.text = _NDBC_SAMPLE
  mock_resp.raise_for_status = MagicMock()

  with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp):
    result = dc.get_variables()

  assert 'header' in result
  assert 'swell' in result
  assert 'wind' in result
  dc._cache = None


def test_expired_cache_fetches_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
  import time

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  stale_value: dict = {'header': [['[G] WATER 55F']], 'swell': [['SWELL 1.5FT 14S']], 'wind': [['WIND 10KT W']]}
  dc._cache = dc.CacheEntry(stale_value)  # type: ignore[attr-defined]
  # Force the cache to appear expired.
  dc._cache.cached_at = time.monotonic() - dc._CACHE_TTL - 1

  mock_resp = MagicMock()
  mock_resp.text = _NDBC_SAMPLE
  mock_resp.raise_for_status = MagicMock()

  with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp) as mock_fetch:
    dc.get_variables()
    mock_fetch.assert_called_once()

  dc._cache = None


def test_stale_cache_served_on_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
  import time

  import requests as _requests

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  stale_value: dict = {'header': [['[Y] WATER 55F']], 'swell': [['SWELL 1.5FT 14S']], 'wind': [['WIND 10KT W']]}
  dc._cache = dc.CacheEntry(stale_value)  # type: ignore[attr-defined]
  dc._cache.cached_at = time.monotonic() - dc._CACHE_TTL - 1

  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=_requests.RequestException('timeout'),
  ):
    result = dc.get_variables()

  assert result == stale_value
  dc._cache = None


def test_no_cache_on_fetch_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  import requests as _requests

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  dc._cache = None

  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=_requests.RequestException('timeout'),
  ):
    with pytest.raises(IntegrationDataUnavailableError):
      dc.get_variables()


# ---------------------------------------------------------------------------
# get_variables — output shape and content
# ---------------------------------------------------------------------------


def test_get_variables_uses_openmeteo_when_no_station(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'dive_conditions': {'latitude': '36.6', 'longitude': '-121.9', 'units': 'imperial'}},
  )
  dc._cache = None

  with patch(
    'integrations.dive_conditions.fetch_with_retry',
    side_effect=[_marine_response(), _forecast_response()],
  ):
    result = dc.get_variables()

  assert 'header' in result
  dc._cache = None


def test_get_variables_imperial_units(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(units='imperial'))
  dc._cache = None

  mock_resp = MagicMock()
  mock_resp.text = _NDBC_SAMPLE
  mock_resp.raise_for_status = MagicMock()

  with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp):
    result = dc.get_variables()

  header = result['header'][0][0]
  # Header should contain 'F' (Fahrenheit) not 'C'
  assert 'F' in header and 'C' not in header
  # Swell row should contain 'FT'
  assert 'FT' in result['swell'][0][0]
  dc._cache = None


def test_get_variables_metric_units(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(units='metric'))
  dc._cache = None

  mock_resp = MagicMock()
  mock_resp.text = _NDBC_SAMPLE
  mock_resp.raise_for_status = MagicMock()

  with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp):
    result = dc.get_variables()

  header = result['header'][0][0]
  assert 'C' in header
  assert 'M' in result['swell'][0][0]
  dc._cache = None


def test_get_variables_missing_data_shows_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  dc._cache = None

  mock_resp = MagicMock()
  mock_resp.text = _NDBC_MISSING_FIELDS
  mock_resp.raise_for_status = MagicMock()

  with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp):
    result = dc.get_variables()

  # Missing fields should render as '--'
  assert '--' in result['header'][0][0]
  assert '--' in result['swell'][0][0]
  dc._cache = None


def test_get_variables_wind_always_in_knots(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  for units in ('imperial', 'metric'):
    monkeypatch.setattr(_cfg, '_config', _patched_config(units=units))
    dc._cache = None

    mock_resp = MagicMock()
    mock_resp.text = _NDBC_SAMPLE
    mock_resp.raise_for_status = MagicMock()

    with patch('integrations.dive_conditions.fetch_with_retry', return_value=mock_resp):
      result = dc.get_variables()

    assert 'KT' in result['wind'][0][0], f'wind should be in KT for units={units}'
    dc._cache = None


def test_get_variables_missing_lat_lon_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'dive_conditions': {}})
  dc._cache = None

  with pytest.raises(IntegrationDataUnavailableError, match='ndbc_station_id or latitude/longitude'):
    dc.get_variables()
