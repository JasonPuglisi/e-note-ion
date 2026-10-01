# quiet.py
#
# Software-side quiet mode for the Vestaboard display. When active, the
# worker renders content normally but stores the result as virtual state
# instead of sending it to the board. On wake, the virtual state is sent
# immediately so the board shows contextually relevant content.
#
# State is persisted to [scheduler].quiet in config.toml so quiet mode
# survives restarts (including Docker container recreates).
#
# A wake can also be deferred (schedule_wake) so the board stays quiet for a
# few minutes after e.g. a morning alarm. The pending wake is persisted to
# data/quiet.json so it survives a restart inside the delay window. The most
# recent command wins: any set_quiet() call cancels a pending wake, and a new
# schedule_wake() replaces it. A pending wake only ever exists while quiet.
#
# Thread-safe: all state is behind a single lock.

import json
import logging
import math
import os
import threading
import time
from pathlib import Path

import config as _config_mod
import homebridge as _homebridge_mod

logger = logging.getLogger(__name__)

# Upper bound on a deferred wake, so a typo cannot leave the board dark all day.
MAX_WAKE_DELAY = 3600

# Runtime state directory (Docker VOLUME /app/data), shared with health.py.
_DATA_DIR = Path('data')
_WAKE_PATH = _DATA_DIR / 'quiet.json'

_lock = threading.Lock()
_active: bool = False
_virtual_state: list[list[int]] | None = None

# Pending deferred wake. _wake_generation increments on every arm so a timer
# callback that was already running when it got cancelled or replaced (and is
# blocked on _lock) can tell it is stale — Timer.cancel() cannot stop it.
_pending_wake: threading.Timer | None = None
_pending_wake_at: float | None = None
_wake_generation: int = 0

# Set by set_quiet() so the worker can detect transitions without polling.
# The worker should wait on this event (with a timeout) in its main loop.
_changed = threading.Event()


def init() -> None:
  """Load persisted quiet state from config.toml and any pending wake.

  Must be called after config.load_config() and before the worker starts.
  A pending wake whose deadline passed while the scheduler was stopped wakes
  the board immediately; one still in the future is re-armed.
  """
  global _active
  expired = False
  with _lock:
    _active = _config_mod.get_optional_bool('scheduler', 'quiet', default=False)
    if _active:
      logger.info('Quiet mode restored from config (board is quiet)')
    wake_at = _read_wake_at()
    if wake_at is not None:
      if not _active:
        # Quiet was cleared by hand (or the wake landed just before the file
        # was removed); nothing left to wake.
        logger.info('Discarding persisted pending wake — quiet mode is not active')
        _remove_wake_file()
      else:
        remaining = wake_at - time.time()
        if remaining <= 0:
          expired = True
        else:
          # Clamp in case the wall clock moved backwards while stopped.
          _arm_wake_locked(min(remaining, MAX_WAKE_DELAY))
          logger.info('Pending wake restored (in %ds)', round(remaining))
  if expired:
    logger.info('Pending wake came due while stopped — waking now')
    set_quiet(False)
    # No timer was armed, so set_quiet() had nothing to cancel. Removed only
    # after the wake is persisted, for the same reason as _fire_pending_wake.
    # Safe outside the lock above: init() runs before the webhook server starts.
    with _lock:
      _remove_wake_file()


def set_quiet(value: bool) -> None:
  """Set quiet mode and persist to config.toml.

  Always cancels a pending deferred wake, even when the mode is unchanged:
  quiet while already quiet means "keep sleeping", which must not leave the
  earlier wake armed.

  When transitioning to inactive, virtual state is preserved for the worker
  to retrieve via pop_virtual_state() and send to the board.
  """
  with _lock:
    if _cancel_wake_locked():
      logger.info('Pending wake cancelled by %s', 'quiet' if value else 'immediate wake')
    changed = _set_active_locked(value)
  # Notify HomeBridge outside the lock (network I/O) and only on a real
  # transition (not the no-op case).
  if changed:
    _homebridge_mod.notify_mode_change('quiet', value)


def schedule_wake(delay: float) -> None:
  """Wake the board after *delay* seconds, replacing any pending wake.

  No-op when quiet mode is not active — the board is already awake.
  """
  if not 0 < delay <= MAX_WAKE_DELAY:
    raise ValueError(f'wake delay must be in (0, {MAX_WAKE_DELAY}] seconds')
  with _lock:
    if not _active:
      logger.debug('Delayed wake ignored — quiet mode is not active')
      return
    replaced = _cancel_wake_locked()
    _arm_wake_locked(delay)
    _write_wake_file(time.time() + delay)
  logger.info('Wake scheduled in %ds%s', round(delay), ' (replaced pending wake)' if replaced else '')


