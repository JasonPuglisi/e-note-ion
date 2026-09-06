# integrations/scheduler.py
#
# Webhook handler for scheduler control actions (quiet mode, public mode).
# Unlike other integrations, this module does not produce display content —
# it modifies scheduler behaviour via the quiet and public modules.

import logging
from typing import Any

import public as _public_mod
import quiet as _quiet_mod

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
    _quiet_mod.set_quiet(True)
  elif action == 'wake':
    _quiet_mod.set_quiet(False)
  elif action == 'public':
    _public_mod.set_public(True)
  elif action == 'private':
    _public_mod.set_public(False)

  return None
