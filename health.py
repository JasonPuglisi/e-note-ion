# health.py
#
# Health tracking for the Vestaboard scheduler. Records outcomes for two
# kinds of targets:
#   1. Integrations — data sources (weather, bart, discogs, …) tracked via
#      success / expected_empty / error events.
#   2. The Vestaboard send path itself — tracked under the reserved target
#      name 'vestaboard' via success / error / locked events. This lets the
#      /health endpoint distinguish "fetch failed" from "display POST failed"
#      so a Vestaboard outage doesn't smear across every integration.
#
# Exposes a summary for the /health endpoint and periodic console log.
#
# Thread-safe: all state is behind a single lock.
#
# Events are persisted to data/health.jsonl so that health history survives
# container restarts. On startup, historical events are loaded from disk and
# entries older than _PURGE_DAYS are discarded. The data/ directory is
# declared as a Docker VOLUME so it persists automatically across container
# stop/start/recreate cycles without user configuration.

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOG_INTERVAL = 3600  # seconds between periodic health log entries

# --- Persistence and threshold constants ---

# Runtime state directory. Declared as a Docker VOLUME (/app/data) so it
# persists across container lifecycle events without user-visible mounts.
# Other features that need persistent runtime state should also use this
# directory.
_LOG_DIR = Path('data')
_LOG_PATH = _LOG_DIR / 'health.jsonl'

_PURGE_DAYS = 7  # discard events older than this on startup and hourly
_PURGE_SECONDS = _PURGE_DAYS * 86400

# Status is HEALTHY when the non-error rate meets or exceeds this threshold.
_SUCCESS_RATE_THRESHOLD = 0.7

# Number of recent events to keep per integration.
_WINDOW_SIZE = 20


class EventType(Enum):
  SUCCESS = 'success'
  EXPECTED_EMPTY = 'expected_empty'
  ERROR = 'error'
  LOCKED = 'locked'


class Status(Enum):
  HEALTHY = 'healthy'
  DEGRADED = 'degraded'
  ERROR = 'error'
  UNKNOWN = 'unknown'


@dataclass
class HealthEvent:
  timestamp: float
  event_type: EventType
  error_message: str | None = None


@dataclass
class _TargetState:
  registered_at: float
  events: deque[HealthEvent] = field(default_factory=lambda: deque(maxlen=_WINDOW_SIZE))


# Reserved target name for the Vestaboard send path. Registered implicitly
# alongside the user's integrations so the /health endpoint can attribute
# display failures separately from integration data fetch failures.
VESTABOARD_TARGET = 'vestaboard'

_lock = threading.Lock()
_targets: dict[str, _TargetState] = {}
_started_at: float = 0.0
_log_timer: threading.Timer | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _load_log() -> None:
  """Load historical health events from disk, purging entries older than
  _PURGE_DAYS. Called once during init() before any integrations fire.

  Handles missing files (fresh install) and malformed lines (skipped with
  a warning). After loading, rewrites the file without purged entries.
  """
  try:
    lines = _LOG_PATH.read_text().splitlines()
  except FileNotFoundError:
    return
  except OSError as e:
    logger.warning('Health: could not read %s — %s', _LOG_PATH, e)
    return

  cutoff = time.time() - _PURGE_SECONDS
  kept_lines: list[str] = []

  for i, line in enumerate(lines):
    line = line.strip()
    if not line:
      continue
    try:
      obj = json.loads(line)
    except json.JSONDecodeError:
      logger.warning('Health: skipping malformed line %d in %s', i + 1, _LOG_PATH)
      continue

    ts = obj.get('ts')
    if not isinstance(ts, (int, float)) or ts < cutoff:
      continue

    name = obj.get('name', '')
    event_str = obj.get('event', '')
    try:
      event_type = EventType(event_str)
    except ValueError:
      logger.warning('Health: unknown event type %r on line %d', event_str, i + 1)
      continue

    error_msg = obj.get('error') if event_type == EventType.ERROR else None

    if name not in _targets:
      _targets[name] = _TargetState(registered_at=ts)
    _targets[name].events.append(HealthEvent(ts, event_type, error_msg))
    kept_lines.append(line)

  # Rewrite the file without purged entries.
  try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_PATH.write_text('\n'.join(kept_lines) + '\n' if kept_lines else '')
  except OSError as e:
    logger.warning('Health: could not rewrite %s — %s', _LOG_PATH, e)


def _append_log(name: str, event_type: EventType, ts: float, error: str | None = None) -> None:
  """Append a single event to the health log file. Caller holds _lock."""
  entry: dict[str, Any] = {'ts': ts, 'name': name, 'event': event_type.value}
  if error is not None:
    entry['error'] = error
  try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOG_PATH, 'a') as f:
      f.write(json.dumps(entry, separators=(',', ':')) + '\n')
  except OSError as e:
    logger.warning('Health: could not write to %s — %s', _LOG_PATH, e)


