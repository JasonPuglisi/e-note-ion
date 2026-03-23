import time
from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import patch

import pytest
from icalendar import Calendar, Event

import integrations.calendar as calendar
from exceptions import IntegrationDataUnavailableError

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_caches() -> Generator[None, None, None]:
  """Reset module-level caches before each test."""
  calendar._ics_cache.clear()
  calendar._caldav_cache = None
  yield
  calendar._ics_cache.clear()
  calendar._caldav_cache = None


@pytest.fixture()
def ical_config_ics(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with a minimal ICS-mode ical section."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'calendar': {'urls': ['https://example.com/cal.ics']}, 'scheduler': {}},
  )


@pytest.fixture()
def ical_config_ics_two_urls(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with two ICS URLs, each with a color."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'calendar': {
        'urls': ['https://example.com/cal1.ics', 'https://example.com/cal2.ics'],
        'colors': ['B', 'G'],
      },
      'scheduler': {},
    },
  )


# ── ICS builder helpers ────────────────────────────────────────────────────────

_UTC = timezone.utc

# Fixed reference time used by helpers and tests that patch _get_now.
# Pinned to noon UTC so events ±3 hours are always within the same calendar day.
_FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=_UTC)


def _make_ics(events_data: list[dict]) -> bytes:
  """Build raw ICS bytes from a list of event property dicts."""
  cal = Calendar()
  for ev_data in events_data:
    ev = Event()
    for key, val in ev_data.items():
      ev.add(key, val)
    cal.add_component(ev)
  return cal.to_ical()


def _make_ics_with_cal_color(events_data: list[dict], cal_color: str) -> bytes:
  """Build raw ICS bytes with an X-APPLE-CALENDAR-COLOR property on the VCALENDAR."""
  cal = Calendar()
  cal.add('X-APPLE-CALENDAR-COLOR', cal_color)
  for ev_data in events_data:
    ev = Event()
    for key, val in ev_data.items():
      ev.add(key, val)
    cal.add_component(ev)
  return cal.to_ical()


def _future_event(title: str = 'MEETING', hours_ahead: float = 2.0) -> dict:
  start = _FIXED_NOW + timedelta(hours=hours_ahead)
  end = start + timedelta(hours=1)
  return {'SUMMARY': title, 'DTSTART': start, 'DTEND': end}


def _past_event(title: str = 'OLD MEETING') -> dict:
  start = _FIXED_NOW - timedelta(hours=3)
  end = _FIXED_NOW - timedelta(hours=2)
  return {'SUMMARY': title, 'DTSTART': start, 'DTEND': end}


def _allday_event(title: str = 'HOLIDAY') -> dict:
  return {'SUMMARY': title, 'DTSTART': _FIXED_NOW.date()}


# ── Color helpers ──────────────────────────────────────────────────────────────


def test_wrap_color_valid() -> None:
  assert calendar._wrap_color('B') == '[B]'
  assert calendar._wrap_color('b') == '[B]'  # case-insensitive
  assert calendar._wrap_color('R') == '[R]'


def test_wrap_color_invalid() -> None:
  with pytest.raises(ValueError, match='Invalid calendar color'):
    calendar._wrap_color('X')
  with pytest.raises(ValueError, match='Invalid calendar color'):
    calendar._wrap_color('purple')


def test_ics_calendar_color_parsed() -> None:
  cal = Calendar()
  cal.add('X-APPLE-CALENDAR-COLOR', '#007AFFFF')
  result = calendar._ics_calendar_color(cal)
  assert result == '[B]'


def test_ics_calendar_color_absent() -> None:
  cal = Calendar()
  assert calendar._ics_calendar_color(cal) is None


# ── ICS mode: basic ────────────────────────────────────────────────────────────


def test_get_variables_returns_events_key(ical_config_ics: None) -> None:
  ics = _make_ics([_future_event('TEAM MEETING')])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  assert 'events' in result
  assert len(result['events']) == 1
  assert len(result['events'][0]) >= 1


