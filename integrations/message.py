# integrations/message.py
#
# Friend message integration — displays ad-hoc messages from registered friends.
#
# Friends send messages via iOS Shortcuts or any HTTP client. Each friend has a
# unique per-scoped secret registered by the board owner using the main webhook
# secret. Messages are identified by their credential name and displayed with
# the friend's configured name and color.
#
# Two actions are handled:
#   message  (default) — post a message; requires a registered friend credential
#   register           — register a new friend; requires the main webhook secret
#
# See content/contrib/message.md for setup instructions and iOS Shortcut templates.

import json
import logging
import re
from pathlib import Path
from typing import Any

from scheduler import WebhookMessage

logger = logging.getLogger(__name__)

_MESSAGE_JSON_PATH = Path(__file__).parent.parent / 'content' / 'contrib' / 'message.json'

# Maps color config key → Vestaboard display character (each = 1 display column).
_COLOR_MAP: dict[str, str] = {
  'R': '[R]',
  'O': '[O]',
  'Y': '[Y]',
  'G': '[G]',
  'B': '[B]',
  'V': '[V]',
  'W': '[W]',
  'K': '[K]',
  'H': '❤️',
}

# Maps full color names → config key (for Shortcut convenience; single-letter codes also accepted).
_COLOR_NAMES: dict[str, str] = {
  'red': 'R',
  'orange': 'O',
  'yellow': 'Y',
  'green': 'G',
  'blue': 'B',
  'violet': 'V',
  'white': 'W',
  'black': 'K',
  'heart': 'H',
}

# Max characters for the friend name in the header row per model.
# Header: {color}(1) + ' '(1) + 'FROM '(5) = 7 cols used; remainder is for name.
# Note: 15 cols → 8 for name. Flagship: 22 cols → 15 for name.
_MAX_NAME_COLS: dict[str, int] = {'note': 8, 'flagship': 15}


def _load_template_config(template_name: str) -> dict[str, Any]:
  """Return effective config for a webhook-only template from message.json."""
  import config as _config_mod

  with open(_MESSAGE_JSON_PATH) as f:
    content = json.load(f)
  template = content['templates'][template_name]
  schedule = template['schedule']

  effective: dict[str, Any] = {
    'hold': schedule['hold'],
    'timeout': schedule['timeout'],
    'priority': template['priority'],
    'truncation': template.get('truncation', 'hard'),
  }

  override = _config_mod.get_schedule_override(f'message.{template_name}')
  for field in ('hold', 'timeout'):
    val = override.get(field)
    if isinstance(val, int) and val >= 0:
      effective[field] = val
  priority_val = override.get('priority')
  if isinstance(priority_val, int) and 0 <= priority_val <= 10:
    effective['priority'] = priority_val

  return effective


def _build_header(friend: dict[str, Any], credential_name: str, model: str) -> str:
  """Build the header row for a friend message.

  Format: '{color} FROM {NAME}' where the name is hard-capped to fit row 1.
  The color character always occupies exactly 1 display column.
  """
  color_key = str(friend.get('color', 'W')).upper()
  color_char = _COLOR_MAP.get(color_key, '[W]')
  max_cols = _MAX_NAME_COLS.get(model, _MAX_NAME_COLS['note'])
  return f'{color_char} FROM {credential_name.upper()[:max_cols]}'


def handle_webhook(
  payload: dict[str, Any],
  credential_name: str | None = None,
) -> WebhookMessage | None:
  """Process a friend message or registration webhook payload.

  For message posting: credential_name must identify a registered friend with a
  [message.friends.<name>] config entry. Anonymous and admin posts are rejected.

  For registration ('action': 'register'): only the admin (main webhook secret,
  credential_name == '') may register friends. Returns None always for registration
  (no display message; the admin Shortcut presents the secret to the user directly).

  Returns a WebhookMessage for message posts, None for registration or on any error.
  """
  try:
    action = str(payload.get('action', 'message')).lower()

    if action == 'register':
      return _handle_register(payload, credential_name)

    # Message posting — requires a registered friend credential.
    # Explicitly reject the admin (empty string) and unauthenticated (None) callers.
    if not credential_name:
      logger.debug('message: discarding: no credential or admin secret used')
      return None

    import config as _config_mod

    friend = _config_mod.get_message_friend(credential_name)
    if friend is None:
      logger.debug('message: discarding: credential %r not registered as a message friend', credential_name)
      return None

    message_text = str(payload.get('message', '')).strip()
    message_lines = [line.strip().upper() for line in message_text.split('\n') if line.strip()]
    if not message_lines:
      logger.debug('message: discarding: empty or missing message')
      return None

    model = _config_mod.get_optional('scheduler', 'model', 'note')
    header = _build_header(friend, credential_name, model)
    cfg = _load_template_config('notification')

    logger.debug('message: enqueueing from %r', credential_name)
    return WebhookMessage(
      data={
        'templates': [{'format': [header, '{message}']}],
        'variables': {'message': [message_lines]},
        'truncation': cfg['truncation'],
      },
      priority=cfg['priority'],
      hold=cfg['hold'],
      timeout=cfg['timeout'],
      supersede_tag=f'message.{credential_name}',
    )
  except Exception as e:  # noqa: BLE001
    logger.error('Message webhook error: %s', e)
    return None


def _handle_register(
  payload: dict[str, Any],
  credential_name: str | None,
) -> None:
  """Register a new friend credential. Only the admin (main webhook secret) may call this.

  Expects payload fields:
    name         (str, required): friend's name; spaces→hyphens, lowercased; shown on board (uppercased)
    color        (str, optional): color name (Red, Orange, …, Heart) or code (R O Y G B V W K H); defaults to White
    passphrase   (str, required): plaintext passphrase; hashed with argon2id before storing

  Writes [webhook.credentials.<name>] and [message.friends.<name>] to config.toml.
  Re-registering an existing name overwrites the previous entry.
  """
  from argon2 import PasswordHasher

  import config as _config_mod

  # Only admin (credential_name == '') can register.
  if credential_name != '':
    logger.warning('message: registration rejected: must use main webhook secret')
    return None

  name = re.sub(r'\s+', '-', str(payload.get('name', '')).strip().lower())
  if not name or not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
    logger.warning('message: registration rejected: invalid or missing name %r', name)
    return None

  color_raw = str(payload.get('color', 'W')).strip()
  color = _COLOR_NAMES.get(color_raw.lower(), color_raw.upper())
  if color not in _COLOR_MAP:
    logger.warning('message: registration rejected: invalid color %r', color)
    return None

  passphrase = str(payload.get('passphrase', '')).strip()
  if len(passphrase) < 8:
    logger.warning('message: registration rejected: passphrase too short (min 8 chars)')
    return None

  ph = PasswordHasher()
  secret_hash = ph.hash(passphrase)

  _config_mod.write_config_section(
    f'webhook.credentials.{name}',
    {'secret_hash': secret_hash, 'webhooks': ['message']},
  )
  _config_mod.write_config_section(
    f'message.friends.{name}',
    {'color': color},
  )

  logger.info('message: registered friend %r (color=%r)', name, color)
  return None
