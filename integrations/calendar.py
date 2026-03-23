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
#
# Birthdays mode — iCloud Contacts via CardDAV:
#   [calendar]
#   carddav_url = "https://contacts.icloud.com/"
#   username = "you@icloud.com"
#   password = "xxxx-xxxx-xxxx-xxxx"   # same app-specific password
#   birthdays_lookahead_days = 7        # optional; default 7
#
# Omit carddav_url to disable birthdays entirely. get_variables_birthdays()
# raises IntegrationDataUnavailableError immediately if carddav_url is absent.

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET  # nosec B405 — XML from authenticated Apple API

import recurring_ical_events
import requests
from icalendar import Calendar
from icalendar.cal import Component

from exceptions import IntegrationDataUnavailableError
from integrations.color import hex_to_color_tag
from integrations.http import fetch_with_retry

logger = logging.getLogger(__name__)

_VALID_COLORS = frozenset('ROYGBVWK')

# Per-URL ICS bytes cache: url → (raw_bytes, monotonic_fetch_time).
_ics_cache: dict[str, tuple[bytes, float]] = {}
_ICS_CACHE_TTL = 30 * 60  # 30 minutes

# CalDAV calendar cache: list of (caldav.Calendar, color_tag | None) pairs.
# None = not yet populated.
_caldav_cache: list[tuple[Any, str | None]] | None = None

# CardDAV addressbook URL cache (process lifetime — URL structure never changes).
_carddav_addressbook_url: str | None = None

# CardDAV home URL cache (process lifetime — populated alongside addressbook URL).
_carddav_home_url: str | None = None

# Birthday contacts cache: (contacts, monotonic_fetch_time) or None.
# contacts is a list of (display_name, month, day).
_birthday_cache: tuple[list[tuple[str, int, int]], float] | None = None
_BIRTHDAY_CACHE_TTL = 24 * 60 * 60  # 24 hours

# Self contact cache: (display_name, month, day) or None (process lifetime).
# Populated by _resolve_self_contact(); BDAY never changes in practice.
_self_contact_cache: tuple[str, int, int] | None = None


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


# ── Timezone ───────────────────────────────────────────────────────────────────


def _display_tz() -> Any:
  """Return the display timezone (ZoneInfo | None for system local).

  Reads [scheduler].timezone from config. Returns None to use system local
  timezone, matching the behaviour of config.get_timezone().
  """
  import config as _config_mod

  return _config_mod.get_timezone()


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
    age = time.monotonic() - fetched_at
    if age <= _ICS_CACHE_TTL:
      logger.debug('calendar: ICS cache hit for %r (%.0fs old)', url, age)
      return data

  try:
    r = fetch_with_retry('GET', url, timeout=15)
    r.raise_for_status()
    data = r.content
    _ics_cache[url] = (data, time.monotonic())
    logger.debug('calendar: fetched ICS from %r (%d bytes)', url, len(data))
    return data
  except requests.RequestException as e:
    if cached is not None:
      logger.warning('calendar: fetch failed for %r, serving stale cache — %s', url, e)
      return cached[0]
    raise IntegrationDataUnavailableError(f'calendar: fetch failed for {url!r} — {e}') from None


