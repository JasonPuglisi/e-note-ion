# integrations/uptimerobot.py
#
# UptimeRobot integration — service outage alerts via API polling.
#
# Polls the UptimeRobot /getMonitors API on a cron schedule. When all monitors
# are up, raises IntegrationDataUnavailableError so the template is silently
# skipped. When any monitor is down (status 8 or 9), returns variables for an
# outage display card with the monitor name and elapsed downtime.
#
# During an active outage, refresh_interval keeps the display current (duration
# ticks up each refresh). When all monitors recover, the next poll raises
# IntegrationDataUnavailableError and normal content resumes.
#
# Free-tier compatible — uses the /getMonitors REST endpoint (10 req/min limit).
# No webhook or paid plan features required.

import logging
import time
from typing import Any

import requests

import integrations.vestaboard as _vb
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, raise_for_credentials

logger = logging.getLogger(__name__)

_API_URL = 'https://api.uptimerobot.com/v2/getMonitors'

# Monitor status codes from UptimeRobot API.
_STATUS_SEEMS_DOWN = 8
_STATUS_DOWN = 9
_DOWN_STATUSES = frozenset({_STATUS_SEEMS_DOWN, _STATUS_DOWN})

# Cache API responses for 30 s to avoid redundant calls when the scheduler
# invokes get_variables() in quick succession (e.g. cron fire + refresh).
_CACHE_TTL = 30
_cache: CacheEntry | None = None

# Fallback: tracks first-observation time (wall clock) for down monitors when
# the API's log data is missing or unparseable. Normally the API provides the
# actual outage start via logs[0].datetime, which survives restarts.
_first_seen_down: dict[int, float] = {}

# UptimeRobot log entry types.
_LOG_TYPE_DOWN = 1


def _fmt_duration(seconds: int) -> str:
  """Format an outage duration for display.

  Single-unit, rounded-down. Steps coarsen as duration grows to keep the
  display readable and reduce flap updates on long outages:
    < 60 s         → '0 MINUTES'
    < 10 min       → 'N MINUTE' / 'N MINUTES' (per-minute)
    < 60 min       → 'N MINUTES' rounded down to nearest 5
    < 24 hr        → 'N HOUR' / 'N HOURS' (rounded down)
    >= 24 hr       → 'N DAY' / 'N DAYS' (rounded down)
  Longest output 'N MINUTES' fits 15-col Note with 'DOWN ' prefix.
  """
  if seconds < 0:
    seconds = 0
  if seconds < 60:
    return '0 MINUTES'
  mins = seconds // 60
  if mins < 10:
    return '1 MINUTE' if mins == 1 else f'{mins} MINUTES'
  if mins < 60:
    return f'{(mins // 5) * 5} MINUTES'
  hours = mins // 60
  if hours < 24:
    return '1 HOUR' if hours == 1 else f'{hours} HOURS'
  days = hours // 24
  return '1 DAY' if days == 1 else f'{days} DAYS'


def _outage_start(monitor: dict[str, Any], mid: int, now: float) -> float:
  """Return the Unix timestamp when this monitor's current outage began.

  Prefers the most recent type=1 log entry's `datetime` field (true start,
  survives process restarts). Falls back to first-observation time when the
  API omits logs or the entry is unparseable.
  """
  logs = monitor.get('logs') or []
  if logs and logs[0].get('type') == _LOG_TYPE_DOWN:
    dt = logs[0].get('datetime')
    if isinstance(dt, (int, float)) and dt > 0:
      return float(dt)
  if mid not in _first_seen_down:
    _first_seen_down[mid] = now
  return _first_seen_down[mid]


def _fetch_monitors() -> list[dict[str, Any]]:
  """Fetch all monitors (with latest log) from the UptimeRobot API.

  Returns the list of monitor dicts from the response. Raises
  IntegrationDataUnavailableError on API errors.
  """
  import config as _config_mod

  api_key = _config_mod.get('uptimerobot', 'api_key')

  try:
    r = fetch_with_retry(
      'POST',
      _API_URL,
      data={
        'api_key': api_key,
        'format': 'json',
        'logs': '1',
        'logs_limit': '1',
      },
      timeout=10,
    )
    r.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'UptimeRobot: API request failed — {e}') from None

  data = r.json()
  if data.get('stat') != 'ok':
    error_msg = data.get('error', {}).get('message', 'unknown error')
    raise IntegrationDataUnavailableError(f'UptimeRobot: API returned error — {error_msg}')

  return data.get('monitors', [])


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch monitor statuses and return variables for the outage template.

  Returns outage display variables when any monitor is down. Raises
  IntegrationDataUnavailableError when all monitors are up (template is
  silently skipped).
  """
  global _cache

  if _cache is not None and _cache.is_valid(_CACHE_TTL):
    logger.debug('uptimerobot: cache hit')
    return _cache.value

  monitors = _fetch_monitors()
  now = time.time()

  # Find monitors in down state and resolve outage start for each.
  down_starts: dict[int, float] = {}
  down_names: dict[int, str] = {}
  for m in monitors:
    if m.get('status', 0) not in _DOWN_STATUSES:
      continue
    mid = int(m['id'])
    down_names[mid] = str(m.get('friendly_name', '')).strip()
    down_starts[mid] = _outage_start(m, mid, now)

  if not down_names:
    # All monitors up — clean up fallback tracking state.
    _first_seen_down.clear()
    raise IntegrationDataUnavailableError('UptimeRobot: all monitors up', expected=True)

  # Clean up fallback entries for monitors that recovered.
  for mid in list(_first_seen_down):
    if mid not in down_names:
      del _first_seen_down[mid]

  # Show the monitor whose outage started earliest.
  longest_id = min(down_starts, key=lambda k: down_starts[k])
  display_name = _vb.truncate_line(down_names[longest_id].upper(), _vb.model.cols, 'ellipsis')
  duration = int(now - down_starts[longest_id])

  result: dict[str, list[list[str]]] = {
    'monitor': [[display_name]],
    'detail': [[f'DOWN {_fmt_duration(duration)}']],
  }

  _cache = CacheEntry(result)
  return result


def preflight() -> None:
  """Validate the UptimeRobot API key at startup (#503).

  UptimeRobot answers a bad key with HTTP 401, so this does not need to inspect
  the body — unlike get_variables, which must distinguish "all monitors up"
  (an expected-empty result) from a real failure.
  """
  import config as _config_mod

  api_key = _config_mod.get('uptimerobot', 'api_key')
  r = fetch_with_retry('POST', _API_URL, data={'api_key': api_key, 'format': 'json', 'limit': 1}, timeout=10)
  raise_for_credentials(r, 'uptimerobot')