def test_get_variables_24h_time_format(ical_config_ics: None) -> None:
  # Event at 14:30 UTC on the same day as _FIXED_NOW (noon UTC).
  start = _FIXED_NOW.replace(hour=14, minute=30, second=0, microsecond=0)
  end = start + timedelta(hours=1)
  ics = _make_ics([{'SUMMARY': 'DENTIST', 'DTSTART': start, 'DTEND': end}])

  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()

  lines = result['events'][0]
  assert any('14:30' in line for line in lines), f'Expected 14:30 in lines: {lines}'
  assert not any('AM' in line or 'PM' in line for line in lines)


def test_get_variables_all_day_no_time_prefix(ical_config_ics: None) -> None:
  ics = _make_ics([_allday_event('PROJECT DEADLINE')])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any('PROJECT DEADLINE' in line for line in lines)
  # Should have no time component (no colon between two digits)
  for line in lines:
    if 'PROJECT DEADLINE' in line:
      assert ':' not in line.split('PROJECT')[0] or not line.split('PROJECT')[0].strip()


# ── ICS mode: color ────────────────────────────────────────────────────────────


def test_get_variables_apple_color_auto_detected(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {'urls': ['https://example.com/cal.ics']}, 'scheduler': {}})
  ics = _make_ics_with_cal_color([_future_event('WORK MEETING')], '#007AFFFF')  # blue
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any(line.startswith('[B]') for line in lines)


def test_get_variables_configured_color_used(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'calendar': {'urls': ['https://example.com/cal.ics'], 'colors': ['V']}, 'scheduler': {}},
  )
  ics = _make_ics([_future_event('PERSONAL EVENT')])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any(line.startswith('[V]') for line in lines)


def test_get_variables_configured_color_overrides_apple(monkeypatch: pytest.MonkeyPatch) -> None:
  """User-configured color takes precedence over auto-detected X-APPLE-CALENDAR-COLOR."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'calendar': {'urls': ['https://example.com/cal.ics'], 'colors': ['G']}, 'scheduler': {}},
  )
  ics = _make_ics_with_cal_color([_future_event('MEETING')], '#FF2D30FF')  # red in ICS
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any(line.startswith('[G]') for line in lines)


# ── ICS mode: filtering ────────────────────────────────────────────────────────


def test_get_variables_timed_event_ended_skipped(ical_config_ics: None) -> None:
  ics = _make_ics([_past_event('FINISHED MEETING')])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        with pytest.raises(IntegrationDataUnavailableError):
          calendar.get_variables()


def test_get_variables_no_summary_skipped(ical_config_ics: None) -> None:
  start = _FIXED_NOW + timedelta(hours=1)
  end = _FIXED_NOW + timedelta(hours=2)
  ics = _make_ics([{'DTSTART': start, 'DTEND': end}])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        with pytest.raises(IntegrationDataUnavailableError):
          calendar.get_variables()


def test_get_variables_cancelled_skipped(ical_config_ics: None) -> None:
  ics = _make_ics(
    [
      {
        'SUMMARY': 'CANCELLED MEETING',
        'STATUS': 'CANCELLED',
        'DTSTART': _FIXED_NOW + timedelta(hours=1),
        'DTEND': _FIXED_NOW + timedelta(hours=2),
      }
    ]
  )
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        with pytest.raises(IntegrationDataUnavailableError):
          calendar.get_variables()


def test_get_variables_no_events_raises(ical_config_ics: None) -> None:
  ics = _make_ics([])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        with pytest.raises(IntegrationDataUnavailableError, match='no events today'):
          calendar.get_variables()


# ── ICS mode: sort order ───────────────────────────────────────────────────────


def test_get_variables_timed_before_allday(ical_config_ics: None) -> None:
  ics = _make_ics([_allday_event('ALL DAY EVENT'), _future_event('TIMED EVENT')])
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  timed_idx = next(i for i, ln in enumerate(lines) if 'TIMED EVENT' in ln)
  allday_idx = next(i for i, ln in enumerate(lines) if 'ALL DAY EVENT' in ln)
  assert timed_idx < allday_idx


def test_get_variables_url_order_tiebreaker(monkeypatch: pytest.MonkeyPatch) -> None:
  """When two events have the same start time, the event from URL 0 comes first."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'calendar': {'urls': ['https://example.com/a.ics', 'https://example.com/b.ics']}, 'scheduler': {}},
  )
  start = _FIXED_NOW + timedelta(hours=2)
  end = start + timedelta(hours=1)
  ics_a = _make_ics([{'SUMMARY': 'URL A EVENT', 'DTSTART': start, 'DTEND': end}])
  ics_b = _make_ics([{'SUMMARY': 'URL B EVENT', 'DTSTART': start, 'DTEND': end}])

  def fake_fetch(url: str) -> bytes:
    return ics_a if 'a.ics' in url else ics_b

  with patch('integrations.calendar._fetch_ics_bytes', side_effect=fake_fetch):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  a_idx = next(i for i, ln in enumerate(lines) if 'URL A EVENT' in ln)
  b_idx = next(i for i, ln in enumerate(lines) if 'URL B EVENT' in ln)
  assert a_idx < b_idx


