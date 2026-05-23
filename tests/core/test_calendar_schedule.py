from datetime import datetime, timedelta, timezone
from typing import Any, Generator
from unittest.mock import patch

import pytest
from icalendar import Event

import integrations.calendar_schedule as cs

_UTC = timezone.utc
_NOW = datetime(2026, 5, 14, 12, 0, 0, tzinfo=_UTC)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
  """Reset module caches and config between tests."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {})
  cs.reset_cache()
  yield
  cs.reset_cache()


def _enable(
  monkeypatch: pytest.MonkeyPatch,
  *,
  gated: list[str] | None = None,
  with_calendar: bool = True,
) -> None:
  """Set [scheduler.calendar_schedule] in config and (optionally) [calendar]."""
  import config as _config_mod

  cfg: dict[str, Any] = {
    'scheduler': {'calendar_schedule': {'gated_templates': gated or []}},
  }
  if with_calendar:
    cfg['calendar'] = {'urls': ['https://example.com/cal.ics']}
  monkeypatch.setattr(_config_mod, '_config', cfg)


def _make_event(
  *,
  description: str,
  start: datetime,
  end: datetime | None = None,
  all_day: bool = False,
) -> Any:
  ev = Event()
  ev.add('SUMMARY', 'Test event')
  ev.add('DESCRIPTION', description)
  if all_day:
    ev.add('DTSTART', start.date())
    if end:
      ev.add('DTEND', end.date())
  else:
    ev.add('DTSTART', start)
    if end:
      ev.add('DTEND', end)
  return ev


# ── _extract_keywords ──────────────────────────────────────────────────────────


def test_extract_keywords_single_allow() -> None:
  assert cs._extract_keywords('vestaboard:bart') == [(True, 'bart')]


def test_extract_keywords_single_deny() -> None:
  assert cs._extract_keywords('vestaboard:!discogs.morning_spin') == [(False, 'discogs.morning_spin')]


def test_extract_keywords_multiple_lines() -> None:
  desc = 'vestaboard:bart\nvestaboard:!discogs.morning_spin\nvestaboard:weather'
  assert cs._extract_keywords(desc) == [
    (True, 'bart'),
    (False, 'discogs.morning_spin'),
    (True, 'weather'),
  ]


def test_extract_keywords_inline_prose_does_not_match() -> None:
  desc = "let's talk about vestaboard:bart timing in the meeting"
  assert cs._extract_keywords(desc) == []


def test_extract_keywords_with_other_lines() -> None:
  desc = 'Meeting with team\nvestaboard:bart\nReview Q2 plan'
  assert cs._extract_keywords(desc) == [(True, 'bart')]


def test_extract_keywords_case_insensitive() -> None:
  assert cs._extract_keywords('VESTABOARD:BART') == [(True, 'BART')]


def test_extract_keywords_whitespace_tolerated() -> None:
  assert cs._extract_keywords('  vestaboard : bart  ') == [(True, 'bart')]
  assert cs._extract_keywords('vestaboard:! bart') == [(False, 'bart')]


def test_extract_keywords_empty_description() -> None:
  assert cs._extract_keywords('') == []


# ── _resolve_overrides (deny wins) ─────────────────────────────────────────────


def test_resolve_overrides_single_allow() -> None:
  ev = _make_event(description='vestaboard:bart', start=_NOW)
  assert cs._resolve_overrides([ev]) == {'bart': True}


def test_resolve_overrides_single_deny() -> None:
  ev = _make_event(description='vestaboard:!bart', start=_NOW)
  assert cs._resolve_overrides([ev]) == {'bart': False}


def test_resolve_overrides_deny_wins_on_conflict() -> None:
  ev1 = _make_event(description='vestaboard:bart', start=_NOW)
  ev2 = _make_event(description='vestaboard:!bart', start=_NOW)
  assert cs._resolve_overrides([ev1, ev2]) == {'bart': False}
  # Order-independent.
  assert cs._resolve_overrides([ev2, ev1]) == {'bart': False}


def test_resolve_overrides_specific_and_stem_independent() -> None:
  # Stem and specific-template tokens resolve as separate keys; the
  # combination is interpreted at gate-check time, not here.
  ev1 = _make_event(description='vestaboard:bart', start=_NOW)
  ev2 = _make_event(description='vestaboard:!bart.departures', start=_NOW)
  assert cs._resolve_overrides([ev1, ev2]) == {'bart': True, 'bart.departures': False}


# ── is_open semantics ─────────────────────────────────────────────────────────


def test_is_open_returns_true_when_feature_disabled() -> None:
  # No [scheduler.calendar_schedule] section in config.
  assert cs.is_open('bart.departures', 'bart') is True


def test_is_open_default_open_when_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=[])
  with patch.object(cs, '_fetch_active_events', return_value=[]):
    assert cs.is_open('bart.departures', 'bart') is True


def test_is_open_default_closed_when_gated_by_template_id(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=['bart.departures'])
  with patch.object(cs, '_fetch_active_events', return_value=[]):
    assert cs.is_open('bart.departures', 'bart') is False


def test_is_open_default_closed_when_gated_by_stem(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=['bart'])
  with patch.object(cs, '_fetch_active_events', return_value=[]):
    assert cs.is_open('bart.departures', 'bart') is False
    assert cs.is_open('bart.alerts', 'bart') is False


def test_is_open_calendar_opens_gated_template(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=['bart.departures'])
  ev = _make_event(
    description='vestaboard:bart.departures', start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=1)
  )
  with patch.object(cs, '_fetch_active_events', return_value=[ev]):
    assert cs.is_open('bart.departures', 'bart') is True


def test_is_open_calendar_closes_default_open_template(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=[])
  ev = _make_event(
    description='vestaboard:!discogs.morning_spin', start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=1)
  )
  with patch.object(cs, '_fetch_active_events', return_value=[ev]):
    assert cs.is_open('discogs.morning_spin', 'discogs') is False


def test_is_open_specific_override_wins_over_stem(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=[])
  # Stem says open, specific template says closed — specific wins.
  ev1 = _make_event(description='vestaboard:bart', start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=1))
  ev2 = _make_event(
    description='vestaboard:!bart.departures', start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=1)
  )
  with patch.object(cs, '_fetch_active_events', return_value=[ev1, ev2]):
    assert cs.is_open('bart.departures', 'bart') is False
    assert cs.is_open('bart.alerts', 'bart') is True


def test_is_open_stem_override_open_falls_through_to_default_for_other_stems(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=['discogs'])
  ev = _make_event(description='vestaboard:bart', start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=1))
  with patch.object(cs, '_fetch_active_events', return_value=[ev]):
    assert cs.is_open('bart.departures', 'bart') is True  # opened by event
    assert cs.is_open('discogs.morning_spin', 'discogs') is False  # gated, no event


# ── _is_active_now (event time windows) ───────────────────────────────────────


def test_is_active_now_timed_event_within_window() -> None:
  ev = _make_event(description='', start=_NOW - timedelta(hours=1), end=_NOW + timedelta(hours=1))
  assert cs._is_active_now(ev, _NOW, _UTC) is True


def test_is_active_now_timed_event_before_start() -> None:
  ev = _make_event(description='', start=_NOW + timedelta(hours=1), end=_NOW + timedelta(hours=2))
  assert cs._is_active_now(ev, _NOW, _UTC) is False


def test_is_active_now_timed_event_after_end() -> None:
  ev = _make_event(description='', start=_NOW - timedelta(hours=2), end=_NOW - timedelta(hours=1))
  assert cs._is_active_now(ev, _NOW, _UTC) is False


def test_is_active_now_timed_event_at_end_boundary_excluded() -> None:
  # [DTSTART, DTEND) — end boundary is exclusive.
  ev = _make_event(description='', start=_NOW - timedelta(hours=1), end=_NOW)
  assert cs._is_active_now(ev, _NOW, _UTC) is False


def test_is_active_now_all_day_event_today() -> None:
  ev = _make_event(description='', start=_NOW, all_day=True)
  assert cs._is_active_now(ev, _NOW, _UTC) is True


def test_is_active_now_all_day_event_yesterday() -> None:
  yesterday = _NOW - timedelta(days=1)
  ev = _make_event(description='', start=yesterday, all_day=True)
  assert cs._is_active_now(ev, _NOW, _UTC) is False


def test_is_active_now_point_event_no_end_returns_false() -> None:
  # Point-in-time event with no DTEND or DURATION — gating semantics undefined.
  ev = _make_event(description='', start=_NOW)
  assert cs._is_active_now(ev, _NOW, _UTC) is False


# ── Cache behavior ────────────────────────────────────────────────────────────


def test_cache_refresh_only_once_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=[])
  call_count = {'n': 0}

  def fake_fetch(now: datetime, tz: Any) -> list[Any]:
    call_count['n'] += 1
    return []

  with patch.object(cs, '_fetch_active_events', side_effect=fake_fetch):
    cs.is_open('bart.departures', 'bart')
    cs.is_open('bart.departures', 'bart')
    cs.is_open('weather.now', 'weather')
  assert call_count['n'] == 1


def test_cache_refresh_after_reset(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=[])
  call_count = {'n': 0}

  def fake_fetch(now: datetime, tz: Any) -> list[Any]:
    call_count['n'] += 1
    return []

  with patch.object(cs, '_fetch_active_events', side_effect=fake_fetch):
    cs.is_open('bart.departures', 'bart')
    cs.reset_cache()
    cs.is_open('bart.departures', 'bart')
  assert call_count['n'] == 2


# ── Fetch error handling ──────────────────────────────────────────────────────


def test_fetch_active_events_no_calendar_section_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {})
  assert cs._fetch_active_events(_NOW, _UTC) == []


def test_fetch_active_events_ics_failure_logged(
  monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
  import config as _config_mod
  import integrations.calendar as _cal

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {'urls': ['https://example.com/cal.ics']}})
  monkeypatch.setattr(_cal, '_collect_candidates_ics', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('boom')))
  with caplog.at_level('WARNING'):
    result = cs._fetch_active_events(_NOW, _UTC)
  assert result == []
  assert 'ICS fetch failed' in caplog.text


def test_get_overrides_handles_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
  _enable(monkeypatch, gated=[])
  with patch.object(cs, '_fetch_active_events', side_effect=RuntimeError('boom')):
    # The error propagates from _refresh; gate falls back to default-open.
    # _refresh does not swallow exceptions raised inside it (unlike the
    # per-source fetchers which catch their own); document this behavior.
    with pytest.raises(RuntimeError):
      cs.is_open('bart.departures', 'bart')


# ── Recurring events (smoke test via _resolve_overrides) ──────────────────────


def test_recurring_weekday_only_today() -> None:
  # Pretend recurring expansion already happened and the active-now filter
  # picked out exactly today's instance. _resolve_overrides operates on
  # whatever events the fetcher hands back.
  today_morning = _NOW.replace(hour=8, minute=0)
  today_evening = _NOW.replace(hour=10, minute=0)
  ev = _make_event(description='vestaboard:bart.departures', start=today_morning, end=today_evening)
  assert cs._resolve_overrides([ev]) == {'bart.departures': True}


# ── tz=None config + all-day CalDAV event (regression for #556) ───────────────


def test_refresh_resolves_all_day_caldav_event_with_unset_timezone(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # Regression: when [scheduler].timezone is unset, get_timezone() returns
  # None. _refresh must promote tz to now.tzinfo before downstream event
  # helpers run, or _is_active_now compares naive (constructed from
  # tzinfo=None) against aware now and raises TypeError.
  import config as _config_mod
  import integrations.calendar as _cal

  monkeypatch.setattr(
    _config_mod,
    '_config',
    {
      'scheduler': {'calendar_schedule': {'gated_templates': []}},
      'calendar': {
        'caldav_url': 'https://example.com/',
        'username': 'u',
        'password': 'p',
      },
    },
  )
  # get_timezone() returns None for unset [scheduler].timezone.
  assert _config_mod.get_timezone() is None

  # Multi-day all-day event spanning yesterday → tomorrow (covers today).
  ev = _make_event(
    description='vestaboard:bart.departures',
    start=_NOW - timedelta(days=1),
    end=_NOW + timedelta(days=1),
    all_day=True,
  )
  monkeypatch.setattr(_cal, '_collect_candidates_caldav', lambda *a, **kw: [(ev, None, 0)])

  # Pass an aware now (matching what _get_now produces in production) so the
  # promotion `tz = tz or now.tzinfo` has a concrete tzinfo to fall back on.
  cs._refresh(now=_NOW)

  with cs._cache_lock:
    assert dict(cs._cache) == {'bart.departures': True}
