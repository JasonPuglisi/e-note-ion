# integrations/plex.py
#
# Plex Media Server integration — dynamic now-playing display via webhook.
#
# Plex sends webhook events when playback starts, pauses, resumes, or stops.
# This integration translates those events into display messages:
#   - media.play / media.resume → "NOW PLAYING" with show/movie title
#   - media.pause               → "PAUSED" with show/movie title
#   - media.stop                → short "stopped" card ([R] NOW PLAYING, hold=60s)
#
# Requires Plex Pass and a webhook configured in Plex Media Server settings
# to POST to the scheduler's webhook endpoint. See content/contrib/plex.md
# for setup instructions.
#
# No config.toml keys are required for the integration itself. To override
# hold/timeout/priority for the now_playing or paused templates, add a
# [plex.schedules.now_playing] or [plex.schedules.paused] section to
# config.toml — the same override syntax used for scheduled templates.

import json
from pathlib import Path
from typing import Any

import integrations.vestaboard as _vb
from scheduler import WebhookMessage

_PLEX_JSON_PATH = Path(__file__).parent.parent / 'content' / 'contrib' / 'plex.json'

_LEADING_ARTICLES = ('THE ', 'AN ', 'A ')

# Events that trigger playback display.
_PLAY_EVENTS = frozenset({'media.play', 'media.resume'})
_PAUSE_EVENT = 'media.pause'
_STOP_EVENTS = frozenset({'media.stop'})

# All events this integration handles; others are silently discarded.
_HANDLED_EVENTS = _PLAY_EVENTS | {_PAUSE_EVENT} | _STOP_EVENTS


def _strip_leading_article(title: str) -> str:
  """Remove a leading article (A, An, The) from an uppercased title."""
  for article in _LEADING_ARTICLES:
    if title.startswith(article):
      return title[len(article) :]
  return title


def _load_template_config(template_name: str) -> dict[str, Any]:
  """Return effective config for a webhook-only template from plex.json.

  Applies any [plex.schedules.<template_name>] overrides from config.toml
  on top of the JSON defaults, matching the behaviour of scheduled templates.
  """
  import config as _config_mod

  with open(_PLEX_JSON_PATH) as f:
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

  override = _config_mod.get_schedule_override(f'plex.{template_name}')
  for field in ('hold', 'timeout'):
    val = override.get(field)
    if isinstance(val, int) and val >= 0:
      effective[field] = val
  priority_val = override.get('priority')
  if isinstance(priority_val, int) and 0 <= priority_val <= 10:
    effective['priority'] = priority_val

  return effective


def handle_webhook(payload: dict[str, Any]) -> WebhookMessage | None:
  """Process a Plex webhook event and return a WebhookMessage or None.

  Returns None for unrecognised events, non-video media types, or missing
  metadata. Errors are logged and return None rather than propagating.
  """
  try:
    event = payload.get('event', '')
    if event not in _HANDLED_EVENTS:
      return None

    try:
      import integrations.trakt as _trakt

      _trakt.clear_watching_state()
    except ImportError:
      pass

    metadata = payload.get('Metadata')
    media_type = metadata.get('type') if metadata else None

    if media_type == 'episode' and metadata:
      show_name = _vb.truncate_line(metadata['grandparentTitle'].upper(), _vb.model.cols, 'word')
      episode_ref = f'S{metadata["parentIndex"]}E{metadata["index"]}'
      episode_detail = _strip_leading_article((metadata.get('title') or '').upper())
      episode_line = f'{episode_ref} {episode_detail}'.strip()
    elif media_type == 'movie' and metadata:
      show_name = _vb.truncate_line(metadata['title'].upper(), _vb.model.cols, 'word')
      episode_line = ''
    else:
      show_name = ''
      episode_line = ''

    if event in _STOP_EVENTS:
      cfg = _load_template_config('stopped')
      has_media = bool(show_name)
      return WebhookMessage(
        data={
          'templates': cfg['templates'],
          'variables': {'show_name': [[show_name]], 'episode_line': [[episode_line]]} if has_media else {},
          'truncation': cfg['truncation'],
        },
        priority=cfg['priority'],
        hold=cfg['hold'],
        timeout=cfg['timeout'],
        interrupt=True,
        supersede_tag='plex',
      )

    if not show_name:
      return None

    template_name = 'paused' if event == _PAUSE_EVENT else 'now_playing'
    cfg = _load_template_config(template_name)

    return WebhookMessage(
      data={
        'templates': cfg['templates'],
        'variables': {
          'show_name': [[show_name]],
          'episode_line': [[episode_line]],
        },
        'truncation': cfg['truncation'],
      },
      priority=cfg['priority'],
      hold=cfg['hold'],
      timeout=cfg['timeout'],
      indefinite=True,
      interrupt=True,
      supersede_tag='plex',
    )
  except Exception as e:  # noqa: BLE001
    print(f'Plex webhook error: {e}')
    return None
