import importlib.util
import json
import time
from pathlib import Path
from queue import Empty
from typing import Any, Generator

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

# e-note-ion.py has a hyphen in its name so it can't be imported with a
# standard import statement. Load it via importlib instead.
_ROOT = Path(__file__).parent.parent.parent
_spec = importlib.util.spec_from_file_location('scheduler', _ROOT / 'e-note-ion.py')
assert _spec is not None and _spec.loader is not None
_mod: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]


@pytest.fixture()
def sched() -> Generator[BackgroundScheduler, None, None]:
  s = BackgroundScheduler()
  yield s
  if s.running:
    s.shutdown(wait=False)


@pytest.fixture(autouse=True)
def drain_queue() -> Generator[None, None, None]:
  """Drain the shared queue before each test to prevent cross-test pollution."""
  while True:
    try:
      _mod._queue.get_nowait()
    except Empty:
      break
  yield


# --- parse_cron ---


def test_parse_cron_valid() -> None:
  result = _mod.parse_cron('0 8 * * 1-5')
  assert result == {
    'minute': '0',
    'hour': '8',
    'day': '*',
    'month': '*',
    'day_of_week': '1-5',
  }


def test_parse_cron_too_few_fields() -> None:
  with pytest.raises(ValueError):
    _mod.parse_cron('0 8 * *')


def test_parse_cron_too_many_fields() -> None:
  with pytest.raises(ValueError):
    _mod.parse_cron('0 8 * * * extra')


# --- QueuedMessage ordering ---


def test_higher_priority_sorts_first() -> None:
  low = _mod.QueuedMessage(priority=3, seq=0, name='low', scheduled_at=0.0, data={}, hold=60, timeout=60)
  high = _mod.QueuedMessage(priority=8, seq=1, name='high', scheduled_at=0.0, data={}, hold=60, timeout=60)
  assert high < low  # min-heap: high priority should be popped first


def test_equal_priority_earlier_seq_first() -> None:
  first = _mod.QueuedMessage(priority=5, seq=0, name='first', scheduled_at=0.0, data={}, hold=60, timeout=60)
  second = _mod.QueuedMessage(priority=5, seq=1, name='second', scheduled_at=0.0, data={}, hold=60, timeout=60)
  assert first < second


# --- pop_valid_message ---


def test_pop_valid_message_returns_message() -> None:
  msg = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='test',
    scheduled_at=time.monotonic(),
    data={},
    hold=60,
    timeout=60,
  )
  _mod._queue.put(msg)
  result = _mod.pop_valid_message()
  assert result is msg


def test_pop_valid_message_discards_expired_returns_next() -> None:
  expired = _mod.QueuedMessage(
    priority=5,
    seq=0,
    name='expired',
    scheduled_at=time.monotonic() - 100,  # waited 100s
    data={},
    hold=60,
    timeout=10,  # timeout only 10s
  )
  valid = _mod.QueuedMessage(
    priority=5,
    seq=1,
    name='valid',
    scheduled_at=time.monotonic(),
    data={},
    hold=60,
    timeout=60,
  )
  _mod._queue.put(expired)
  _mod._queue.put(valid)
  result = _mod.pop_valid_message()
  assert result is not None
  assert result.name == 'valid'


def test_pop_valid_message_returns_none_when_empty() -> None:
  # Queue is empty (drained by autouse fixture); waits up to 1s then returns None
  result = _mod.pop_valid_message()
  assert result is None


# --- _load_file ---


def _make_content(
  *,
  priority: int = 5,
  public: bool = True,
  truncation: str | None = None,
) -> dict[str, Any]:
  template: dict[str, Any] = {
    'schedule': {'cron': '0 8 * * *', 'hold': 60, 'timeout': 60},
    'priority': priority,
    'public': public,
    'templates': [{'format': ['HELLO']}],
  }
  if truncation is not None:
    template['truncation'] = truncation
  return {'templates': {'tmpl': template}}


def test_load_file_registers_job(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content()))
  _mod._load_file(sched, f, False)
  assert len(sched.get_jobs()) == 1


def test_load_file_invalid_priority_raises(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'bad.json'
  f.write_text(json.dumps(_make_content(priority=99)))
  with pytest.raises(ValueError, match='priority'):
    _mod._load_file(sched, f, False)


def test_load_file_invalid_truncation_raises(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'bad.json'
  f.write_text(json.dumps(_make_content(truncation='bogus')))
  with pytest.raises(ValueError, match='truncation'):
    _mod._load_file(sched, f, False)


def test_load_file_public_mode_skips_private(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(public=False)))
  _mod._load_file(sched, f, public_mode=True)
  assert len(sched.get_jobs()) == 0


def test_load_file_public_mode_keeps_public(sched: BackgroundScheduler, tmp_path: Path) -> None:
  f = tmp_path / 'test.json'
  f.write_text(json.dumps(_make_content(public=True)))
  _mod._load_file(sched, f, public_mode=True)
  assert len(sched.get_jobs()) == 1
