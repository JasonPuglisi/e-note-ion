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

import config as _cfg
import integrations.vestaboard as _vb
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry

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

# Tracks when each monitor was first observed as down (monotonic clock).
# Used to compute elapsed downtime for the display. Ephemeral — lost on
# restart; duration resets to 0 on the next detection.
_first_seen_down: dict[int, float] = {}


def _fmt_duration(seconds: int) -> str:
  """Format a duration in seconds for display (fits 15-col Note row)."""
  if seconds < 0:
    seconds = 0
  if seconds < 60:
    return f'{seconds} SEC'
  if seconds < 3600:
    return f'{seconds // 60} MIN'
  hours = seconds // 3600
  if hours < 24:
    mins = (seconds % 3600) // 60
    return f'{hours}H {mins}M' if mins else f'{hours} HR'
  days = hours // 24
  return f'{days} DAY' if days == 1 else f'{days} DAYS'


def _fetch_monitors() -> list[dict[str, Any]]:
  """Fetch all monitors from the UptimeRobot API.

  Returns the list of monitor dicts from the response. Raises
  IntegrationDataUnavailableError on API errors.
  """
  api_key = _cfg.get('uptimerobot', 'api_key')

  try:
    r = fetch_with_retry(
      'POST',
      _API_URL,
      data={'api_key': api_key, 'format': 'json'},
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

  # Find monitors in down state.
  current_down: dict[int, str] = {}
  for m in monitors:
    status = m.get('status', 0)
    if status in _DOWN_STATUSES:
      current_down[int(m['id'])] = str(m.get('friendly_name', '')).strip()

  if not current_down:
    # All monitors up — clean up tracking state.
    _first_seen_down.clear()
    raise IntegrationDataUnavailableError('UptimeRobot: all monitors up')

  # Track first-seen time for newly down monitors.
  now = time.monotonic()
  for mid in current_down:
    if mid not in _first_seen_down:
      _first_seen_down[mid] = now
  # Clean up monitors that recovered.
  for mid in list(_first_seen_down):
    if mid not in current_down:
      del _first_seen_down[mid]

  # Show the monitor that has been down the longest.
  longest_id = min(_first_seen_down, key=lambda k: _first_seen_down[k])
  display_name = _vb.truncate_line(current_down[longest_id].upper(), _vb.model.cols, 'ellipsis')
  duration = int(now - _first_seen_down[longest_id])

  result: dict[str, list[list[str]]] = {
    'monitor': [[display_name]],
    'detail': [[f'DOWN {_fmt_duration(duration)}']],
  }

  _cache = CacheEntry(result)
  return result