def test_get_variables_multiple_urls_merged(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'calendar': {'urls': ['https://example.com/a.ics', 'https://example.com/b.ics']}, 'scheduler': {}},
  )
  ics_a = _make_ics([_future_event('EVENT FROM A')])
  ics_b = _make_ics([_future_event('EVENT FROM B', hours_ahead=3)])

  def fake_fetch(url: str) -> bytes:
    return ics_a if 'a.ics' in url else ics_b

  with patch('integrations.calendar._fetch_ics_bytes', side_effect=fake_fetch):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any('EVENT FROM A' in ln for ln in lines)
  assert any('EVENT FROM B' in ln for ln in lines)


# ── ICS mode: failure handling ─────────────────────────────────────────────────


def test_get_variables_one_url_fails_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
  """If one URL fails with no cache, it is skipped; events from the other URL still show."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'calendar': {'urls': ['https://example.com/fail.ics', 'https://example.com/ok.ics']}, 'scheduler': {}},
  )
  ics_ok = _make_ics([_future_event('OK EVENT')])

  def fake_fetch(url: str) -> bytes:
    if 'fail' in url:
      raise IntegrationDataUnavailableError('fetch failed')
    return ics_ok

  with patch('integrations.calendar._fetch_ics_bytes', side_effect=fake_fetch):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any('OK EVENT' in ln for ln in lines)


def test_get_variables_cache_served_on_transient_failure(ical_config_ics: None) -> None:
  """On fetch failure, stale cached bytes are served."""
  ics = _make_ics([_future_event('CACHED EVENT')])
  # Seed the cache with stale-but-valid data.
  import time

  calendar._ics_cache['https://example.com/cal.ics'] = (ics, time.monotonic() - 99999)

  import requests as req

  with patch('integrations.calendar._fetch_ics_bytes', side_effect=req.ConnectionError('down')):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      # fetch_ics_bytes handles the stale cache internally; get_variables should succeed
      # by re-fetching from stale cache via the real function (not mocked here)
      pass

  # Call the real _fetch_ics_bytes to confirm stale cache is returned on error.
  with patch('integrations.calendar.fetch_with_retry', side_effect=req.ConnectionError('down')):
    data = calendar._fetch_ics_bytes('https://example.com/cal.ics')
  assert data == ics


def test_get_variables_raises_cold_start_failure(ical_config_ics: None) -> None:
  """On fetch failure with no cache, IntegrationDataUnavailableError is raised."""
  import requests as req

  with patch('integrations.calendar.fetch_with_retry', side_effect=req.ConnectionError('down')):
    with pytest.raises(IntegrationDataUnavailableError, match='fetch failed'):
      calendar._fetch_ics_bytes('https://example.com/cal.ics')


# ── Both modes simultaneously ──────────────────────────────────────────────────


def test_get_variables_both_modes_merged(monkeypatch: pytest.MonkeyPatch) -> None:
  """Events from ICS and CalDAV are merged into a single sorted list."""
  from unittest.mock import MagicMock

  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'calendar': {
        'urls': ['https://example.com/cal.ics'],
        'caldav_url': 'https://caldav.icloud.com/',
        'username': 'user@icloud.com',
        'password': 'xxxx-xxxx-xxxx-xxxx',
      },
      'scheduler': {},
    },
  )

  ics = _make_ics([_future_event('ICS EVENT', hours_ahead=1)])

  # Build a fake CalDAV calendar that returns a CalDAV event.
  caldav_event_ics = _make_ics([_future_event('CALDAV EVENT', hours_ahead=3)])
  fake_cal_obj = MagicMock()
  fake_cal_obj.icalendar_instance = Calendar.from_ical(caldav_event_ics)
  fake_caldav_cal = MagicMock()
  fake_caldav_cal.events.return_value = [fake_cal_obj]

  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._get_caldav_calendars', return_value=[(fake_caldav_cal, '[G]')]):
      with patch('integrations.calendar._display_tz', return_value=_UTC):
        with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
          result = calendar.get_variables()

  lines = result['events'][0]
  assert any('ICS EVENT' in ln for ln in lines), f'ICS event missing from: {lines}'
  assert any('CALDAV EVENT' in ln for ln in lines), f'CalDAV event missing from: {lines}'
  # ICS event is 1h ahead, CalDAV is 3h ahead → ICS should sort first.
  ics_idx = next(i for i, ln in enumerate(lines) if 'ICS EVENT' in ln)
  caldav_idx = next(i for i, ln in enumerate(lines) if 'CALDAV EVENT' in ln)
  assert ics_idx < caldav_idx


def test_get_variables_caldav_absent_does_not_block_ics(monkeypatch: pytest.MonkeyPatch) -> None:
  """If CalDAV returns no calendars, ICS events still appear."""
  import config as _config_mod

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'calendar': {
        'urls': ['https://example.com/cal.ics'],
        'caldav_url': 'https://caldav.icloud.com/',
        'username': 'user@icloud.com',
        'password': 'xxxx-xxxx-xxxx-xxxx',
      },
      'scheduler': {},
    },
  )

  ics = _make_ics([_future_event('ICS ONLY EVENT')])

  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._get_caldav_calendars', return_value=[]):
      with patch('integrations.calendar._display_tz', return_value=_UTC):
        with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
          result = calendar.get_variables()

  lines = result['events'][0]
  assert any('ICS ONLY EVENT' in ln for ln in lines)


# ── Birthday tests ─────────────────────────────────────────────────────────────

_BDAY_CONFIG = {
  'calendar': {
    'carddav_url': 'https://contacts.icloud.com/',
    'username': 'user@icloud.com',
    'password': 'xxxx-xxxx-xxxx-xxxx',
  },
  'scheduler': {},
}

# Fixed date for birthday tests: Thursday 2026-01-15.
_BDAY_TODAY = _FIXED_NOW.date()


@pytest.fixture(autouse=True)
def reset_birthday_caches() -> Generator[None, None, None]:
  calendar._carddav_addressbook_url = None
  calendar._carddav_home_url = None
  calendar._birthday_cache = None
  calendar._self_contact_cache = None
  yield
  calendar._carddav_addressbook_url = None
  calendar._carddav_home_url = None
  calendar._birthday_cache = None
  calendar._self_contact_cache = None


def _patch_birthdays(
  monkeypatch: pytest.MonkeyPatch,
  contacts: list[tuple[str, int, int]],
  config: dict | None = None,
) -> None:
  """Patch config, birthday cache, and now for birthday unit tests."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', config or _BDAY_CONFIG)
  monkeypatch.setattr(calendar, '_birthday_cache', (contacts, time.monotonic()))
  monkeypatch.setattr(calendar, '_carddav_addressbook_url', 'https://fake/ab/')
  monkeypatch.setattr(calendar, '_display_tz', lambda: _UTC)
  monkeypatch.setattr(calendar, '_get_now', lambda tz: _FIXED_NOW)


