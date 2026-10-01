# integrations/scheduler.py
#
# Webhook handler for scheduler control actions (quiet mode, public mode).
# Unlike other integrations, this module does not produce display content —
# it modifies scheduler behaviour via the quiet and public modules.

import logging
import math
from typing import Any

import public as _public_mod
import quiet as _quiet_mod

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({'quiet', 'wake', 'public', 'private'})


def _parse_delay(raw: Any) -> float:
  """Validate a wake delay in seconds.

  Accepts a JSON number or a numeric string (iOS Shortcuts makes it easy to
  send the Text type by mistake). Rejects booleans, which are ints in Python.
  The raw value is never echoed back — it is untrusted input.
  """
  value: float | None = None
  if isinstance(raw, str):
    try:
      value = float(raw.strip())
    except ValueError:
      value = None
  elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
    value = float(raw)
  if value is None or not math.isfinite(value) or not 0 <= value <= _quiet_mod.MAX_WAKE_DELAY:
    raise ValueError(f'Invalid wake delay — expected a number of seconds from 0 to {_quiet_mod.MAX_WAKE_DELAY}')
  return value


def handle_webhook(
  payload: dict[str, Any],
  *,
  credential_name: str | None = None,
) -> None:
  """Handle a scheduler control webhook.

  Payload:
    {"action": "quiet"}               — enable quiet mode
    {"action": "wake"}                — disable quiet mode
    {"action": "wake", "delay": 300}  — disable quiet mode after 300 seconds
    {"action": "public"}              — enable public mode (hide private content)
    {"action": "private"}             — disable public mode (show all content)

  The most recent command wins: quiet or an immediate wake cancels a pending
  delayed wake, and a new delayed wake replaces it.

  Returns None (no display message to enqueue).
  """
  action = payload.get('action', '')
  if action not in _VALID_ACTIONS:
    raise ValueError(f'Invalid scheduler action: {action!r} — expected one of {sorted(_VALID_ACTIONS)}')

  delay = 0.0
  if 'delay' in payload:
    if action != 'wake':
      raise ValueError(f'delay is only supported for the wake action, not {action!r}')
    delay = _parse_delay(payload['delay'])

  if action == 'quiet':
    _quiet_mod.set_quiet(True)
  elif action == 'wake':
    if delay > 0:
      _quiet_mod.schedule_wake(delay)
    else:
      _quiet_mod.set_quiet(False)
  elif action == 'public':
    _public_mod.set_public(True)
  elif action == 'private':
    _public_mod.set_public(False)

  return None
