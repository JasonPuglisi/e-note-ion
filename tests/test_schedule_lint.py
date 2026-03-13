# tests/test_schedule_lint.py
#
# Schedule linter — validates all content JSON templates against scheduling
# conventions. Runs as part of the default pytest suite.
#
# Checks:
#   1. Required fields  — every template has hold and timeout
#   2. Priority range   — priority is 0–10
#   3. Hold floor       — hold >= 30 s
#   4. Sub-hourly timeout ceiling — */N (N < 15) templates have timeout <= 120 s
#   5. Slot hold budget — no shared slot exceeds 1800 s combined hold
#      (priority >= 9 templates excluded: they are brief personal inserts
#       that show first and return quickly, so they don't participate in
#       the shared budget the same way)
#   6. Webhook-only fields — webhook-only templates still have hold and timeout

import json
import re
from pathlib import Path
from typing import Any

import pytest

_CONTENT_ROOT = Path(__file__).parent.parent / 'content'
_CONTRIB_DIR = _CONTENT_ROOT / 'contrib'
_USER_DIR = _CONTENT_ROOT / 'user'

_SLOT_BUDGET_SECS = 1800
_PERSONAL_PRIORITY_FLOOR = 9  # templates at this priority or above are excluded from slot budget


def _load_all_templates() -> list[tuple[str, str, dict[str, Any]]]:
  """Return (file, template_name, template_dict) for every template in contrib and user dirs."""
  results: list[tuple[str, str, dict[str, Any]]] = []
  dirs = [_CONTRIB_DIR]
  if _USER_DIR.exists():
    dirs.append(_USER_DIR)
  for content_dir in dirs:
    for json_file in sorted(content_dir.glob('*.json')):
      with open(json_file) as f:
        data = json.load(f)
      for name, tmpl in data.get('templates', {}).items():
        results.append((json_file.name, name, tmpl))
  return results


_ALL_TEMPLATES = _load_all_templates()


@pytest.mark.parametrize('file,name,tmpl', _ALL_TEMPLATES)
def test_required_schedule_fields(file: str, name: str, tmpl: dict[str, Any]) -> None:
  """Every template must have hold and timeout."""
  schedule = tmpl.get('schedule', {})
  assert 'hold' in schedule, f'{file}:{name} — missing schedule.hold'
  assert 'timeout' in schedule, f'{file}:{name} — missing schedule.timeout'


@pytest.mark.parametrize('file,name,tmpl', _ALL_TEMPLATES)
def test_priority_range(file: str, name: str, tmpl: dict[str, Any]) -> None:
  """priority must be 0–10."""
  priority = tmpl.get('priority')
  assert priority is not None, f'{file}:{name} — missing priority'
  assert isinstance(priority, int) and 0 <= priority <= 10, f'{file}:{name} — priority {priority} is out of range 0–10'


@pytest.mark.parametrize('file,name,tmpl', _ALL_TEMPLATES)
def test_hold_floor(file: str, name: str, tmpl: dict[str, Any]) -> None:
  """hold must be >= 30 s."""
  hold = tmpl.get('schedule', {}).get('hold')
  if hold is not None:
    assert hold >= 30, f'{file}:{name} — hold={hold} s is suspiciously low (minimum 30 s)'


@pytest.mark.parametrize('file,name,tmpl', _ALL_TEMPLATES)
def test_sub_hourly_timeout(file: str, name: str, tmpl: dict[str, Any]) -> None:
  """Sub-hourly cron templates (*/N where N < 15) should have timeout <= 120 s.

  Stale sub-hourly messages accumulate quickly; a short timeout ensures they
  are discarded rather than shown late.
  """
  cron = tmpl.get('schedule', {}).get('cron', '')
  if not cron:
    return
  minute_field = cron.split()[0]
  m = re.match(r'^\*/(\d+)$', minute_field)
  if m and int(m.group(1)) < 15:
    timeout = tmpl.get('schedule', {}).get('timeout', 0)
    assert timeout <= 120, (
      f'{file}:{name} — sub-hourly cron (*/{m.group(1)}) should have timeout <= 120 s, '
      f'got {timeout} s; stale sub-hourly messages accumulate quickly'
    )