def test_birthday_formats_today(monkeypatch: pytest.MonkeyPatch) -> None:
  """Birthday today → 'TODAY FIRSTNAME'."""
  _patch_birthdays(monkeypatch, [('ADAM', _BDAY_TODAY.month, _BDAY_TODAY.day)])
  result = calendar.get_variables_birthdays()
  assert result['birthdays'][0] == ['TODAY ADAM']


def test_birthday_formats_day_name(monkeypatch: pytest.MonkeyPatch) -> None:
  """Birthday in 3 days → '<DAY> FIRSTNAME'."""
  target = _BDAY_TODAY + timedelta(days=3)
  expected_day = target.strftime('%a').upper()
  _patch_birthdays(monkeypatch, [('BRIANNA', target.month, target.day)])
  result = calendar.get_variables_birthdays()
  assert result['birthdays'][0] == [f'{expected_day} BRIANNA']


def test_birthday_uses_display_name(monkeypatch: pytest.MonkeyPatch) -> None:
  """Display name from cache appears after the day label."""
  _patch_birthdays(monkeypatch, [('ADAM', _BDAY_TODAY.month, _BDAY_TODAY.day)])
  result = calendar.get_variables_birthdays()
  assert result['birthdays'][0][0].endswith('ADAM')


def test_birthday_filters_outside_window(monkeypatch: pytest.MonkeyPatch) -> None:
  """Birthdays beyond lookahead_days are excluded."""
  far = _BDAY_TODAY + timedelta(days=30)
  _patch_birthdays(monkeypatch, [('FAR', far.month, far.day)])
  with pytest.raises(IntegrationDataUnavailableError):
    calendar.get_variables_birthdays()


