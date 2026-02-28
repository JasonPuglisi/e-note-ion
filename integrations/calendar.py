# integrations/calendar.py
#
# Calendar integration.
#
# Fetches events from ICS feeds and/or a private iCloud CalDAV account and
# returns them as a variables dict for use with content templates.
# Both modes can be active simultaneously — events are merged and sorted.
#
# ICS mode — public or secret-address feeds (Google Calendar, iCloud public):
#   [calendar]
#   urls = ["https://..."]
#   colors = ["B", "G"]   # optional; R/O/Y/G/B/V/W/K, parallel to urls
#
# CalDAV mode — private iCloud:
#   [calendar]
#   caldav_url = "https://caldav.icloud.com/"
#   username = "you@icloud.com"
#   password = "xxxx-xxxx-xxxx-xxxx"   # app-specific password from appleid.apple.com
#   calendar_names = ["Work", "Personal"]  # optional; default: all calendars
#
# Both modes may be configured at once; events are merged and sorted together.
#
# Events are sorted: timed (soonest first) then all-day (alphabetical).
# Source order (URL index, then CalDAV calendar order) breaks start-time ties.
# Timed events that have already ended are excluded.
# Events with no SUMMARY or STATUS:CANCELLED are silently skipped.
# If no events remain after filtering, raises IntegrationDataUnavailableError.

import math
import time
from datetime import date, datetime, timedelta
from typing import Any

import recurring_ical_events
import requests
from icalendar import Calendar
from icalendar.cal import Component

from exceptions import IntegrationDataUnavailableError
from integrations.http import fetch_with_retry

# Approximate sRGB values for each Vestaboard color letter. Used for
# nearest-color matching when reading hex color values from ICS/CalDAV data.
_COLOR_RGB: dict[str, tuple[int, int, int]] = {
  'R': (255, 59, 48),
  'O': (255, 149, 0),
  'Y': (255, 204, 0),
  'G': (52, 199, 89),
  'B': (0, 122, 255),
  'V': (175, 82, 222),
  'W': (255, 255, 255),
  'K': (0, 0, 0),
}

_VALID_COLORS = frozenset(_COLOR_RGB)

# Per-URL ICS bytes cache: url → (raw_bytes, monotonic_fetch_time).
_ics_cache: dict[str, tuple[bytes, float]] = {}
_ICS_CACHE_TTL = 30 * 60  # 30 minutes

# CalDAV calendar cache: list of (caldav.Calendar, color_tag | None) pairs.
# None = not yet populated.
_caldav_cache: list[tuple[Any, str | None]] | None = None


# ── Color helpers ──────────────────────────────────────────────────────────────


def _wrap_color(letter: str) -> str:
  """Validate a color letter from config and wrap it as a Vestaboard tag.

  Raises ValueError if the letter is not a recognised Vestaboard color.
  Input is case-insensitive.
  """
  upper = letter.strip().upper()
  if upper not in _VALID_COLORS:
    raise ValueError(f'Invalid calendar color {letter!r} — valid options: R, O, Y, G, B, V, W, K')
  return f'[{upper}]'