@pytest.mark.parametrize('file,name,tmpl', _ALL_TEMPLATES)
def test_webhook_only_has_schedule_fields(file: str, name: str, tmpl: dict[str, Any]) -> None:
  """Webhook-only templates (webhook=true, no cron) must still have hold and timeout."""
  if not tmpl.get('webhook'):
    return
  if tmpl.get('schedule', {}).get('cron'):
    return  # has a cron too — covered by required-fields check
  schedule = tmpl.get('schedule', {})
  assert 'hold' in schedule, f'{file}:{name} — webhook-only template missing schedule.hold'
  assert 'timeout' in schedule, f'{file}:{name} — webhook-only template missing schedule.timeout'


def _parse_minute_mark(minute_field: str) -> int | None:
  """Return the canonical minute mark for a cron minute field, or None for sub-hourly.

  Sub-hourly expressions (*/N where N < 15) are excluded from slot budget
  checks — they're designed to dominate the display and are documented as such.
  """
  m = re.match(r'^\*/(\d+)$', minute_field)
  if m:
    return None if int(m.group(1)) < 15 else int(m.group(1))
  # Single value or first value of a comma list
  first = minute_field.split(',')[0].strip()
  m = re.match(r'^(\d+)$', first)
  return int(m.group(1)) if m else None


def _expand_hours(hour_field: str) -> set[int]:
  """Parse a cron hour field into the set of hours it covers."""
  if hour_field == '*':
    return set(range(24))
  hours: set[int] = set()
  for part in hour_field.split(','):
    part = part.strip()
    if '-' in part:
      start, end = part.split('-', 1)
      hours.update(range(int(start), int(end) + 1))
    elif part.startswith('*/'):
      step = int(part[2:])
      hours.update(range(0, 24, step))
    else:
      hours.add(int(part))
  return hours


def test_slot_hold_budget() -> None:
  """No shared cron slot may exceed 1800 s of combined hold.

  Two templates share a slot when they have the same minute mark and at least
  one overlapping hour. The worst-case budget for a slot is the sum of holds
  for all templates that can fire at the same clock time.

  Templates with priority >= 9 are excluded: they are brief personal inserts
  (e.g. feeding reminders, self-birthday) that always show first and finish
  quickly. The remaining templates — the ones that need to queue behind them —
  are what this budget protects.
  """
  eligible: list[tuple[int, set[int], int, str, str]] = []

  for file, name, tmpl in _ALL_TEMPLATES:
    schedule = tmpl.get('schedule', {})
    cron = schedule.get('cron', '')
    hold = schedule.get('hold')
    priority = tmpl.get('priority', 5)

    if not cron or hold is None:
      continue
    if priority >= _PERSONAL_PRIORITY_FLOOR:
      continue  # personal-tier inserts exempt from shared budget

    parts = cron.split()
    if len(parts) != 5:
      continue

    minute_mark = _parse_minute_mark(parts[0])
    if minute_mark is None:
      continue  # sub-hourly excluded

    hours = _expand_hours(parts[1])
    eligible.append((minute_mark, hours, hold, file, name))

  violations: list[str] = []
  for mark in sorted({e[0] for e in eligible}):
    slot = [(hours, hold, f, n) for (m, hours, hold, f, n) in eligible if m == mark]
    all_hours = set().union(*(hours for hours, _, _, _ in slot))
    for hour in sorted(all_hours):
      firing = [(hold, f, n) for (hours, hold, f, n) in slot if hour in hours]
      if len(firing) < 2:
        continue
      total = sum(h for h, _, _ in firing)
      if total > _SLOT_BUDGET_SECS:
        participants = ', '.join(f'{f}:{n} ({h} s)' for h, f, n in firing)
        violations.append(
          f'  :{mark:02d} at {hour:02d}:00 — combined {total} s > {_SLOT_BUDGET_SECS} s: {participants}'
        )

  assert not violations, 'Slot hold budget exceeded:\n' + '\n'.join(violations)