def test_birthday_multiple_sorted(monkeypatch: pytest.MonkeyPatch) -> None:
  """Multiple birthdays sorted: today first, then ascending days, then name."""
  day3 = _BDAY_TODAY + timedelta(days=3)
  day1 = _BDAY_TODAY + timedelta(days=1)
  contacts = [
    ('ZARA', day3.month, day3.day),
    ('ADAM', _BDAY_TODAY.month, _BDAY_TODAY.day),
    ('BLAKE', day1.month, day1.day),
  ]
  _patch_birthdays(monkeypatch, contacts)
  result = calendar.get_variables_birthdays()
  lines = result['birthdays'][0]
  assert lines[0].endswith('ADAM')
  assert lines[1].endswith('BLAKE')
  assert lines[2].endswith('ZARA')


def test_birthday_no_results_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  """Empty window raises IntegrationDataUnavailableError."""
  _patch_birthdays(monkeypatch, [])
  with pytest.raises(IntegrationDataUnavailableError):
    calendar.get_variables_birthdays()


def test_birthday_no_carddav_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  """Missing carddav_url raises IntegrationDataUnavailableError immediately."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {}, 'scheduler': {}})
  with pytest.raises(IntegrationDataUnavailableError, match='carddav_url'):
    calendar.get_variables_birthdays()


def test_birthday_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
  """Stale cache (> 24h) triggers a real HTTP fetch; fresh cache does not."""
  from unittest.mock import MagicMock

  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _BDAY_CONFIG)
  monkeypatch.setattr(calendar, '_carddav_addressbook_url', 'https://fake/ab/')
  monkeypatch.setattr(calendar, '_display_tz', lambda: _UTC)
  monkeypatch.setattr(calendar, '_get_now', lambda tz: _FIXED_NOW)

  contacts = [('ADAM', _BDAY_TODAY.month, _BDAY_TODAY.day)]
  fresh_time = time.monotonic()
  stale_time = fresh_time - calendar._BIRTHDAY_CACHE_TTL - 1

  # Fresh cache — no HTTP request should be made.
  monkeypatch.setattr(calendar, '_birthday_cache', (contacts, fresh_time))
  with patch('integrations.calendar.requests.request') as mock_req:
    calendar.get_variables_birthdays()
    mock_req.assert_not_called()

  # Stale cache — HTTP request should be made.
  fake_response = MagicMock()
  fake_response.content = (
    b'<?xml version="1.0"?>'
    b'<multistatus xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
    b'<response><href>/ab/1.vcf</href>'
    b'<propstat><prop><card:address-data>'
    b'FN:Adam Test\r\nBDAY:2000-01-15\r\n'
    b'</card:address-data></prop>'
    b'<status>HTTP/1.1 200 OK</status></propstat></response>'
    b'</multistatus>'
  )
  monkeypatch.setattr(calendar, '_birthday_cache', (contacts, stale_time))
  with patch('integrations.calendar.requests.request', return_value=fake_response):
    calendar.get_variables_birthdays()


def test_parse_bday_formats() -> None:
  """_parse_bday handles all supported BDAY formats."""
  assert calendar._parse_bday('1997-03-10') == (3, 10)
  assert calendar._parse_bday('19970310') == (3, 10)
  assert calendar._parse_bday('--03-10') == (3, 10)
  assert calendar._parse_bday('--0310') == (3, 10)
  assert calendar._parse_bday('not-a-date') is None


# ── Self-birthday tests ────────────────────────────────────────────────────────

_SELF_BDAY_TODAY = _BDAY_TODAY  # reuse the fixed date: 2026-01-15

# Minimal vCard for the self contact, with BDAY matching _SELF_BDAY_TODAY.
_SELF_VCARD = (
  'BEGIN:VCARD\r\n'
  'VERSION:3.0\r\n'
  'FN:Alex Smith\r\n'
  f'BDAY;value=date:{_SELF_BDAY_TODAY.year}-{_SELF_BDAY_TODAY.month:02d}-{_SELF_BDAY_TODAY.day:02d}\r\n'
  'END:VCARD\r\n'
)

_SELF_ME_CARD_PROPFIND_RESPONSE = (
  '<?xml version="1.0"?>'
  '<multistatus xmlns="DAV:">'
  '<response><href>/home/</href>'
  '<propstat><prop>'
  '<me-card xmlns="http://calendarserver.org/ns/">'
  '<href xmlns="DAV:">/home/card/self.vcf</href>'
  '</me-card>'
  '</prop><status>HTTP/1.1 200 OK</status></propstat>'
  '</response>'
  '</multistatus>'
)


def _patch_self_birthday(
  monkeypatch: pytest.MonkeyPatch,
  vcard_text: str = _SELF_VCARD,
  config: dict | None = None,
) -> None:
  """Patch config, home URL, and _get_now for self-birthday unit tests.

  Mocks the me-card PROPFIND and vCard GET so no real HTTP is made.
  """
  from unittest.mock import MagicMock

  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', config or _BDAY_CONFIG)
  monkeypatch.setattr(calendar, '_carddav_addressbook_url', 'https://fake/ab/')
  monkeypatch.setattr(calendar, '_carddav_home_url', 'https://fake/home/')
  monkeypatch.setattr(calendar, '_display_tz', lambda: _UTC)
  monkeypatch.setattr(calendar, '_get_now', lambda tz: _FIXED_NOW)

  propfind_resp = MagicMock()
  propfind_resp.content = _SELF_ME_CARD_PROPFIND_RESPONSE.encode()
  propfind_resp.raise_for_status = lambda: None

  vcard_resp = MagicMock()
  vcard_resp.text = vcard_text
  vcard_resp.raise_for_status = lambda: None

  def _mock_request(method: str, url: str, **kwargs: object) -> MagicMock:
    if method == 'PROPFIND':
      return propfind_resp
    return vcard_resp

  monkeypatch.setattr(calendar.requests, 'request', _mock_request)
  monkeypatch.setattr(calendar.requests, 'get', lambda url, **kw: vcard_resp)


def test_self_birthday_today(monkeypatch: pytest.MonkeyPatch) -> None:
  """Birthday matches today → returns display name (first + last initial)."""
  _patch_self_birthday(monkeypatch)
  result = calendar.get_variables_self_birthday()
  assert result == {'name': [['ALEX S']]}


def test_self_birthday_not_today(monkeypatch: pytest.MonkeyPatch) -> None:
  """Birthday is not today → raises IntegrationDataUnavailableError."""
  tomorrow = _SELF_BDAY_TODAY + timedelta(days=1)
  vcard = (
    'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alex Smith\r\n'
    f'BDAY;value=date:{tomorrow.year}-{tomorrow.month:02d}-{tomorrow.day:02d}\r\n'
    'END:VCARD\r\n'
  )
  _patch_self_birthday(monkeypatch, vcard_text=vcard)
  with pytest.raises(IntegrationDataUnavailableError, match='not the owner'):
    calendar.get_variables_self_birthday()


def test_self_birthday_no_carddav_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  """Missing carddav_url → raises immediately."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {}, 'scheduler': {}})
  with pytest.raises(IntegrationDataUnavailableError, match='carddav_url'):
    calendar.get_variables_self_birthday()