def _purge_stale_events() -> bool:
  """Remove events older than _PURGE_DAYS from in-memory state.

  Returns True if any events were purged (caller should rewrite the log).
  Caller holds _lock.
  """
  cutoff = time.time() - _PURGE_SECONDS
  purged = False
  for state in _targets.values():
    before = len(state.events)
    state.events = deque(
      (e for e in state.events if e.timestamp >= cutoff),
      maxlen=_WINDOW_SIZE,
    )
    if len(state.events) < before:
      purged = True
  return purged


def _rewrite_log() -> None:
  """Rewrite the log file from current in-memory state. Caller holds _lock."""
  lines: list[str] = []
  for name, state in sorted(_targets.items()):
    for event in state.events:
      entry: dict[str, Any] = {
        'ts': event.timestamp,
        'name': name,
        'event': event.event_type.value,
      }
      if event.error_message is not None:
        entry['error'] = event.error_message
      lines.append(json.dumps(entry, separators=(',', ':')))
  try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_PATH.write_text('\n'.join(lines) + '\n' if lines else '')
  except OSError as e:
    logger.warning('Health: could not rewrite %s — %s', _LOG_PATH, e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init() -> None:
  """Initialize the health system. Call once at scheduler startup.

  Loads persisted events from disk and registers the reserved Vestaboard
  send-path target so /health always reports it alongside integrations.
  """
  global _started_at
  _started_at = time.time()
  with _lock:
    _load_log()
    if VESTABOARD_TARGET not in _targets:
      _targets[VESTABOARD_TARGET] = _TargetState(registered_at=time.time())


def register(name: str) -> None:
  """Register a target for health tracking.

  Called at content load time for each integration template. Safe to call
  multiple times for the same name (e.g. when multiple templates use the
  same integration with different functions).
  """
  with _lock:
    if name not in _targets:
      _targets[name] = _TargetState(registered_at=time.time())


def record_success(name: str) -> None:
  """Record a successful call against the named target."""
  with _lock:
    state = _targets.get(name)
    if state is None:
      return
    ts = time.time()
    state.events.append(HealthEvent(ts, EventType.SUCCESS))
    _append_log(name, EventType.SUCCESS, ts)


def record_expected_empty(name: str) -> None:
  """Record an integration call that returned no data (expected)."""
  with _lock:
    state = _targets.get(name)
    if state is None:
      return
    ts = time.time()
    state.events.append(HealthEvent(ts, EventType.EXPECTED_EMPTY))
    _append_log(name, EventType.EXPECTED_EMPTY, ts)


def record_error(name: str, error: str) -> None:
  """Record a failed call against the named target."""
  with _lock:
    state = _targets.get(name)
    if state is None:
      return
    ts = time.time()
    state.events.append(HealthEvent(ts, EventType.ERROR, error))
    _append_log(name, EventType.ERROR, ts, error)


def record_locked(name: str) -> None:
  """Record a Vestaboard 'locked' response (HTTP 423).

  423 is expected behavior during quiet hours or when the board is briefly
  rate-limited, so it is tracked separately from errors and does NOT count
  toward the success-rate denominator used to compute status.
  """
  with _lock:
    state = _targets.get(name)
    if state is None:
      return
    ts = time.time()
    state.events.append(HealthEvent(ts, EventType.LOCKED))
    _append_log(name, EventType.LOCKED, ts)


def _compute_status(state: _TargetState) -> Status:
  """Compute health status for a single target. Caller holds _lock.

  Uses a success-rate threshold instead of binary error detection. LOCKED
  events are excluded from the denominator so expected quiet-hours 423
  responses do not tank status.
  - No scored events → unknown
  - No errors → healthy
  - All errors → error
  - Non-error rate ≥ _SUCCESS_RATE_THRESHOLD → healthy
  - Below threshold → degraded
  """
  scored = [e for e in state.events if e.event_type != EventType.LOCKED]
  if not scored:
    return Status.UNKNOWN
  errors = sum(1 for e in scored if e.event_type == EventType.ERROR)
  total = len(scored)
  if errors == 0:
    return Status.HEALTHY
  if errors == total:
    return Status.ERROR
  if (total - errors) / total >= _SUCCESS_RATE_THRESHOLD:
    return Status.HEALTHY
  return Status.DEGRADED


def _format_timestamp(ts: float | None) -> str | None:
  """Convert a Unix timestamp to ISO 8601 UTC string, or None."""
  if ts is None:
    return None
  return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _target_detail(name: str, state: _TargetState) -> dict[str, Any]:
  """Build the per-target health detail dict. Caller holds _lock.

  `success_rate` and `total_events` exclude LOCKED events (they are not
  failures and would skew the denominator). `locked_events` reports the
  raw count of locked events in the window.
  """
  status = _compute_status(state)
  scored = [e for e in state.events if e.event_type != EventType.LOCKED]
  total = len(scored)
  errors = sum(1 for e in scored if e.event_type == EventType.ERROR)
  ok = total - errors
  locked = sum(1 for e in state.events if e.event_type == EventType.LOCKED)

  last_success: float | None = None
  last_expected_empty: float | None = None
  last_error: float | None = None
  last_error_message: str | None = None
  last_locked: float | None = None

  for event in reversed(state.events):
    if event.event_type == EventType.SUCCESS and last_success is None:
      last_success = event.timestamp
    elif event.event_type == EventType.EXPECTED_EMPTY and last_expected_empty is None:
      last_expected_empty = event.timestamp
    elif event.event_type == EventType.ERROR and last_error is None:
      last_error = event.timestamp
      last_error_message = event.error_message
    elif event.event_type == EventType.LOCKED and last_locked is None:
      last_locked = event.timestamp

  detail: dict[str, Any] = {
    'status': status.value,
    'last_success': _format_timestamp(last_success),
    'last_expected_empty': _format_timestamp(last_expected_empty),
    'last_error': _format_timestamp(last_error),
    'last_error_message': last_error_message,
    'last_locked': _format_timestamp(last_locked),
    'locked_events': locked,
    'success_rate': ok / total if total else None,
    'total_events': total,
    'registered_at': _format_timestamp(state.registered_at),
  }
  return detail


def overall_status() -> Status:
  """Return the worst status across all registered integrations."""
  with _lock:
    worst = Status.HEALTHY
    for state in _targets.values():
      s = _compute_status(state)
      if s == Status.ERROR:
        return Status.ERROR
      if s == Status.DEGRADED:
        worst = Status.DEGRADED
    return worst


def get_summary() -> dict[str, Any]:
  """Return the full health summary for the /health endpoint.

  The Vestaboard send-path target is returned under its own top-level
  `vestaboard` key, separate from the `integrations` dict, so display
  failures can be attributed distinctly from integration fetch failures.
  """
  with _lock:
    integrations: dict[str, Any] = {}
    vestaboard_detail: dict[str, Any] | None = None
    worst = Status.HEALTHY
    for name, state in sorted(_targets.items()):
      detail = _target_detail(name, state)
      if name == VESTABOARD_TARGET:
        vestaboard_detail = detail
      else:
        integrations[name] = detail
      s = Status(detail['status'])
      if s == Status.ERROR:
        worst = Status.ERROR
      elif s == Status.DEGRADED and worst != Status.ERROR:
        worst = Status.DEGRADED

    return {
      'status': worst.value,
      'uptime_seconds': round(time.time() - _started_at) if _started_at else 0,
      'vestaboard': vestaboard_detail,
      'integrations': integrations,
    }


def _log_summary() -> None:
  """Log a one-line health summary plus details for non-healthy targets.

  Also purges stale events (older than _PURGE_DAYS) from memory and disk
  as a belt-and-suspenders check for long-running instances.
  """
  with _lock:
    # Periodic purge of stale events.
    if _purge_stale_events():
      _rewrite_log()

    if not _targets:
      return
    counts: dict[str, int] = {}
    problems: list[str] = []
    for name, state in sorted(_targets.items()):
      s = _compute_status(state)
      counts[s.value] = counts.get(s.value, 0) + 1
      if s in (Status.DEGRADED, Status.ERROR):
        scored = [e for e in state.events if e.event_type != EventType.LOCKED]
        total = len(scored)
        errors = sum(1 for e in scored if e.event_type == EventType.ERROR)
        ok = total - errors
        last_err = None
        for event in reversed(state.events):
          if event.event_type == EventType.ERROR:
            last_err = event.error_message
            break
        problems.append(f'  {s.value}: {name} ({ok}/{total} ok, last error: "{last_err}")')
      elif s == Status.UNKNOWN:
        age_min = round((time.time() - state.registered_at) / 60)
        problems.append(f'  unknown: {name} (registered {age_min}m ago, no events yet)')

  total_count = sum(counts.values())
  parts = [f'{v} {k}' for k, v in counts.items()]
  logger.info('Health: %d targets — %s', total_count, ', '.join(parts))
  for line in problems:
    logger.info(line)


def start_periodic_log() -> None:
  """Start a repeating background timer that logs health summaries."""
  global _log_timer

  def _tick() -> None:
    global _log_timer
    _log_summary()
    _log_timer = threading.Timer(_LOG_INTERVAL, _tick)
    _log_timer.daemon = True
    _log_timer.start()

  _log_timer = threading.Timer(_LOG_INTERVAL, _tick)
  _log_timer.daemon = True
  _log_timer.start()


def stop_periodic_log() -> None:
  """Cancel the periodic log timer (for clean shutdown in tests)."""
  global _log_timer
  if _log_timer is not None:
    _log_timer.cancel()
    _log_timer = None


def reset() -> None:
  """Clear all state (for tests)."""
  global _started_at, _log_timer
  with _lock:
    _targets.clear()
    _started_at = 0.0
  stop_periodic_log()
