from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.bart as bart
import integrations.vestaboard as vb

# Minimal ETD API response used across multiple tests.
_FAKE_ETD: dict[str, Any] = {
  'root': {
    'station': [
      {
        'name': 'Milpitas',
        'abbr': 'MLPT',
        'etd': [
          {
            'destination': 'Daly City',
            'abbreviation': 'DALY',
            'limited': '0',
            'estimate': [
              {
                'minutes': '5',
                'platform': '2',
                'direction': 'South',
                'length': '6',
                'color': 'GREEN',
                'hexcolor': '#339933',
                'bikeflag': '1',
                'delay': '0',
                'cancelflag': '0',
                'dynamicflag': '0',
              },
              {
                'minutes': '20',
                'platform': '2',
                'direction': 'South',
                'length': '6',
                'color': 'GREEN',
                'hexcolor': '#339933',
                'bikeflag': '1',
                'delay': '0',
                'cancelflag': '0',
                'dynamicflag': '0',
              },
            ],
          },
        ],
      }
    ]
  }
}

# Minimal routes and routeinfo API fixtures for cache tests.
_FAKE_ROUTES_ONE: dict[str, Any] = {'root': {'routes': {'route': [{'number': '6', 'color': 'GREEN'}]}}}

_FAKE_ROUTEINFO_6_DALY: dict[str, Any] = {
  'root': {
    'routes': {
      'route': {
        'number': '6',
        'destination': 'DALY',
        'config': {'station': ['MLPT', 'CIVC', 'DALY']},
      }
    }
  }
}

_FAKE_ROUTES_TWO: dict[str, Any] = {
  'root': {
    'routes': {
      'route': [
        {'number': '1', 'color': 'YELLOW'},
        {'number': '6', 'color': 'GREEN'},
      ]
    }
  }
}

_FAKE_ROUTEINFO_1_NO_MLPT: dict[str, Any] = {
  'root': {
    'routes': {
      'route': {
        'number': '1',
        'destination': 'ANTC',
        'config': {'station': ['SFIA', 'MLBR', 'ANTC']},
      }
    }
  }
}

_FAKE_ROUTES_MULTI_COLOR: dict[str, Any] = {
  'root': {
    'routes': {
      'route': [
        {'number': '4', 'color': 'ORANGE'},
        {'number': '6', 'color': 'GREEN'},
      ]
    }
  }
}

_FAKE_ROUTEINFO_4_BERY: dict[str, Any] = {
  'root': {
    'routes': {
      'route': {
        'number': '4',
        'destination': 'BERY',
        'config': {'station': ['MLPT', 'BERY']},
      }
    }
  }
}

_FAKE_ROUTEINFO_6_BERY: dict[str, Any] = {
  'root': {
    'routes': {
      'route': {
        'number': '6',
        'destination': 'BERY',
        'config': {'station': ['MLPT', 'BERY']},
      }
    }
  }
}


def _mock(json_data: dict[str, Any]) -> MagicMock:
  """Return a mock response with the given JSON payload."""
  m = MagicMock()
  m.json.return_value = json_data
  m.raise_for_status.return_value = None
  return m


# Reset the module-level color cache before every test so routes API calls
# don't bleed between tests. Tests that specifically exercise cache population
# set bart._dest_color_cache = None themselves after this fixture runs.
@pytest.fixture(autouse=True)
def reset_color_cache() -> Generator[None, None, None]:
  original = bart._dest_color_cache  # noqa: SLF001
  bart._dest_color_cache = {}  # noqa: SLF001
  yield
  bart._dest_color_cache = original  # noqa: SLF001


# --- _format_minutes ---


def test_format_minutes_leaving() -> None:
  assert bart._format_minutes('Leaving') == 'Now'  # noqa: SLF001


def test_format_minutes_zero() -> None:
  assert bart._format_minutes('0') == 'Now'  # noqa: SLF001


def test_format_minutes_numeric() -> None:
  assert bart._format_minutes('12') == '12'  # noqa: SLF001


def test_format_minutes_non_numeric_passthrough() -> None:
  assert bart._format_minutes('???') == '???'  # noqa: SLF001


# --- _build_line ---


def test_build_line_basic() -> None:
  estimates = [{'minutes': '5'}, {'minutes': '15'}]
  result = bart._build_line('[G]', estimates)  # noqa: SLF001
  assert result.startswith('[G]')
  assert '5' in result
  assert vb.display_len(result) <= vb.model.cols


def test_build_line_stops_at_col_limit() -> None:
  # Enough estimates to potentially overflow the board width
  estimates = [{'minutes': str(i)} for i in range(1, 30)]
  result = bart._build_line('[G]', estimates)  # noqa: SLF001
  assert vb.display_len(result) <= vb.model.cols