def _ics_calendar_color(cal: Calendar) -> str | None:
  """Return the nearest Vestaboard color tag from X-APPLE-CALENDAR-COLOR, or None."""
  raw = cal.get('X-APPLE-CALENDAR-COLOR')
  if not raw:
    return None
  try:
    return hex_to_color_tag(str(raw))
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
        logger.warning('calendar: %s', e)

    try:
      raw = _fetch_ics_bytes(url)
    except IntegrationDataUnavailableError as e:
      logger.warning('calendar: skipping URL %d — %s', i + 1, e)
      continue

    cal = Calendar.from_ical(raw)
    auto_color = _ics_calendar_color(cal)
    color_tag = configured_color or auto_color

    try:
      occurrences = recurring_ical_events.of(cal).between(window_start, window_end)
    except Exception as e:  # noqa: BLE001
      logger.warning('calendar: failed to expand events for URL %d — %s', i + 1, e)
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
    logger.debug('calendar: CalDAV cache hit (%d calendar(s))', len(_caldav_cache))
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
        color_tag = hex_to_color_tag(str(raw_color))
    except Exception:  # noqa: BLE001  # nosec B110 — color is optional; CalDAV property fetch may fail on non-Apple servers
      pass

    result.append((cal, color_tag))

  # If calendar_names was specified, reorder to match the requested order.
  if name_filter is not None:
    order = {name: i for i, name in enumerate(calendar_names or [])}
    result.sort(key=lambda item: order.get(str(item[0].name or ''), len(order)))

  logger.debug('calendar: CalDAV discovered %d calendar(s)', len(result))
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
    logger.warning('calendar: no CalDAV calendars found')
    return []

  window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
  window_end = window_start + timedelta(days=1)

  candidates: list[tuple[Component, str | None, int]] = []

  for i, (cal, color_tag) in enumerate(calendars):
    try:
      events = cal.events()
    except Exception as e:  # noqa: BLE001
      logger.warning('calendar: failed to fetch events from CalDAV calendar %d — %s', i + 1, e)
      continue

    # Build a merged Calendar for client-side recurring event expansion.
    merged = Calendar()
    for event_obj in events:
      try:
        for component in event_obj.icalendar_instance.subcomponents:
          if component.name == 'VEVENT':
            merged.add_component(component)
      except Exception:  # noqa: BLE001  # nosec B112 — skip malformed CalDAV event objects; continue to next
        continue

    try:
      occurrences = recurring_ical_events.of(merged).between(window_start, window_end)
    except Exception as e:  # noqa: BLE001
      logger.warning('calendar: failed to expand CalDAV events for calendar %d — %s', i + 1, e)
      continue

    for component in occurrences:
      candidates.append((component, color_tag, index_offset + i))

  return candidates


# ── CardDAV / birthdays ────────────────────────────────────────────────────────


def _resolve_display_name(nickname: str | None, fn_parts: list[str]) -> str:
  """Return the preferred display name for a contact, uppercased.

  Priority: nickname → first + last initial → first name only.
  """
  if nickname and nickname.strip():
    return nickname.strip().upper()
  if len(fn_parts) >= 2:
    return f'{fn_parts[0].upper()} {fn_parts[-1][0].upper()}'
  return fn_parts[0].upper() if fn_parts else ''


def _parse_bday(bday_str: str) -> tuple[int, int] | None:
  """Parse a BDAY vCard value into (month, day), or None if unparseable.

  Handles: YYYY-MM-DD, YYYYMMDD, --MM-DD, --MMDD.
  """
  s = bday_str.strip()
  try:
    if s.startswith('--'):
      digits = s[2:].replace('-', '')
      if len(digits) == 4:
        return int(digits[:2]), int(digits[2:])
    elif len(s) == 10 and s[4] == '-' and s[7] == '-':
      return int(s[5:7]), int(s[8:10])
    elif len(s) == 8 and s.isdigit():
      return int(s[4:6]), int(s[6:8])
  except ValueError:
    pass
  return None


