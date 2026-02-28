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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {'calendar': {'urls': ['https://example.com/cal.ics']}, 'scheduler': {}},
  )


@pytest.fixture()
def ical_config_ics_two_urls(monkeypatch: pytest.MonkeyPatch) -> None:
  """Patch config with two ICS URLs, each with a color."""
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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


def test_nearest_color_tag_red() -> None:
  # Apple red #FF2D30FF → nearest is R
  assert calendar._nearest_color_tag('#FF2D30FF') == '[R]'


def test_nearest_color_tag_blue() -> None:
  assert calendar._nearest_color_tag('#007AFF') == '[B]'


def test_nearest_color_tag_strips_alpha() -> None:
  # With and without alpha should resolve to the same color
  assert calendar._nearest_color_tag('#52C755FF') == calendar._nearest_color_tag('#52C755')


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
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'calendar': {'urls': ['https://example.com/cal.ics']}, 'scheduler': {}})
  ics = _make_ics_with_cal_color([_future_event('WORK MEETING')], '#007AFFFF')  # blue
  with patch('integrations.calendar._fetch_ics_bytes', return_value=ics):
    with patch('integrations.calendar._display_tz', return_value=_UTC):
      with patch('integrations.calendar._get_now', return_value=_FIXED_NOW):
        result = calendar.get_variables()
  lines = result['events'][0]
  assert any(line.startswith('[B]') for line in lines)


def test_get_variables_configured_color_used(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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


# ── CalDAV color ───────────────────────────────────────────────────────────────


def test_nearest_color_tag_apple_red() -> None:
  """Apple's default red #FF2D55FF maps to [R]."""
  assert calendar._nearest_color_tag('#FF2D55FF') == '[R]'


def test_nearest_color_tag_apple_green() -> None:
  """Apple's default green #34C759FF maps to [G]."""
  assert calendar._nearest_color_tag('#34C759FF') == '[G]'


def test_nearest_color_tag_white() -> None:
  assert calendar._nearest_color_tag('#FFFFFFFF') == '[W]'


def test_nearest_color_tag_black() -> None:
  assert calendar._nearest_color_tag('#000000FF') == '[K]'


# ── Both modes simultaneously ──────────────────────────────────────────────────


def test_get_variables_both_modes_merged(monkeypatch: pytest.MonkeyPatch) -> None:
  """Events from ICS and CalDAV are merged into a single sorted list."""
  from unittest.mock import MagicMock

  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
  fake_cal_obj.icalendar_object = Calendar.from_ical(caldav_event_ics)
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
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
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