def test_build_line_empty_estimates() -> None:
  result = bart._build_line('[G]', [])  # noqa: SLF001
  assert result == '[G] --'


# --- _no_service_line ---


def test_no_service_line_known_dest() -> None:
  result = bart._no_service_line('DALY', {'DALY': ['[G]']})  # noqa: SLF001
  assert 'NO SERVICE' in result
  assert '[G]' in result


def test_no_service_line_unknown_dest() -> None:
  result = bart._no_service_line('ZZZZ', {})  # noqa: SLF001
  assert result == 'NO SERVICE'


def test_no_service_line_multi_color_uses_first() -> None:
  # When multiple tags exist, the first (lowest route number) is used.
  result = bart._no_service_line('BERY', {'BERY': ['[O]', '[G]']})  # noqa: SLF001
  assert result == '[O] NO SERVICE'


# --- _fetch_dest_colors ---


def test_fetch_dest_colors_maps_terminal_to_color() -> None:
  with patch(
    'integrations.bart.requests.get',
    side_effect=[_mock(_FAKE_ROUTES_ONE), _mock(_FAKE_ROUTEINFO_6_DALY)],
  ):
    result = bart._fetch_dest_colors('testkey', 'MLPT')  # noqa: SLF001
  assert result['DALY'] == ['[G]']


def test_fetch_dest_colors_excludes_routes_not_serving_origin() -> None:
  # Route 1 (YELLOW) has no MLPT — only route 6 (GREEN, DALY) should appear.
  with patch(
    'integrations.bart.requests.get',
    side_effect=[
      _mock(_FAKE_ROUTES_TWO),
      _mock(_FAKE_ROUTEINFO_1_NO_MLPT),
      _mock(_FAKE_ROUTEINFO_6_DALY),
    ],
  ):
    result = bart._fetch_dest_colors('testkey', 'MLPT')  # noqa: SLF001
  assert 'ANTC' not in result
  assert result.get('DALY') == ['[G]']


def test_fetch_dest_colors_handles_multiple_colors_per_dest() -> None:
  # Routes 4 (ORANGE) and 6 (GREEN) both serve BERY from MLPT.
  # Route 4 has a lower number so ORANGE appears first.
  with patch(
    'integrations.bart.requests.get',
    side_effect=[
      _mock(_FAKE_ROUTES_MULTI_COLOR),
      _mock(_FAKE_ROUTEINFO_4_BERY),
      _mock(_FAKE_ROUTEINFO_6_BERY),
    ],
  ):
    result = bart._fetch_dest_colors('testkey', 'MLPT')  # noqa: SLF001
  assert '[O]' in result['BERY']
  assert '[G]' in result['BERY']
  assert result['BERY'][0] == '[O]'


def test_fetch_dest_colors_raises_on_http_error() -> None:
  mock_fail = MagicMock()
  mock_fail.raise_for_status.side_effect = requests.HTTPError('network error')
  with patch('integrations.bart.requests.get', return_value=mock_fail):
    with pytest.raises(requests.HTTPError):
      bart._fetch_dest_colors('testkey', 'MLPT')  # noqa: SLF001


# --- get_variables ---


@pytest.fixture()
def bart_env(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'bart': {'api_key': 'testkey', 'station': 'MLPT', 'line1_dest': 'DALY'}},
  )


def test_get_variables_happy_path(bart_env: None) -> None:
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert result['station'] == [['Milpitas']]
  assert len(result['line1']) == 1
  assert '[G]' in result['line1'][0][0]
  assert '5' in result['line1'][0][0]


def test_get_variables_no_service_when_dest_absent(bart_env: None) -> None:
  # ETD response has no entry for the requested destination
  empty_etd = {'root': {'station': [{'name': 'Milpitas', 'abbr': 'MLPT', 'etd': []}]}}
  mock_resp = MagicMock()
  mock_resp.json.return_value = empty_etd
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert 'NO SERVICE' in result['line1'][0][0]


def test_get_variables_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {})
  with pytest.raises(ValueError, match='api_key'):
    bart.get_variables()


def test_get_variables_missing_station(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'bart': {'api_key': 'testkey'}})
  with pytest.raises(ValueError, match='station'):
    bart.get_variables()


def test_get_variables_matches_by_abbreviation_code(bart_env: None) -> None:
  # BART_LINE_1_DEST="DALY" matches ETD entry with abbreviation="DALY"
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert '[G]' in result['line1'][0][0]
  assert '5' in result['line1'][0][0]