def _nearest_color_tag(hex_color: str) -> str:
  """Return the nearest Vestaboard color tag for a hex color string.

  Accepts #RRGGBB or #RRGGBBAA (Apple's format). Alpha channel is ignored.
  Finds the closest color by Euclidean distance in RGB space.
  """
  hex_color = hex_color.strip().lstrip('#')
  r = int(hex_color[0:2], 16)
  g = int(hex_color[2:4], 16)
  b = int(hex_color[4:6], 16)

  best_letter = 'W'
  best_dist = float('inf')
  for letter, (cr, cg, cb) in _COLOR_RGB.items():
    dist = math.sqrt((r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2)
    if dist < best_dist:
      best_dist = dist
      best_letter = letter
  return f'[{best_letter}]'


# ── Timezone ───────────────────────────────────────────────────────────────────


def _display_tz() -> Any:
  """Return the display timezone (ZoneInfo | None for system local).

  Reads [scheduler].timezone from config. Returns None to use system local
  timezone, matching the behaviour of config.get_timezone().
  """
  import config as _cfg

  return _cfg.get_timezone()


def _get_now(tz: Any) -> datetime:
  """Return current time in the given timezone. Extracted for testability."""
  return datetime.now(tz) if tz else datetime.now().astimezone()


# ── ICS fetching and parsing ───────────────────────────────────────────────────


def _fetch_ics_bytes(url: str) -> bytes:
  """Fetch raw ICS bytes from a URL, with per-URL caching.

  Returns cached bytes if within _ICS_CACHE_TTL. On transient failure,
  returns cached bytes if available (even if stale). Raises
  IntegrationDataUnavailableError on cold-start fetch failure.
  """
  cached = _ics_cache.get(url)
  if cached is not None:
    data, fetched_at = cached
    if time.monotonic() - fetched_at <= _ICS_CACHE_TTL:
      return data

  try:
    r = fetch_with_retry('GET', url, timeout=15)
    r.raise_for_status()
    data = r.content
    _ics_cache[url] = (data, time.monotonic())
    return data
  except requests.RequestException as e:
    if cached is not None:
      print(f'calendar: fetch failed for {url!r}, serving stale cache — {e}')
      return cached[0]
    raise IntegrationDataUnavailableError(f'calendar: fetch failed for {url!r} — {e}') from None


def _ics_calendar_color(cal: Calendar) -> str | None:
  """Return the nearest Vestaboard color tag from X-APPLE-CALENDAR-COLOR, or None."""
  raw = cal.get('X-APPLE-CALENDAR-COLOR')
  if not raw:
    return None
  try:
    return _nearest_color_tag(str(raw))
  except ValueError, IndexError:
    return None


# ── Event helpers ──────────────────────────────────────────────────────────────


def _is_allday(component: Component) -> bool:
  """Return True if the event is an all-day event (DATE-only DTSTART)."""
  dtstart = component.get('DTSTART')
  if dtstart is None:
    return False
  return isinstance(dtstart.dt, date) and not isinstance(dtstart.dt, datetime)


def _event_start(component: Component, tz: Any) -> datetime:
  """Return the event start as a timezone-aware datetime in the display timezone.

  For all-day events, returns midnight of the start date in the display TZ.
  For timed events, converts to display TZ (attaches TZ to floating times).
  """
  dtstart = component.get('DTSTART')
  dt = dtstart.dt if dtstart else datetime.now(tz)

  if isinstance(dt, date) and not isinstance(dt, datetime):
    # All-day event: treat as midnight in display TZ.
    result = datetime(dt.year, dt.month, dt.day, tzinfo=tz)
  elif dt.tzinfo is None:
    # Floating time — attach display TZ.
    result = dt.replace(tzinfo=tz)
  else:
    result = dt.astimezone(tz)
  return result


def _event_end(component: Component, tz: Any, start: datetime) -> datetime | None:
  """Return the event end as a timezone-aware datetime in the display timezone.

  Handles DTEND, DURATION, and all-day end dates. Returns None for
  point-in-time events with no end or duration.
  """
  dtend = component.get('DTEND')
  if dtend is not None:
    dt = dtend.dt
    if isinstance(dt, date) and not isinstance(dt, datetime):
      return datetime(dt.year, dt.month, dt.day, tzinfo=tz)
    if dt.tzinfo is None:
      return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)

  duration = component.get('DURATION')
  if duration is not None:
    return start + duration.dt

  return None


def _format_event(component: Component, tz: Any, color_tag: str | None) -> str | None:
  """Format a VEVENT component as a single display line, or None to skip.

  Timed events: '[TAG ]HH:MM TITLE'
  All-day events: '[TAG ]TITLE'

  Returns None if the event has no SUMMARY or is CANCELLED.
  """
  summary = component.get('SUMMARY')
  if not summary:
    return None
  if str(component.get('STATUS', '')).upper() == 'CANCELLED':
    return None

  title = str(summary).upper()
  prefix = f'{color_tag} ' if color_tag else ''

  if _is_allday(component):
    return f'{prefix}{title}'

  start = _event_start(component, tz)
  return f'{prefix}{start.strftime("%H:%M")} {title}'


# ── ICS mode ───────────────────────────────────────────────────────────────────


