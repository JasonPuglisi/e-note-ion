# integrations/calendar_schedule.py
#
# Calendar-driven gating for cron-triggered template enqueues.
#
# Reads `vestaboard:<token>` keywords from event descriptions in the user's
# configured `[calendar]` calendars (ICS and CalDAV). Tokens decide whether
# templates are allowed to enqueue while the event is active.
#
# Token grammar (one keyword per description line, case-insensitive):
#   vestaboard:<file_stem>             — open all templates in that file
#   vestaboard:<file_stem>.<template>  — open one specific template
#   vestaboard:!<file_stem>            — close all templates in that file
#   vestaboard:!<file_stem>.<template> — close one specific template
#
# Resolution (deny wins on conflict):
#   1. specific template_id override (allow or deny)
#   2. file-stem override (allow or deny)
#   3. default — closed if matched by [scheduler.calendar_schedule].gated_templates,
#      else open
#
# Section absent from config = feature disabled, no gating applied.

import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from icalendar.cal import Component

logger = logging.getLogger(__name__)

# One keyword per line in event description text. Case-insensitive.
# Token charset: alphanumeric, underscore, dot, with optional leading bang.
_KEYWORD_RE = re.compile(
  r'^\s*vestaboard\s*:\s*(!?\s*[a-zA-Z0-9_.]+)\s*$',
  re.IGNORECASE | re.MULTILINE,
)

# Resolved-override cache: { 'bart.departures': True, 'discogs': False, ... }.
# Refreshed at most once per _CACHE_TTL seconds.
_cache: dict[str, bool] = {}
_cache_at: float = 0.0
_cache_lock = threading.Lock()
_CACHE_TTL = 60  # seconds — re-extract overrides at most this often


def _is_enabled() -> bool:
  """True if the [scheduler.calendar_schedule] section exists in config."""
  import config as _config_mod

  return 'calendar_schedule' in _config_mod._config.get('scheduler', {})


def _gated_templates() -> set[str]:
  """Return the set of template_ids and stems closed by default."""
  import config as _config_mod

  raw = _config_mod._config.get('scheduler', {}).get('calendar_schedule', {}).get('gated_templates', [])
  if not isinstance(raw, list):
    return set()
  return {str(x) for x in raw if isinstance(x, str)}


def _parse_token(token: str) -> tuple[bool, str]:
  """Return (allow, target). allow=False means deny (`!` prefix)."""
  t = token.strip()
  if t.startswith('!'):
    return False, t[1:].strip()
  return True, t


def _extract_keywords(description: str) -> list[tuple[bool, str]]:
  """Return [(allow, target), ...] from one event description string."""
  if not description:
    return []
  return [_parse_token(m.group(1)) for m in _KEYWORD_RE.finditer(description)]


def _is_active_now(component: Component, now: datetime, tz: Any) -> bool:
  """True if this VEVENT covers `now` in the display timezone.

  All-day events span [DTSTART, DTEND) where DTEND defaults to start + 1 day
  for single-day events. Timed events use [DTSTART, DTEND); point-in-time
  events with no DTEND or DURATION are treated as not active (a gate that
  flips for an instant has no useful semantics here).
  """
  from integrations.calendar import _event_end, _event_start, _is_allday

  start = _event_start(component, tz)
  end = _event_end(component, tz, start)
  if _is_allday(component) and end is None:
    end = start + timedelta(days=1)
  if end is None:
    return False
  return start <= now < end


def _fetch_active_events(now: datetime, tz: Any) -> list[Component]:
  """Collect calendar events currently active at `now`.

  Reuses the ICS and CalDAV fetchers in `integrations/calendar.py`, which
  already cache network responses (30-min ICS cache, process-lifetime
  CalDAV calendar cache). Returns an empty list on fetch failure with a
  warning logged — gating must not crash the scheduler.
  """
  import config as _config_mod
  import integrations.calendar as _cal

  cal_cfg = _config_mod._config.get('calendar', {})
  if not isinstance(cal_cfg, dict) or not cal_cfg:
    return []

  active: list[Component] = []

  if cal_cfg.get('urls'):
    try:
      for component, _, _ in _cal._collect_candidates_ics(cal_cfg, now, tz):
        if _is_active_now(component, now, tz):
          active.append(component)
    except Exception as e:  # noqa: BLE001
      logger.warning('calendar_schedule: ICS fetch failed: %s', e)

  if cal_cfg.get('caldav_url'):
    try:
      for component, _, _ in _cal._collect_candidates_caldav(cal_cfg, now, tz):
        if _is_active_now(component, now, tz):
          active.append(component)
    except Exception as e:  # noqa: BLE001
      logger.warning('calendar_schedule: CalDAV fetch failed: %s', e)

  return active


def _resolve_overrides(events: list[Component]) -> dict[str, bool]:
  """Reduce active events to a flat target → allow/deny mapping.

  Applies deny-wins-on-conflict: once a target is denied, subsequent allows
  for the same target are ignored.
  """
  out: dict[str, bool] = {}
  for component in events:
    desc = str(component.get('DESCRIPTION', '') or '')
    for allow, target in _extract_keywords(desc):
      if not target:
        continue
      if out.get(target) is False:
        continue  # deny already won
      out[target] = allow
  return out


def _refresh(now: datetime | None = None) -> None:
  """Refresh the resolved-overrides cache. Acquires _cache_lock."""
  import config as _config_mod
  import integrations.calendar as _cal

  global _cache_at

  tz = _config_mod.get_timezone()
  if now is None:
    now = _cal._get_now(tz)
  # Promote None to local tzinfo so downstream event helpers produce aware
  # datetimes — matches the convention in calendar.get_variables.
  tz = tz or now.tzinfo

  events = _fetch_active_events(now, tz)
  resolved = _resolve_overrides(events)

  with _cache_lock:
    _cache.clear()
    _cache.update(resolved)
    _cache_at = time.monotonic()


def _get_overrides() -> dict[str, bool]:
  """Return the cached overrides, refreshing if stale."""
  with _cache_lock:
    age = time.monotonic() - _cache_at
    fresh = age < _CACHE_TTL and _cache_at > 0
  if fresh:
    return dict(_cache)
  _refresh()
  with _cache_lock:
    return dict(_cache)


def is_open(template_id: str, stem: str) -> bool:
  """True if the named template is currently allowed to enqueue.

  template_id is `<file_stem>.<template_name>` (e.g. 'bart.departures').
  stem is the file stem alone ('bart').

  When [scheduler.calendar_schedule] is absent, returns True (no gating).
  Otherwise applies override resolution and falls back to the default state
  (open unless template_id or stem is in `gated_templates`).
  """
  if not _is_enabled():
    return True

  overrides = _get_overrides()
  if template_id in overrides:
    return overrides[template_id]
  if stem in overrides:
    return overrides[stem]

  gated = _gated_templates()
  return template_id not in gated and stem not in gated


def reset_cache() -> None:
  """Clear the resolved-overrides cache. Intended for tests."""
  global _cache_at
  with _cache_lock:
    _cache.clear()
    _cache_at = 0.0