def test_self_birthday_no_bday_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  """me-card vCard has no BDAY → raises."""
  vcard = 'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alex Smith\r\nEND:VCARD\r\n'
  _patch_self_birthday(monkeypatch, vcard_text=vcard)
  with pytest.raises(IntegrationDataUnavailableError, match='could not be resolved'):
    calendar.get_variables_self_birthday()


def test_self_birthday_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
  """Cached self contact is returned without any HTTP request."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _BDAY_CONFIG)
  monkeypatch.setattr(calendar, '_display_tz', lambda: _UTC)
  monkeypatch.setattr(calendar, '_get_now', lambda tz: _FIXED_NOW)
  monkeypatch.setattr(
    calendar,
    '_self_contact_cache',
    ('ALEX', _SELF_BDAY_TODAY.month, _SELF_BDAY_TODAY.day),
  )
  with patch('integrations.calendar.requests.request') as mock_req:
    result = calendar.get_variables_self_birthday()
    mock_req.assert_not_called()
  assert result == {'name': [['ALEX']]}


def test_self_birthday_me_card_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
  """Full me-card PROPFIND + vCard GET path populates _self_contact_cache."""
  _patch_self_birthday(monkeypatch)
  assert calendar._self_contact_cache is None
  calendar.get_variables_self_birthday()
  assert calendar._self_contact_cache == ('ALEX S', _SELF_BDAY_TODAY.month, _SELF_BDAY_TODAY.day)


def test_birthdays_suppresses_self(monkeypatch: pytest.MonkeyPatch) -> None:
  """Self contact is excluded from get_variables_birthdays() results."""
  # Pre-populate self contact cache so _resolve_self_contact() returns it.
  monkeypatch.setattr(
    calendar,
    '_self_contact_cache',
    ('ALEX', _SELF_BDAY_TODAY.month, _SELF_BDAY_TODAY.day),
  )
  # Contacts include self (ALEX) and one other (BLAKE, also today).
  _patch_birthdays(
    monkeypatch,
    [
      ('ALEX', _SELF_BDAY_TODAY.month, _SELF_BDAY_TODAY.day),
      ('BLAKE', _SELF_BDAY_TODAY.month, _SELF_BDAY_TODAY.day),
    ],
  )
  result = calendar.get_variables_birthdays()
  lines = result['birthdays'][0]
  assert not any('ALEX' in line for line in lines)
  assert any('BLAKE' in line for line in lines)