def _get_addressbook_url(carddav_url: str, username: str, password: str) -> str:
  """Discover the CardDAV addressbook URL. Cached for process lifetime.

  Performs a three-step PROPFIND discovery: root → principal →
  addressbook-home → first addressbook collection.
  Raises IntegrationDataUnavailableError on failure.
  """
  global _carddav_addressbook_url, _carddav_home_url

  if _carddav_addressbook_url is not None:
    logger.debug('calendar: CardDAV addressbook cache hit')
    return _carddav_addressbook_url

  auth = (username, password)
  ns = {'d': 'DAV:', 'card': 'urn:ietf:params:xml:ns:carddav'}

  try:
    # Step 1: current-user-principal
    r = requests.request(
      'PROPFIND',
      carddav_url,
      headers={'Depth': '0', 'Content-Type': 'application/xml'},
      data=(
        '<?xml version="1.0" encoding="utf-8"?><propfind xmlns="DAV:"><prop><current-user-principal/></prop></propfind>'
      ),
      auth=auth,
      timeout=15,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)  # nosec B314 — XML from authenticated Apple API
    principal_href = root.findtext('.//d:current-user-principal/d:href', namespaces=ns)
    if not principal_href:
      raise IntegrationDataUnavailableError('calendar: CardDAV principal not found')
    principal_url = urljoin(carddav_url, principal_href)

    # Step 2: addressbook-home-set
    r = requests.request(
      'PROPFIND',
      principal_url,
      headers={'Depth': '0', 'Content-Type': 'application/xml'},
      data=(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<propfind xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
        '<prop><card:addressbook-home-set/></prop></propfind>'
      ),
      auth=auth,
      timeout=15,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)  # nosec B314 — XML from authenticated Apple API
    home_href = root.findtext('.//card:addressbook-home-set/d:href', namespaces=ns)
    if not home_href:
      raise IntegrationDataUnavailableError('calendar: CardDAV addressbook home not found')
    home_url = urljoin(carddav_url, home_href)

    # Step 3: first addressbook collection
    r = requests.request(
      'PROPFIND',
      home_url,
      headers={'Depth': '1', 'Content-Type': 'application/xml'},
      data=(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<propfind xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
        '<prop><resourcetype/></prop></propfind>'
      ),
      auth=auth,
      timeout=15,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)  # nosec B314 — XML from authenticated Apple API
    addressbook_url: str | None = None
    for resp in root.findall('d:response', ns):
      rtype = resp.find('.//d:resourcetype', ns)
      if rtype is not None and rtype.find('card:addressbook', ns) is not None:
        href = resp.findtext('d:href', namespaces=ns)
        if href:
          addressbook_url = urljoin(home_url, href)
          break
    if not addressbook_url:
      raise IntegrationDataUnavailableError('calendar: no CardDAV addressbook found')

  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'calendar: CardDAV discovery failed — {e}') from None

  logger.debug('calendar: CardDAV addressbook discovered: %r', addressbook_url)
  _carddav_home_url = home_url
  _carddav_addressbook_url = addressbook_url
  return addressbook_url


def _fetch_birthday_contacts(
  addressbook_url: str,
  username: str,
  password: str,
) -> list[tuple[str, int, int]]:
  """Fetch contacts with BDAY fields from a CardDAV addressbook.

  Returns a list of (first_name, month, day) tuples. Results are cached
  for _BIRTHDAY_CACHE_TTL seconds.
  """
  global _birthday_cache

  if _birthday_cache is not None:
    contacts, fetched_at = _birthday_cache
    age = time.monotonic() - fetched_at
    if age <= _BIRTHDAY_CACHE_TTL:
      logger.debug('calendar: birthday cache hit (%.0fs old)', age)
      return contacts

  try:
    r = requests.request(
      'REPORT',
      addressbook_url,
      headers={'Depth': '1', 'Content-Type': 'application/xml'},
      data=(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<card:addressbook-query'
        ' xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
        '<d:prop><card:address-data>'
        '<card:prop name="FN"/><card:prop name="BDAY"/>'
        '</card:address-data></d:prop>'
        '</card:addressbook-query>'
      ),
      auth=(username, password),
      timeout=15,
    )
    r.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'calendar: birthday contacts fetch failed — {e}') from None

  ns = {'d': 'DAV:', 'card': 'urn:ietf:params:xml:ns:carddav'}
  root = ET.fromstring(r.content)  # nosec B314 — XML from authenticated Apple API
  contacts: list[tuple[str, int, int]] = []

  for resp in root.findall('d:response', ns):
    data = resp.findtext('.//card:address-data', namespaces=ns)
    if not data:
      continue
    fn: str | None = None
    nickname: str | None = None
    bday_raw: str | None = None
    for line in data.splitlines():
      upper = line.upper()
      if upper.startswith('FN:'):
        fn = line[3:].strip()
      elif upper.startswith('NICKNAME:'):
        nickname = line[9:].strip()
      elif upper.startswith('BDAY') and ':' in line:
        bday_raw = line.split(':', 1)[1].strip()
    if not fn or not bday_raw:
      continue
    parsed = _parse_bday(bday_raw)
    if parsed is None:
      continue
    parts = fn.split()
    if not parts:
      continue
    display_name = _resolve_display_name(nickname, parts)
    contacts.append((display_name, parsed[0], parsed[1]))

  logger.debug('calendar: fetched %d birthday contact(s)', len(contacts))
  _birthday_cache = (contacts, time.monotonic())
  return contacts


