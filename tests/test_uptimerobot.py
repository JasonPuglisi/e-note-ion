import time
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.uptimerobot as uptimerobot
from exceptions import IntegrationDataUnavailableError


def _mock_response(data: dict[str, Any]) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = data
  resp.raise_for_status = MagicMock()
  return resp


def _api_response(monitors: list[dict[str, Any]]) -> dict[str, Any]:
  return {
    'stat': 'ok',
    'pagination': {'offset': 0, 'limit': 50, 'total': len(monitors)},
    'monitors': monitors,
  }


def _monitor(
  id: int = 1,
  friendly_name: str = 'My API',
  status: int = 2,
) -> dict[str, Any]:
  return {
    'id': id,
    'friendly_name': friendly_name,
    'url': 'https://api.example.com',
    'status': status,
  }


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'uptimerobot': {'api_key': 'test-key'}})
  yield


@pytest.fixture(autouse=True)
def _reset_state() -> Generator[None, None, None]:
  uptimerobot._cache = None
  uptimerobot._first_seen_down.clear()
  yield
  uptimerobot._cache = None
  uptimerobot._first_seen_down.clear()


# ---------------------------------------------------------------------------
# All monitors up — template skipped
# ---------------------------------------------------------------------------


def test_all_up_raises_unavailable() -> None:
  resp = _mock_response(_api_response([_monitor(status=2)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError):
      uptimerobot.get_variables()


def test_all_up_clears_first_seen_state() -> None:
  uptimerobot._first_seen_down[1] = time.monotonic() - 600
  resp = _mock_response(_api_response([_monitor(status=2)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError):
      uptimerobot.get_variables()
  assert len(uptimerobot._first_seen_down) == 0


def test_paused_monitor_treated_as_up() -> None:
  resp = _mock_response(_api_response([_monitor(status=0)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError):
      uptimerobot.get_variables()


def test_empty_monitors_raises_unavailable() -> None:
  resp = _mock_response(_api_response([]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError):
      uptimerobot.get_variables()


# ---------------------------------------------------------------------------
# Outage detection
# ---------------------------------------------------------------------------


def test_down_monitor_returns_variables() -> None:
  resp = _mock_response(_api_response([_monitor(id=1, friendly_name='Prod API', status=9)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['PROD API']]
  assert result['detail'][0][0].startswith('DOWN ')


def test_seems_down_treated_as_down() -> None:
  resp = _mock_response(_api_response([_monitor(id=1, friendly_name='Staging', status=8)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['STAGING']]


def test_monitor_name_uppercased() -> None:
  resp = _mock_response(_api_response([_monitor(friendly_name='my api', status=9)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['MY API']]


def test_down_monitor_tracks_first_seen() -> None:
  resp = _mock_response(_api_response([_monitor(id=42, status=9)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    uptimerobot.get_variables()
  assert 42 in uptimerobot._first_seen_down


def test_duration_increases_on_subsequent_calls() -> None:
  uptimerobot._first_seen_down[1] = time.monotonic() - 900  # 15 min ago
  resp = _mock_response(_api_response([_monitor(id=1, status=9)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['detail'] == [['DOWN 15 MIN']]


# ---------------------------------------------------------------------------
# Multi-monitor state
# ---------------------------------------------------------------------------


def test_shows_longest_down_monitor() -> None:
  now = time.monotonic()
  uptimerobot._first_seen_down[1] = now - 600  # 10 min ago
  uptimerobot._first_seen_down[2] = now - 60  # 1 min ago
  resp = _mock_response(
    _api_response(
      [
        _monitor(id=1, friendly_name='Old Down', status=9),
        _monitor(id=2, friendly_name='New Down', status=9),
      ]
    )
  )
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['OLD DOWN']]
  assert result['detail'] == [['DOWN 10 MIN']]


def test_recovered_monitor_removed_from_tracking() -> None:
  uptimerobot._first_seen_down[1] = time.monotonic() - 300
  uptimerobot._first_seen_down[2] = time.monotonic() - 60
  resp = _mock_response(
    _api_response(
      [
        _monitor(id=1, friendly_name='Still Down', status=9),
        _monitor(id=2, friendly_name='Recovered', status=2),
      ]
    )
  )
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    uptimerobot.get_variables()
  assert 1 in uptimerobot._first_seen_down
  assert 2 not in uptimerobot._first_seen_down


# ---------------------------------------------------------------------------
# API errors
# ---------------------------------------------------------------------------


def test_api_error_raises_unavailable() -> None:
  with patch(
    'integrations.uptimerobot.fetch_with_retry',
    side_effect=requests.ConnectionError('refused'),
  ):
    with pytest.raises(IntegrationDataUnavailableError):
      uptimerobot.get_variables()


def test_api_stat_fail_raises_unavailable() -> None:
  resp = _mock_response({'stat': 'fail', 'error': {'message': 'wrong API key'}})
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError, match='wrong API key'):
      uptimerobot.get_variables()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_fetch() -> None:
  from integrations.http import CacheEntry

  uptimerobot._cache = CacheEntry(
    {
      'monitor': [['CACHED']],
      'detail': [['DOWN 5 MIN']],
    }
  )
  with patch('integrations.uptimerobot.fetch_with_retry') as mock_fetch:
    result = uptimerobot.get_variables()
    mock_fetch.assert_not_called()
  assert result['monitor'] == [['CACHED']]


# ---------------------------------------------------------------------------
# Duration formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'seconds,expected',
  [
    (0, '0 SEC'),
    (1, '1 SEC'),
    (59, '59 SEC'),
    (60, '1 MIN'),
    (840, '14 MIN'),
    (3599, '59 MIN'),
    (3600, '1 HR'),
    (3660, '1H 1M'),
    (7200, '2 HR'),
    (7380, '2H 3M'),
    (86400, '1 DAY'),
    (172800, '2 DAYS'),
    (90000, '1 DAY'),
    (-5, '0 SEC'),
  ],
)
def test_fmt_duration(seconds: int, expected: str) -> None:
  assert uptimerobot._fmt_duration(seconds) == expected