def _collect_candidates_ics(
  cal_cfg: dict[str, Any],
  now: datetime,
  tz: Any,
  index_offset: int = 0,
) -> list[tuple[Component, str | None, int]]:
  """Collect event candidates from one or more ICS URLs.

  Returns (component, color_tag, cal_index) tuples for events in today's
  window. One URL failing serves stale cache if available; cold-start
  failures log a warning and skip that URL.
  """
  urls: list[str] = cal_cfg.get('urls', [])
  color_letters: list[str] = cal_cfg.get('colors', [])

  window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
  window_end = window_start + timedelta(days=1)

  candidates: list[tuple[Component, str | None, int]] = []

  for i, url in enumerate(urls):
    configured_color: str | None = None
    if i < len(color_letters):
      try:
        configured_color = _wrap_color(color_letters[i])
      except ValueError as e:
        print(f'calendar: {e}')

    try:
      raw = _fetch_ics_bytes(url)
    except IntegrationDataUnavailableError as e:
      print(f'calendar: skipping URL {i + 1} — {e}')
      continue

    cal = Calendar.from_ical(raw)
    auto_color = _ics_calendar_color(cal)
    color_tag = configured_color or auto_color

    try:
      occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
    except Exception as e:  # noqa: BLE001
      print(f'calendar: failed to expand events for URL {i + 1} — {e}')
      continue

    for component in occurrences:
      candidates.append((component, color_tag, index_offset + i))

  return candidates


# ── CalDAV mode ────────────────────────────────────────────────────────────────


def _get_caldav_calendars(
  caldav_url: str,
  username: str,
  password: str,
  calendar_names: list[str] | None,
) -> list[tuple[Any, str | None]]:
  """Discover iCloud CalDAV calendars and their colors. Cached for process lifetime.

  Returns a list of (caldav.Calendar, color_tag | None) pairs in the order
  specified by calendar_names (or all calendars if calendar_names is empty).
  """
  global _caldav_cache

  if _caldav_cache is not None:
    return _caldav_cache

  import caldav
  from caldav.elements import ical as caldav_ical

  client = caldav.DAVClient(
    url=caldav_url,
    username=username,
    password=password,
    timeout=15,
  )
  try:
    principal = client.principal()
    all_cals = principal.calendars()
  except Exception as e:  # noqa: BLE001
    raise IntegrationDataUnavailableError(f'calendar: CalDAV connection failed — {e}') from None

  result: list[tuple[Any, str | None]] = []

  # Filter to requested calendar names if specified.
  name_filter: set[str] | None = set(calendar_names) if calendar_names else None

  for cal in all_cals:
    cal_name = str(cal.name or '')
    if name_filter is not None and cal_name not in name_filter:
      continue

    color_tag: str | None = None
    try:
      props = cal.get_properties([caldav_ical.CalendarColor()])
      raw_color = props.get('{http://apple.com/ns/ical/}calendar-color') if props else None
      if raw_color:
        color_tag = _nearest_color_tag(str(raw_color))
    except Exception:  # noqa: BLE001  # nosec B110 — color is optional; CalDAV property fetch may fail on non-Apple servers
      pass

    result.append((cal, color_tag))

  # If calendar_names was specified, reorder to match the requested order.
  if name_filter is not None:
    order = {name: i for i, name in enumerate(calendar_names or [])}
    result.sort(key=lambda item: order.get(str(item[0].name or ''), len(order)))

  _caldav_cache = result
  return result


