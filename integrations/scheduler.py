# integrations/scheduler.py
#
# Webhook handler for scheduler control actions (quiet mode, public mode).
# Unlike other integrations, this module does not produce display content —
# it modifies scheduler behaviour via the quiet and public modules.

import logging
from typing import Any

import public
import quiet

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({'quiet', 'wake', 'set_public'})


def handle_webhook(
  payload: dict[str, Any],
  *,
  credential_name: str | None = None,
) -> None:
  """Handle a scheduler control webhook.

  Payload:
    {"action": "quiet"}                    — enable quiet mode
    {"action": "wake"}                     — disable quiet mode
    {"action": "set_public", "value": …}   — set public mode (bool)

  Returns None (no display message to enqueue).
  """
  action = payload.get('action', '')
  if action not in _VALID_ACTIONS:
    raise ValueError(f'Invalid scheduler action: {action!r} — expected one of {sorted(_VALID_ACTIONS)}')

  if action == 'quiet':
    quiet.activate()
  elif action == 'wake':
    quiet.deactivate()
  elif action == 'set_public':
    value = payload.get('value')
    if not isinstance(value, bool):
      raise ValueError(f'set_public requires a boolean "value" field, got {value!r}')
    public.set_public(value)

  return None