def test_get_variables_code_matching_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'bart': {'api_key': 'testkey', 'station': 'MLPT', 'line1_dest': 'daly'}})
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert '[G]' in result['line1'][0][0]


def test_get_variables_unknown_code_shows_no_service(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'bart': {'api_key': 'testkey', 'station': 'MLPT', 'line1_dest': 'ZZZZ'}})
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert 'NO SERVICE' in result['line1'][0][0]


def test_get_variables_line2_absent_when_not_set(bart_env: None) -> None:
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert 'line2' not in result


_FAKE_ETD_TWO_DESTS: dict[str, Any] = {
  'root': {
    'station': [
      {
        'name': 'Milpitas',
        'abbr': 'MLPT',
        'etd': [
          {
            'destination': 'Daly City',
            'abbreviation': 'DALY',
            'limited': '0',
            'estimate': [
              {
                'minutes': '5',
                'platform': '2',
                'direction': 'South',
                'length': '6',
                'color': 'GREEN',
                'hexcolor': '#339933',
                'bikeflag': '1',
                'delay': '0',
                'cancelflag': '0',
                'dynamicflag': '0',
              },
            ],
          },
          {
            'destination': 'Berryessa/North San José',
            'abbreviation': 'BERY',
            'limited': '0',
            'estimate': [
              {
                'minutes': '10',
                'platform': '1',
                'direction': 'South',
                'length': '6',
                'color': 'GREEN',
                'hexcolor': '#339933',
                'bikeflag': '1',
                'delay': '0',
                'cancelflag': '0',
                'dynamicflag': '0',
              },
            ],
          },
        ],
      }
    ]
  }
}


def test_get_variables_two_lines(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'bart': {'api_key': 'testkey', 'station': 'MLPT', 'line1_dest': 'DALY', 'line2_dest': 'BERY'}},
  )
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD_TWO_DESTS
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert 'line1' in result
  assert 'line2' in result
  assert '5' in result['line1'][0][0]
  assert '10' in result['line2'][0][0]


def test_get_variables_http_error_does_not_leak_key(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  api_key = 'supersecretkey99'
  monkeypatch.setattr(_cfg, '_config', {'bart': {'api_key': api_key, 'station': 'MLPT', 'line1_dest': 'DALY'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 401
  mock_resp.reason = 'Unauthorized'
  mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    with pytest.raises(requests.HTTPError) as exc_info:
      bart.get_variables()
  assert api_key not in str(exc_info.value)


# --- get_variables — color cache ---


def test_get_variables_populates_color_cache(bart_env: None) -> None:
  # Override the autouse fixture: start with an unpopulated cache.
  bart._dest_color_cache = None  # noqa: SLF001
  with patch(
    'integrations.bart.requests.get',
    side_effect=[
      _mock(_FAKE_ROUTES_ONE),  # routes call
      _mock(_FAKE_ROUTEINFO_6_DALY),  # routeinfo route 6
      _mock(_FAKE_ETD),  # ETD call
    ],
  ):
    bart.get_variables()
  assert bart._dest_color_cache is not None  # noqa: SLF001
  assert '[G]' in (bart._dest_color_cache or {}).get('DALY', [])  # noqa: SLF001


def test_get_variables_routes_api_failure_does_not_crash(bart_env: None, capsys: pytest.CaptureFixture[str]) -> None:
  # Routes API fails — get_variables should still return ETD data and degrade
  # no-service lines to colorless 'NO SERVICE'.
  bart._dest_color_cache = None  # noqa: SLF001
  empty_etd = {'root': {'station': [{'name': 'Milpitas', 'abbr': 'MLPT', 'etd': []}]}}
  mock_fail = MagicMock()
  mock_fail.raise_for_status.side_effect = requests.HTTPError('network error')
  with patch(
    'integrations.bart.requests.get',
    side_effect=[mock_fail, _mock(empty_etd)],
  ):
    result = bart.get_variables()
  assert result['line1'][0][0] == 'NO SERVICE'
  out = capsys.readouterr().out
  assert 'Warning' in out


def test_get_variables_uses_cached_colors_on_second_call(bart_env: None) -> None:
  # Cache is already populated (autouse sets it to {}). The routes API must
  # NOT be called — only the single ETD request should happen.
  bart._dest_color_cache = {'DALY': ['[G]']}  # noqa: SLF001
  with patch('integrations.bart.requests.get', return_value=_mock(_FAKE_ETD)) as mock_get:
    bart.get_variables()
  # Only one requests.get call (the ETD); no routes/routeinfo calls.
  assert mock_get.call_count == 1
