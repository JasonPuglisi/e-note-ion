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
  assert vb._display_len(result) <= vb.model.cols  # noqa: SLF001


def test_build_line_stops_at_col_limit() -> None:
  # Enough estimates to potentially overflow the board width
  estimates = [{'minutes': str(i)} for i in range(1, 30)]
  result = bart._build_line('[G]', estimates)  # noqa: SLF001
  assert vb._display_len(result) <= vb.model.cols  # noqa: SLF001


def test_build_line_empty_estimates() -> None:
  result = bart._build_line('[G]', [])  # noqa: SLF001
  assert result == '[G] --'


# --- _no_service_line ---


def test_no_service_line_known_dest() -> None:
  result = bart._no_service_line('DALY')  # noqa: SLF001
  assert 'NO SERVICE' in result
  assert '[G]' in result


def test_no_service_line_unknown_dest() -> None:
  result = bart._no_service_line('ZZZZ')  # noqa: SLF001
  assert result == 'NO SERVICE'


# --- get_variables ---


@pytest.fixture()
def bart_env(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('BART_API_KEY', 'testkey')
  monkeypatch.setenv('BART_STATION', 'MLPT')
  monkeypatch.setenv('BART_LINE_1_DEST', 'DALY')
  monkeypatch.delenv('BART_LINE_2_DEST', raising=False)


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
  monkeypatch.delenv('BART_API_KEY', raising=False)
  with pytest.raises(RuntimeError, match='BART_API_KEY'):
    bart.get_variables()


def test_get_variables_missing_station(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('BART_API_KEY', 'testkey')
  monkeypatch.delenv('BART_STATION', raising=False)
  with pytest.raises(RuntimeError, match='BART_STATION'):
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
  monkeypatch.setenv('BART_API_KEY', 'testkey')
  monkeypatch.setenv('BART_STATION', 'MLPT')
  monkeypatch.setenv('BART_LINE_1_DEST', 'daly')  # lowercase
  monkeypatch.delenv('BART_LINE_2_DEST', raising=False)
  mock_resp = MagicMock()
  mock_resp.json.return_value = _FAKE_ETD
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    result = bart.get_variables()
  assert '[G]' in result['line1'][0][0]


def test_get_variables_unknown_code_shows_no_service(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('BART_API_KEY', 'testkey')
  monkeypatch.setenv('BART_STATION', 'MLPT')
  monkeypatch.setenv('BART_LINE_1_DEST', 'ZZZZ')
  monkeypatch.delenv('BART_LINE_2_DEST', raising=False)
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
  monkeypatch.setenv('BART_API_KEY', 'testkey')
  monkeypatch.setenv('BART_STATION', 'MLPT')
  monkeypatch.setenv('BART_LINE_1_DEST', 'DALY')
  monkeypatch.setenv('BART_LINE_2_DEST', 'BERY')
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
  api_key = 'supersecretkey99'
  monkeypatch.setenv('BART_API_KEY', api_key)
  monkeypatch.setenv('BART_STATION', 'MLPT')
  monkeypatch.setenv('BART_LINE_1_DEST', 'DALY')
  mock_resp = MagicMock()
  mock_resp.status_code = 401
  mock_resp.reason = 'Unauthorized'
  mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
  with patch('integrations.bart.requests.get', return_value=mock_resp):
    with pytest.raises(requests.HTTPError) as exc_info:
      bart.get_variables()
  assert api_key not in str(exc_info.value)
