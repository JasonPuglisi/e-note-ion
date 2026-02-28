"""Integration tests for integrations/calendar.py — call real calendar feeds.

Run with: uv run pytest -m integration

Required env vars (at least one mode must be configured):
  CALENDAR_URL             — public/secret-address .ics URL (ICS mode)
  CALENDAR_CALDAV_URL      — CalDAV server URL (CalDAV mode)
  CALENDAR_USERNAME        — CalDAV username / Apple ID
  CALENDAR_PASSWORD        — CalDAV app-specific password
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
  yield
  calendar._ics_cache.clear()
  calendar._caldav_cache = None


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