def _resolve_self_contact(
  carddav_url: str,
  username: str,
  password: str,
) -> tuple[str, int, int] | None:
  """Resolve the owner's contact as (first_name, birth_month, birth_day).

  Uses the CalendarServer me-card extension: PROPFIND on the CardDAV home
  with {http://calendarserver.org/ns/}me-card to get the self vCard href,
  then GET the vCard and parse FN and BDAY.

  Returns None on any failure (property absent, server doesn't support the
  extension, network error, no BDAY set). Caches for process lifetime.
  Only supported for iCloud (calendarserver.org extension required).
  """
  global _self_contact_cache

  if _self_contact_cache is not None:
    return _self_contact_cache

  try:
    # Ensure home URL is populated (triggers discovery if not yet done).
    _get_addressbook_url(carddav_url, username, password)
    if not _carddav_home_url:
      return None

    auth = (username, password)
    ns = {
      'd': 'DAV:',
      'cs': 'http://calendarserver.org/ns/',
    }

    # Step 1: PROPFIND home for me-card href.
    r = requests.request(
      'PROPFIND',
      _carddav_home_url,
      headers={'Depth': '0', 'Content-Type': 'application/xml'},
      data=(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<propfind xmlns="DAV:" xmlns:cs="http://calendarserver.org/ns/">'
        '<prop><cs:me-card/></prop></propfind>'
      ),
      auth=auth,
      timeout=15,
    )
    r.raise_for_status()
    root = ET.fromstring(r.content)  # nosec B314 — XML from authenticated Apple API
    me_card_href = root.findtext('.//cs:me-card/d:href', namespaces=ns)
    if not me_card_href:
      logger.debug('calendar: me-card property absent — self-birthday unavailable')
      return None

    vcard_url = urljoin(_carddav_home_url, me_card_href)

    # Step 2: GET the self vCard.
    r = requests.get(vcard_url, auth=auth, timeout=15)
    r.raise_for_status()

    fn: str | None = None
    nickname: str | None = None
    bday_raw: str | None = None
    for line in r.text.splitlines():
      upper = line.upper()
      if upper.startswith('FN:'):
        fn = line[3:].strip()
      elif upper.startswith('NICKNAME:'):
        nickname = line[9:].strip()
      elif upper.startswith('BDAY') and ':' in line:
        bday_raw = line.split(':', 1)[1].strip()

    if not fn or not bday_raw:
      logger.debug('calendar: me-card missing FN or BDAY — self-birthday unavailable')
      return None

    parsed = _parse_bday(bday_raw)
    if parsed is None:
      logger.debug('calendar: me-card BDAY unparseable — self-birthday unavailable')
      return None

    display_name = _resolve_display_name(nickname, fn.split())
    _self_contact_cache = (display_name, parsed[0], parsed[1])
    logger.debug('calendar: self contact resolved')
    return _self_contact_cache

  except Exception:  # noqa: BLE001  # nosec B112 — broad catch; me-card is best-effort
    logger.debug('calendar: me-card discovery failed — self-birthday unavailable')
    return None


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
  import config as _config_mod

  cal_cfg: dict[str, Any] = _config_mod._config.get('calendar', {})
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


