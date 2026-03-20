# public.py
#
# Runtime public mode state for the Vestaboard display. When active, the
# worker skips templates marked private, hiding personal content when the
# display is in a guest-visible space.
#
# State is persisted to [scheduler].public in config.toml so it survives
# restarts. Thread-safe: all state is behind a single lock.

import logging
import threading

import config as _config_mod

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_public: bool = False


def init() -> None:
  """Load persisted public mode state from config.toml.

  Must be called after config.load_config() and before the worker starts.
  """
  global _public
  with _lock:
    _public = _config_mod.get_public_mode()
    if _public:
      logger.info('Public mode restored from config (private content hidden)')


def set_public(value: bool) -> None:
  """Set public mode and persist to config.toml."""
  global _public
  with _lock:
    if _public == value:
      logger.debug('Public mode already %s', 'active' if value else 'inactive')
      return
    _public = value
    _config_mod.write_config_section('scheduler', {'public': value})
    logger.info('Public mode %s', 'activated' if value else 'deactivated')


def is_public() -> bool:
  """Return whether public mode is currently active."""
  with _lock:
    return _public
