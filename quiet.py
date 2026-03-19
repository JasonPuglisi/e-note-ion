# quiet.py
#
# Software-side quiet mode for the Vestaboard display. When active, the
# worker renders content normally but stores the result as virtual state
# instead of sending it to the board. On wake, the virtual state is sent
# immediately so the board shows contextually relevant content.
#
# State is persisted to [scheduler.quiet] in config.toml so quiet mode
# survives restarts (including Docker container recreates).
#
# Thread-safe: all state is behind a single lock.

import logging
import threading

import config as _config_mod

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active: bool = False
_virtual_state: list[list[int]] | None = None

# Set by activate() and deactivate() so the worker can detect transitions
# without polling. The worker should wait on this event (with a timeout) in
# its main loop.
_changed = threading.Event()


def init() -> None:
  """Load persisted quiet state from config.toml.

  Must be called after config.load_config() and before the worker starts.
  """
  global _active
  with _lock:
    _active = _config_mod.get_optional_bool('scheduler', 'quiet', default=False)
    if _active:
      logger.info('Quiet mode restored from config (board is quiet)')


def activate() -> None:
  """Enable quiet mode and persist to config.toml."""
  global _active
  with _lock:
    if _active:
      logger.debug('Quiet mode already active')
      return
    _active = True
    _config_mod.write_config_section('scheduler.quiet', {'active': True})
    logger.info('Quiet mode activated')
    _changed.set()


def deactivate() -> None:
  """Disable quiet mode and persist to config.toml.

  Virtual state is preserved for the worker to retrieve via
  pop_virtual_state() and send to the board.
  """
  global _active
  with _lock:
    if not _active:
      logger.debug('Quiet mode already inactive')
      return
    _active = False
    _config_mod.write_config_section('scheduler.quiet', {'active': False})
    logger.info('Quiet mode deactivated')
    _changed.set()


def is_active() -> bool:
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
