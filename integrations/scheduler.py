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

_VALID_ACTIONS = frozenset({'quiet', 'wake', 'public', 'private'})


def handle_webhook(
  payload: dict[str, Any],
  *,
  credential_name: str | None = None,
) -> None:
  """Handle a scheduler control webhook.

  Payload:
    {"action": "quiet"}    — enable quiet mode
    {"action": "wake"}     — disable quiet mode
    {"action": "public"}   — enable public mode (hide private content)
    {"action": "private"}  — disable public mode (show all content)

  Returns None (no display message to enqueue).
  """
  action = payload.get('action', '')
  if action not in _VALID_ACTIONS:
    raise ValueError(f'Invalid scheduler action: {action!r} — expected one of {sorted(_VALID_ACTIONS)}')

  if action == 'quiet':
    quiet.activate()
  elif action == 'wake':
    quiet.deactivate()
  elif action == 'public':
    public.set_public(True)
  elif action == 'private':
    public.set_public(False)

  return None
