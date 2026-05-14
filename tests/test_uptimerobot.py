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
  down_seconds_ago: int | None = None,
  logs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  m: dict[str, Any] = {
    'id': id,
    'friendly_name': friendly_name,
    'url': 'https://api.example.com',
    'status': status,
  }
  if logs is not None:
    m['logs'] = logs
  elif down_seconds_ago is not None:
    m['logs'] = [{'type': 1, 'datetime': int(time.time()) - down_seconds_ago, 'duration': 0}]
  return m


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'uptimerobot': {'api_key': 'test-key'}})
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


def test_all_up_clears_fallback_state() -> None:
  uptimerobot._first_seen_down[1] = time.time() - 600
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
  resp = _mock_response(_api_response([_monitor(id=1, friendly_name='Prod API', status=9, down_seconds_ago=120)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['PROD API']]
  assert result['detail'] == [['DOWN 2 MINUTES']]


def test_seems_down_treated_as_down() -> None:
  resp = _mock_response(_api_response([_monitor(id=1, friendly_name='Staging', status=8, down_seconds_ago=60)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['STAGING']]


def test_monitor_name_uppercased() -> None:
  resp = _mock_response(_api_response([_monitor(friendly_name='my api', status=9, down_seconds_ago=60)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['MY API']]


def test_duration_uses_api_log_datetime() -> None:
  """First detection should reflect actual outage start, not observation time."""
  resp = _mock_response(_api_response([_monitor(id=1, status=9, down_seconds_ago=900)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  # 15 minutes — rounded-to-5 tier
  assert result['detail'] == [['DOWN 15 MINUTES']]


def test_duration_survives_restart() -> None:
  """Even with no prior observation state, the API log gives true elapsed time."""
  assert len(uptimerobot._first_seen_down) == 0
  resp = _mock_response(_api_response([_monitor(id=1, status=9, down_seconds_ago=3600)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['detail'] == [['DOWN 1 HOUR']]
  # Fallback dict is not used when API logs are present.
  assert len(uptimerobot._first_seen_down) == 0


# ---------------------------------------------------------------------------
# Fallback: API logs missing or unparseable
# ---------------------------------------------------------------------------


def test_fallback_when_logs_missing() -> None:
  """No `logs` field → falls back to first-observation time."""
  resp = _mock_response(_api_response([_monitor(id=1, status=9, logs=None)]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert 1 in uptimerobot._first_seen_down
  assert result['detail'] == [['DOWN 0 MINUTES']]


def test_fallback_when_logs_empty() -> None:
  resp = _mock_response(_api_response([_monitor(id=1, status=9, logs=[])]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert 1 in uptimerobot._first_seen_down
  assert result['detail'] == [['DOWN 0 MINUTES']]


def test_fallback_when_top_log_is_up() -> None:
  """Most recent log is type=2 (up); fall back rather than picking older down log."""
  resp = _mock_response(
    _api_response(
      [
        _monitor(
          id=1,
          status=9,
          logs=[
            {'type': 2, 'datetime': int(time.time()) - 100, 'duration': 0},
            {'type': 1, 'datetime': int(time.time()) - 9999, 'duration': 100},
          ],
        )
      ]
    )
  )
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    uptimerobot.get_variables()
  assert 1 in uptimerobot._first_seen_down


def test_fallback_when_log_datetime_invalid() -> None:
  resp = _mock_response(_api_response([_monitor(id=1, status=9, logs=[{'type': 1, 'datetime': 0, 'duration': 0}])]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    uptimerobot.get_variables()
  assert 1 in uptimerobot._first_seen_down


# ---------------------------------------------------------------------------
# Multi-monitor state
# ---------------------------------------------------------------------------


def test_shows_earliest_outage_monitor() -> None:
  resp = _mock_response(
    _api_response(
      [
        _monitor(id=1, friendly_name='Old Down', status=9, down_seconds_ago=600),
        _monitor(id=2, friendly_name='New Down', status=9, down_seconds_ago=60),
      ]
    )
  )
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    result = uptimerobot.get_variables()
  assert result['monitor'] == [['OLD DOWN']]
  assert result['detail'] == [['DOWN 10 MINUTES']]


def test_recovered_monitor_removed_from_fallback() -> None:
  uptimerobot._first_seen_down[1] = time.time() - 300
  uptimerobot._first_seen_down[2] = time.time() - 60
  resp = _mock_response(
    _api_response(
      [
        _monitor(id=1, friendly_name='Still Down', status=9, down_seconds_ago=300),
        _monitor(id=2, friendly_name='Recovered', status=2),
      ]
    )
  )
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp):
    uptimerobot.get_variables()
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


def test_fetch_requests_logs() -> None:
  """The API request includes `logs=1, logs_limit=1` so the latest log is returned."""
  resp = _mock_response(_api_response([]))
  with patch('integrations.uptimerobot.fetch_with_retry', return_value=resp) as mock_fetch:
    with pytest.raises(IntegrationDataUnavailableError):
      uptimerobot.get_variables()
  kwargs = mock_fetch.call_args.kwargs
  assert kwargs['data']['logs'] == '1'
  assert kwargs['data']['logs_limit'] == '1'


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_fetch() -> None:
  from integrations.http import CacheEntry

  uptimerobot._cache = CacheEntry(
    {
      'monitor': [['CACHED']],
      'detail': [['DOWN 5 MINUTES']],
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
    # Sub-minute floors to 0 MINUTES
    (-5, '0 MINUTES'),
    (0, '0 MINUTES'),
    (1, '0 MINUTES'),
    (59, '0 MINUTES'),
    # Per-minute tier (1-9 min) with singular at 1
    (60, '1 MINUTE'),
    (119, '1 MINUTE'),
    (120, '2 MINUTES'),
    (540, '9 MINUTES'),
    (599, '9 MINUTES'),
    # 5-minute rounding tier (10-59 min)
    (600, '10 MINUTES'),
    (840, '10 MINUTES'),
    (899, '10 MINUTES'),
    (900, '15 MINUTES'),
    (3299, '50 MINUTES'),
    (3300, '55 MINUTES'),
    (3599, '55 MINUTES'),
    # Hour tier (1-23 hr) with singular at 1
    (3600, '1 HOUR'),
    (5400, '1 HOUR'),
    (6600, '1 HOUR'),
    (7199, '1 HOUR'),
    (7200, '2 HOURS'),
    (82800, '23 HOURS'),
    (86399, '23 HOURS'),
    # Day tier with singular at 1
    (86400, '1 DAY'),
    (129600, '1 DAY'),
    (172799, '1 DAY'),
    (172800, '2 DAYS'),
  ],
)
def test_fmt_duration(seconds: int, expected: str) -> None:
  assert uptimerobot._fmt_duration(seconds) == expected
