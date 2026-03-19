# integrations/scheduler.py
#
# Webhook handler for scheduler control actions (quiet mode).
# Unlike other integrations, this module does not produce display content —
# it modifies scheduler behaviour via the quiet module.

import logging
from typing import Any

import quiet

logger = logging.getLogger(__name__)

_VALID_ACTIONS = frozenset({'quiet', 'wake'})


def handle_webhook(
  payload: dict[str, Any],
  *,
  credential_name: str | None = None,
) -> None:
  """Handle a scheduler control webhook.

  Payload:
    {"action": "quiet"}  — enable quiet mode
    {"action": "wake"}   — disable quiet mode

  Returns None (no display message to enqueue).
  """
  action = payload.get('action', '')
  if action not in _VALID_ACTIONS:
    raise ValueError(f'Invalid scheduler action: {action!r} — expected one of {sorted(_VALID_ACTIONS)}')

  if action == 'quiet':
    quiet.activate()
  elif action == 'wake':
    quiet.deactivate()

  return None