def _collect_candidates_caldav(
  cal_cfg: dict[str, Any],
  now: datetime,
  tz: Any,
  index_offset: int = 0,
) -> list[tuple[Component, str | None, int]]:
  """Collect event candidates from iCloud CalDAV.

  Returns (component, color_tag, cal_index) tuples for events in today's
  window. Raises IntegrationDataUnavailableError on missing credentials or
  connection failure. Returns an empty list if no calendars are found or
  all calendars fail to fetch.
  """
  caldav_url = cal_cfg.get('caldav_url', '')
  username = cal_cfg.get('username', '')
  password = cal_cfg.get('password', '')
  calendar_names_raw = cal_cfg.get('calendar_names')
  calendar_names: list[str] | None = list(calendar_names_raw) if calendar_names_raw else None

  if not caldav_url or not username or not password:
    raise IntegrationDataUnavailableError(
      'calendar: CalDAV mode requires caldav_url, username, and password in config.toml'
    )

  calendars = _get_caldav_calendars(caldav_url, username, password, calendar_names)
  if not calendars:
    print('calendar: no CalDAV calendars found')
    return []

  window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
  window_end = window_start + timedelta(days=1)

  candidates: list[tuple[Component, str | None, int]] = []

  for i, (cal, color_tag) in enumerate(calendars):
    try:
      events = cal.events()
    except Exception as e:  # noqa: BLE001
      print(f'calendar: failed to fetch events from CalDAV calendar {i + 1} — {e}')
      continue

    # Build a merged Calendar for client-side recurring event expansion.
    merged = Calendar()
    for event_obj in events:
      try:
        for component in event_obj.icalendar_object.subcomponents:
          if component.name == 'VEVENT':
            merged.add_component(component)
      except Exception:  # noqa: BLE001  # nosec B112 — skip malformed CalDAV event objects; continue to next
        continue

    try:
      occurrences = recurring_ical_events.of(merged).between(window_start, window_end)
    except Exception as e:  # noqa: BLE001
      print(f'calendar: failed to expand CalDAV events for calendar {i + 1} — {e}')
      continue

    for component in occurrences:
      candidates.append((component, color_tag, index_offset + i))

  return candidates


# ── Sorting and formatting ─────────────────────────────────────────────────────


def _sort_and_format(
  candidates: list[tuple[Component, str | None, int]],
  now: datetime,
  tz: Any,
) -> list[str]:
  """Filter, sort, and format event candidates into display lines.

  Sort order:
    1. Timed events by start time (soonest first).
    2. All-day / multi-day events alphabetically by title.
    3. Source index as tiebreaker (ICS URLs first, then CalDAV calendars).

  Timed events whose end time is already in the past are excluded.
  Events with no SUMMARY or STATUS:CANCELLED are excluded.
  Returns up to 10 lines (scheduler row limit handles further truncation).
  """
  lines: list[str] = []

  def sort_key(item: tuple[Component, str | None, int]) -> tuple:
    component, _, cal_index = item
    allday = _is_allday(component)
    if allday:
      title = str(component.get('SUMMARY', '')).upper()
      return (1, '', title, cal_index)
    start = _event_start(component, tz)
    return (0, start.isoformat(), '', cal_index)

  for component, color_tag, _ in sorted(candidates, key=sort_key):
    # Skip ended timed events.
    if not _is_allday(component):
      start = _event_start(component, tz)
      end = _event_end(component, tz, start)
      if end is not None and end < now:
        continue

    line = _format_event(component, tz, color_tag)
    if line is not None:
      lines.append(line)

    if len(lines) >= 10:
      break

  return lines


# ── Entry point ────────────────────────────────────────────────────────────────


def get_variables() -> dict[str, list[list[str]]]:
  """Return today's calendar events as a variables dict for template rendering.

  Returns key 'events' as a single option containing one line per event.
  Collects from ICS URLs and/or CalDAV if configured (both may be active).
  Raises IntegrationDataUnavailableError if no events are available.
  """
  import config as _cfg

  cal_cfg: dict[str, Any] = _cfg._config.get('calendar', {})
  if not cal_cfg:
    raise IntegrationDataUnavailableError('calendar: no [calendar] section in config.toml')

  has_ics = 'urls' in cal_cfg
  has_caldav = 'caldav_url' in cal_cfg

  if not has_ics and not has_caldav:
    raise IntegrationDataUnavailableError(
      'calendar: [calendar] section must have urls (ICS) and/or caldav_url/username/password (CalDAV)'
    )

  tz = _display_tz()
  now = _get_now(tz)
  tz_ = tz or now.tzinfo

  candidates: list[tuple[Component, str | None, int]] = []

  if has_ics:
    candidates += _collect_candidates_ics(cal_cfg, now, tz_)

  if has_caldav:
    ics_count = len(cal_cfg.get('urls', []))
    candidates += _collect_candidates_caldav(cal_cfg, now, tz_, index_offset=ics_count)

  lines = _sort_and_format(candidates, now, tz_)

  if not lines:
    raise IntegrationDataUnavailableError('calendar: no events today')

  return {'events': [lines]}