def cancel_pending_wake() -> bool:
  """Cancel a pending deferred wake without changing quiet mode.

  Returns whether one was pending.
  """
  with _lock:
    return _cancel_wake_locked()


def pending_wake_at() -> float | None:
  """Return the epoch time of the pending wake, or None."""
  with _lock:
    return _pending_wake_at


def _set_active_locked(value: bool) -> bool:
  """Apply a quiet transition. Caller holds _lock. Returns whether it changed."""
  global _active
  if _active == value:
    logger.debug('Quiet mode already %s', 'active' if value else 'inactive')
    return False
  _active = value
  _config_mod.write_config_section('scheduler', {'quiet': value})
  logger.info('Quiet mode %s', 'activated' if value else 'deactivated')
  _changed.set()
  return True


def _arm_wake_locked(delay: float) -> None:
  """Start the wake timer. Caller holds _lock and has cancelled any prior one."""
  global _pending_wake, _pending_wake_at, _wake_generation
  _wake_generation += 1
  timer = threading.Timer(delay, _fire_pending_wake, args=(_wake_generation,))
  timer.daemon = True
  _pending_wake = timer
  _pending_wake_at = time.time() + delay
  timer.start()


def _cancel_wake_locked() -> bool:
  """Cancel the pending wake, if any. Caller holds _lock."""
  global _pending_wake, _pending_wake_at
  if _pending_wake is None:
    return False
  _pending_wake.cancel()
  _pending_wake = None
  _pending_wake_at = None
  _remove_wake_file()
  return True


def _fire_pending_wake(generation: int) -> None:
  """Timer callback: wake the board if this timer is still the pending one."""
  global _pending_wake, _pending_wake_at
  try:
    with _lock:
      if _pending_wake is None or generation != _wake_generation:
        return
      _pending_wake = None
      _pending_wake_at = None
      logger.info('Delayed wake firing')
      changed = _set_active_locked(False)
      # Removed after the transition: if persisting quiet=false fails, the
      # file survives and the next startup still wakes the board.
      _remove_wake_file()
    if changed:
      _homebridge_mod.notify_mode_change('quiet', False)
  except Exception:
    logger.exception('Delayed wake failed')


def _read_wake_at() -> float | None:
  """Return the persisted wake deadline, or None. Caller holds _lock."""
  try:
    raw = _WAKE_PATH.read_text()
  except FileNotFoundError:
    return None
  except OSError as e:
    logger.warning('Quiet: could not read %s — %s', _WAKE_PATH, e)
    return None
  try:
    wake_at = json.loads(raw)['wake_at']
    if isinstance(wake_at, bool) or not isinstance(wake_at, (int, float)) or not math.isfinite(wake_at):
      raise ValueError('wake_at is not a finite number')
  except (ValueError, KeyError, TypeError) as e:
    logger.warning('Quiet: ignoring malformed %s — %s', _WAKE_PATH, e)
    _remove_wake_file()
    return None
  return float(wake_at)


def _write_wake_file(wake_at: float) -> None:
  """Persist the wake deadline atomically. Caller holds _lock.

  Best-effort: on failure the in-memory timer still fires, the wake just
  does not survive a restart.
  """
  tmp = _WAKE_PATH.with_suffix('.json.tmp')
  try:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps({'wake_at': wake_at}))
    os.replace(tmp, _WAKE_PATH)
  except OSError as e:
    logger.warning('Quiet: could not write %s — %s', _WAKE_PATH, e)


def _remove_wake_file() -> None:
  """Delete the persisted wake deadline. Caller holds _lock."""
  try:
    _WAKE_PATH.unlink(missing_ok=True)
  except OSError as e:
    logger.warning('Quiet: could not remove %s — %s', _WAKE_PATH, e)


def is_quiet() -> bool:
  """Return whether quiet mode is currently active."""
  with _lock:
    return _active


def set_virtual_state(characters: list[list[int]]) -> None:
  """Store rendered character codes as the virtual board state."""
  global _virtual_state
  with _lock:
    _virtual_state = characters


def get_virtual_state() -> list[list[int]] | None:
  """Return the current virtual state without clearing it."""
  with _lock:
    return _virtual_state


def pop_virtual_state() -> list[list[int]] | None:
  """Return and clear the virtual state.

  Called by the worker on quiet→wake transition to send the virtual state
  to the real board.
  """
  global _virtual_state
  with _lock:
    state = _virtual_state
    _virtual_state = None
    return state


def changed_event() -> threading.Event:
  """Return the event that signals quiet state transitions."""
  return _changed