def get_variables_birthdays() -> dict[str, list[list[str]]]:
  """Return upcoming birthdays as a variables dict for template rendering.

  Returns key 'birthdays' as a single option containing one line per birthday,
  formatted as 'FIRSTNAME TODAY' or 'FIRSTNAME MON'.
  Requires carddav_url in [calendar] config — omit it to disable birthdays.
  Raises IntegrationDataUnavailableError if not configured or no birthdays
  fall within the lookahead window.
  """
  import config as _config_mod

  cal_cfg: dict[str, Any] = _config_mod._config.get('calendar', {})
  carddav_url = cal_cfg.get('carddav_url', '')
  username = cal_cfg.get('username', '')
  password = cal_cfg.get('password', '')

  if not carddav_url:
    raise IntegrationDataUnavailableError('calendar: birthdays require carddav_url in [calendar] config')
  if not username or not password:
    raise IntegrationDataUnavailableError('calendar: birthdays require username and password in [calendar] config')

  lookahead = int(cal_cfg.get('birthdays_lookahead_days', 7))

  tz = _display_tz()
  now = _get_now(tz)
  today = now.date()

  addressbook_url = _get_addressbook_url(carddav_url, username, password)
  contacts = _fetch_birthday_contacts(addressbook_url, username, password)

  self_contact = _resolve_self_contact(carddav_url, username, password)

  entries: list[tuple[int, str, str]] = []  # (days_ahead, display_name, line)
  for display_name, month, day in contacts:
    if self_contact and (display_name, month, day) == self_contact:
      continue  # shown exclusively via birthday_self.json
    try:
      candidate = today.replace(month=month, day=day)
    except ValueError:
      continue  # e.g. Feb 29 on a non-leap year — skip
    if candidate < today:
      try:
        candidate = candidate.replace(year=today.year + 1)
      except ValueError:
        continue
    days_ahead = (candidate - today).days
    if days_ahead > lookahead:
      continue
    day_label = 'TODAY' if days_ahead == 0 else candidate.strftime('%a').upper()
    entries.append((days_ahead, display_name, f'{day_label} {display_name}'))

  if not entries:
    raise IntegrationDataUnavailableError(f'calendar: no birthdays in the next {lookahead} days')

  entries.sort(key=lambda x: (x[0], x[1]))
  return {'birthdays': [[line for _, _, line in entries]]}


def get_variables_self_birthday() -> dict[str, list[list[str]]]:
  """Return the owner's first name when today is their birthday.

  Returns key 'name' for use in birthday_self.json format strings.
  Requires carddav_url in [calendar] config and iCloud CardDAV (me-card
  CalendarServer extension). Raises IntegrationDataUnavailableError when
  not configured, me-card unavailable, or today is not the owner's birthday.
  """
  import config as _config_mod

  cal_cfg: dict[str, Any] = _config_mod._config.get('calendar', {})
  carddav_url = cal_cfg.get('carddav_url', '')
  username = cal_cfg.get('username', '')
  password = cal_cfg.get('password', '')

  if not carddav_url:
    raise IntegrationDataUnavailableError('calendar: self-birthday requires carddav_url in [calendar] config')
  if not username or not password:
    raise IntegrationDataUnavailableError('calendar: self-birthday requires username and password in [calendar] config')

  self_contact = _resolve_self_contact(carddav_url, username, password)
  if self_contact is None:
    raise IntegrationDataUnavailableError('calendar: self contact could not be resolved (iCloud me-card required)')

  display_name, month, day = self_contact

  tz = _display_tz()
  now = _get_now(tz)
  today = now.date()

  try:
    birthday_this_year = today.replace(month=month, day=day)
  except ValueError:
    raise IntegrationDataUnavailableError('calendar: self birthday (Feb 29) skipped on non-leap year') from None

  if today != birthday_this_year:
    raise IntegrationDataUnavailableError("calendar: today is not the owner's birthday")

  return {'name': [[display_name]]}
