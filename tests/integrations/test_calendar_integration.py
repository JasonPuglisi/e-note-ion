"""Integration tests for integrations/calendar.py — call real calendar feeds.

Run with: uv run pytest -m integration

Required env vars (at least one mode must be configured):
  CALENDAR_URL             — public/secret-address .ics URL (ICS mode)
  CALENDAR_CALDAV_URL      — CalDAV server URL (CalDAV mode)
  CALENDAR_USERNAME        — CalDAV/CardDAV username / Apple ID
  CALENDAR_PASSWORD        — CalDAV/CardDAV app-specific password
  CALENDAR_CARDDAV_URL     — CardDAV server URL (birthdays mode)
"""

import os
from typing import Generator

import pytest

import config as _cfg
import integrations.calendar as calendar
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def reset_caches() -> Generator[None, None, None]:
  calendar._ics_cache.clear()
  calendar._caldav_cache = None
  calendar._carddav_addressbook_url = None
  calendar._birthday_cache = None
  yield
  calendar._ics_cache.clear()
  calendar._caldav_cache = None
  calendar._carddav_addressbook_url = None
  calendar._birthday_cache = None


@pytest.mark.integration
@pytest.mark.require_env('CALENDAR_URL')
def test_ics_mode_real_feed(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid events or raises cleanly from a real .ics feed."""
  monkeypatch.setattr(_cfg, '_config', {'calendar': {'urls': [os.environ['CALENDAR_URL']]}, 'scheduler': {}})

  try:
    result = calendar.get_variables()
  except IntegrationDataUnavailableError:
    pytest.skip('no events today in the configured calendar — not a failure')

  assert 'events' in result
  assert len(result['events']) == 1
  lines = result['events'][0]
  assert lines, 'events list is empty'
  for line in lines:
    assert isinstance(line, str) and line.strip(), f'empty line in events: {lines!r}'


@pytest.mark.integration
@pytest.mark.require_env('CALENDAR_CALDAV_URL', 'CALENDAR_USERNAME', 'CALENDAR_PASSWORD')
def test_caldav_mode_real_icloud(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() connects to a real CalDAV server and returns valid events or raises cleanly."""
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'calendar': {
        'caldav_url': os.environ['CALENDAR_CALDAV_URL'],
        'username': os.environ['CALENDAR_USERNAME'],
        'password': os.environ['CALENDAR_PASSWORD'],
      },
      'scheduler': {},
    },
  )

  try:
    result = calendar.get_variables()
  except IntegrationDataUnavailableError:
    pytest.skip('no events today in the CalDAV calendars — not a failure')

  assert 'events' in result
  lines = result['events'][0]
  assert lines, 'events list is empty'
  for line in lines:
    assert isinstance(line, str) and line.strip(), f'empty line in events: {lines!r}'


@pytest.mark.integration
@pytest.mark.require_env('CALENDAR_CARDDAV_URL', 'CALENDAR_USERNAME', 'CALENDAR_PASSWORD')
def test_birthdays_mode_real_icloud(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables_birthdays() connects to real iCloud Contacts and returns valid results."""
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'calendar': {
        'carddav_url': os.environ['CALENDAR_CARDDAV_URL'],
        'username': os.environ['CALENDAR_USERNAME'],
        'password': os.environ['CALENDAR_PASSWORD'],
        'birthdays_lookahead_days': 365,
      },
      'scheduler': {},
    },
  )

  result = calendar.get_variables_birthdays()

  assert 'birthdays' in result
  lines = result['birthdays'][0]
  assert lines, 'birthdays list is empty'
  for line in lines:
    assert isinstance(line, str) and line.strip(), f'empty line in birthdays: {lines!r}'
    parts = line.split()
    assert len(parts) == 2, f'expected "FIRSTNAME DAY", got: {line!r}'  # noqa: PLR2004
    assert parts[1] in {'TODAY', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN'}, f'unexpected day label: {line!r}'
