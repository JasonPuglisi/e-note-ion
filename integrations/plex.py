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

import enum
import json
from datetime import datetime
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


class _State(enum.Enum):
  IDLE = 'idle'
  PLAYING = 'playing'
  PAUSED = 'paused'


# Tracks the current Plex playback state. play/resume always transition to
# PLAYING. pause is only valid from PLAYING. stop is valid from PLAYING or
# PAUSED. Invalid transitions return None without firing any display update.
_state: _State = _State.IDLE


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

  Enforces a state machine (IDLE → PLAYING ↔ PAUSED → IDLE):
  - play/resume: always valid; transition to PLAYING.
  - pause: only valid from PLAYING; ignored in IDLE or PAUSED.
  - stop: only valid from PLAYING or PAUSED; ignored in IDLE.

  For pause and stop, also checks whether Plex content is still on the board
  (via scheduler.current_hold_tag). If the board has moved on to other content,
  the state still transitions (reflecting reality) but no message is returned —
  avoiding stale events interrupting unrelated content.

  Returns None for unrecognised events, invalid state transitions, non-video
  media, board displacement, or missing metadata.
  """
  global _state

  def _log(msg: str) -> None:
    print(f'[{datetime.now().strftime("%H:%M:%S")}] [plex] {msg}')

  try:
    event = payload.get('event', '')
    if event not in _HANDLED_EVENTS:
      return None

    _log(f'{event} (state={_state.value})')

    try:
      import integrations.trakt as _trakt

      _trakt.clear_watching_state()
    except ImportError:
      pass

    # --- State machine transition and validity check ---

    if event in _PLAY_EVENTS:
      _state = _State.PLAYING
    elif event == _PAUSE_EVENT:
      if _state != _State.PLAYING:
        _log(f'discarding {event}: state={_state.value}, expected playing')
        return None
      _state = _State.PAUSED
    elif event in _STOP_EVENTS:
      if _state == _State.IDLE:
        _log(f'discarding {event}: state=idle')
        return None
      _state = _State.IDLE

    # --- Board displacement check (pause and stop only) ---
    # play/resume always fires — it initiates a new session regardless of what
    # is currently on the board. pause and stop are only meaningful if Plex
    # content is still showing; if the board has moved on, suppress the message
    # (state has already transitioned above to reflect reality).

    if event not in _PLAY_EVENTS:
      import scheduler as _sched

      hold_tag = _sched.current_hold_tag()
      if hold_tag != 'plex':
        _log(f'discarding {event}: board tag={hold_tag!r}, expected "plex"')
        return None

    # --- Build metadata ---

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
      _log(f'no displayable metadata (type={media_type!r})')
      show_name = ''
      episode_line = ''

    if event in _STOP_EVENTS:
      cfg = _load_template_config('stopped')
      has_media = bool(show_name)
      _log(f'enqueueing stopped (has_media={has_media})')
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
      _log(f'discarding {event}: no show_name (media_type={media_type!r})')
      return None

    template_name = 'paused' if event == _PAUSE_EVENT else 'now_playing'
    _log(f'enqueueing {template_name}: {show_name!r}')
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
