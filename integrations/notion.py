# integrations/notion.py
#
# Notion webhook integration — displays automation-triggered notifications.
#
# Notion automations send an HTTP POST to the webhook endpoint with a
# structured payload. This integration parses the payload and formats a
# display message with a static "[W] FROM NOTION" header row.
#
# No config.toml keys are required for the integration itself. To override
# hold/timeout/priority for the notification template, add a
# [notion.schedules.notification] section to config.toml — the same override
# syntax used for scheduled templates.

import json
import logging
from pathlib import Path
from typing import Any

from scheduler import WebhookMessage

logger = logging.getLogger(__name__)

_NOTION_JSON_PATH = Path(__file__).parent.parent / 'content' / 'contrib' / 'notion.json'


def _load_template_config(template_name: str) -> dict[str, Any]:
  """Return effective config for a webhook-only template from notion.json.

  Applies any [notion.schedules.<template_name>] overrides from config.toml
  on top of the JSON defaults, matching the behaviour of scheduled templates.
  """
  import config as _config_mod

  with open(_NOTION_JSON_PATH) as f:
    content = json.load(f)
  template = content['templates'][template_name]
  schedule = template['schedule']

  effective: dict[str, Any] = {
    'hold': schedule['hold'],
    'timeout': schedule['timeout'],
    'priority': template['priority'],
    'truncation': template.get('truncation', 'hard'),
    'templates': template.get('templates', []),
  }

  override = _config_mod.get_schedule_override(f'notion.{template_name}')
  for field in ('hold', 'timeout'):
    val = override.get(field)
    if isinstance(val, int) and val >= 0:
      effective[field] = val
  priority_val = override.get('priority')
  if isinstance(priority_val, int) and 0 <= priority_val <= 10:
    effective['priority'] = priority_val

  return effective


def handle_webhook(payload: dict[str, Any]) -> WebhookMessage | None:
  """Process a Notion webhook payload and return a WebhookMessage or None.

  Expected payload fields:
    message (str, required): body text; newlines produce multiple display lines.
    urgent  (bool, optional, default false): if true, interrupt the current hold.
    tag     (str, optional, default "notion"): deduplication key — queued messages
            with the same namespaced tag are replaced by this one. Set to "" to
            disable superseding entirely.

  Returns None if message is missing or blank, or on any unexpected error.
  """
  try:
    message = payload.get('message', '')
    if not isinstance(message, str):
      message = ''
    message_lines = [line.strip().upper() for line in message.split('\n') if line.strip()]
    if not message_lines:
      logger.debug('notion: discarding: empty or missing message')
      return None

    urgent = bool(payload.get('urgent', False))

    raw_tag = payload.get('tag')
    if raw_tag is None:
      supersede_tag = 'notion'
    elif isinstance(raw_tag, str):
      supersede_tag = f'notion.{raw_tag}' if raw_tag else ''
    else:
      logger.debug('notion: invalid tag type %r, using default', type(raw_tag).__name__)
      supersede_tag = 'notion'

    cfg = _load_template_config('notification')

    logger.debug('notion: enqueueing notification (urgent=%s, tag=%r)', urgent, supersede_tag)
    return WebhookMessage(
      data={
        'templates': cfg['templates'],
        'variables': {'message': [message_lines]},
        'truncation': cfg['truncation'],
      },
      priority=cfg['priority'],
      hold=cfg['hold'],
      timeout=cfg['timeout'],
      interrupt=urgent,
      supersede_tag=supersede_tag,
    )
  except Exception as e:  # noqa: BLE001
    logger.error('Notion webhook error: %s', e)
    return None
