# integrations/plex.py
#
# Plex Media Server integration — dynamic now-playing display via webhook.
#
# Plex sends webhook events when playback starts, pauses, resumes, or stops.
# This integration translates those events into display messages:
#   - media.play / media.resume → "NOW PLAYING" with show/movie title
#   - media.pause               → "PAUSED" with show/movie title
#   - media.stop                → short "stopped" card ([R] NOW PLAYING, hold=60s)
#                                 debounced: if a play/resume arrives within the
#                                 stop_debounce window, the stopped card is suppressed
#                                 to avoid a flash between back-to-back episodes.
#
# Requires Plex Pass and a webhook configured in Plex Media Server settings
# to POST to the scheduler's webhook endpoint. See content/contrib/plex.md
# for setup instructions.
#
# No config.toml keys are required for the integration itself. To override
# hold/timeout/priority for the now_playing or paused templates, add a
# [plex.schedules.now_playing] or [plex.schedules.paused] section to
# config.toml — the same override syntax used for scheduled templates.
# To tune the stop debounce window, add [plex] stop_debounce = <seconds>.

import enum
import json
import logging
import threading
from pathlib import Path
from typing import Any

import integrations.media as _media
import integrations.vestaboard as _vb
from scheduler import WebhookMessage

logger = logging.getLogger(__name__)

_PLEX_JSON_PATH = Path(__file__).parent.parent / 'content' / 'contrib' / 'plex.json'

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

# Pending debounce timer for media.stop. When a stop event arrives we delay
# enqueueing the stopped card by _stop_debounce() seconds. If a play/resume
# arrives in that window we cancel the timer — suppressing the stopped card
# entirely to avoid a flash between back-to-back episodes.
_pending_stop_timer: threading.Timer | None = None


def _stop_debounce() -> int:
  """Return the stop debounce window in seconds from config (default 3)."""
  import config as _config_mod

  raw = _config_mod.get_optional('plex', 'stop_debounce')
  try:
    return max(0, int(raw)) if raw is not None else 3
  except ValueError:
    return 3


def _parse_tmdb_id_from_guids(guids: list[dict[str, Any]]) -> int | None:
  """Extract a TMDb ID from a Plex Metadata.Guid array, or None if absent."""
  for guid in guids:
    guid_id = guid.get('id', '')
    if guid_id.startswith('tmdb://'):
      try:
        return int(guid_id[len('tmdb://') :])
      except ValueError:
        pass
  return None


def _canonicalize_plex_title(raw_title: str, guids: list[dict[str, Any]], media_type: str) -> str:
  """Return the canonical title via TMDb if configured and Guid contains a tmdb:// entry.

  Falls back to raw_title if TMDb is unconfigured, the Guid array has no TMDb
  entry (e.g. older Plex Media Server), or the lookup fails.

  media_type must be 'show' or 'movie'.
  """
  import integrations.tmdb as _tmdb

  if not _tmdb.is_configured():
    return raw_title
  tmdb_id = _parse_tmdb_id_from_guids(guids)
  if tmdb_id is None:
    return raw_title
  canonical = _tmdb.get_show_title(tmdb_id) if media_type == 'show' else _tmdb.get_movie_title(tmdb_id)
  return canonical if canonical else raw_title


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


def handle_webhook(payload: dict[str, Any], credential_name: str | None = None) -> WebhookMessage | None:
  """Process a Plex webhook event and return a WebhookMessage or None.

  Enforces a state machine (IDLE → PLAYING ↔ PAUSED → IDLE):
  - play/resume: always valid; transition to PLAYING.
  - pause: only valid from PLAYING; ignored in IDLE or PAUSED.
  - stop: only valid from PLAYING or PAUSED; ignored in IDLE.

  For pause and stop, also checks whether Plex content is still on the board
  (via scheduler.current_hold_tag). If the board has moved on to other content,
  the state still transitions (reflecting reality) but no message is returned —
  avoiding stale events interrupting unrelated content.

  stop events are debounced: the stopped card is enqueued asynchronously after
  _stop_debounce() seconds. If a play/resume arrives in that window the timer
  is cancelled and the stopped card is suppressed entirely.

  Returns None for unrecognised events, invalid state transitions, non-video
  media, board displacement, or missing metadata.
  """
  global _state, _pending_stop_timer

  try:
    event = payload.get('event', '')
    if event not in _HANDLED_EVENTS:
      return None

    logger.debug('plex: %s (state=%s)', event, _state.value)

    try:
      import integrations.trakt as _trakt

      _trakt.clear_watching_state()
    except ImportError:
      pass

    # --- State machine transition and validity check ---

    if event in _PLAY_EVENTS:
      # Cancel any pending stop debounce — a follow-up play supersedes the stop.
      if _pending_stop_timer is not None:
        _pending_stop_timer.cancel()
        _pending_stop_timer = None
        logger.debug('plex: cancelled pending stop timer (play/resume arrived)')
      _state = _State.PLAYING
    elif event == _PAUSE_EVENT:
      if _state != _State.PLAYING:
        logger.debug('plex: discarding %s: state=%s, expected playing', event, _state.value)
        return None
      _state = _State.PAUSED
    elif event in _STOP_EVENTS:
      # Cancel any stale pending stop timer (defensive — shouldn't normally exist).
      if _pending_stop_timer is not None:
        _pending_stop_timer.cancel()
        _pending_stop_timer = None
      if _state == _State.IDLE:
        logger.debug('plex: discarding %s: state=idle', event)
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
        logger.debug('plex: discarding %s: board tag=%r, expected "plex"', event, hold_tag)
        return None

    # --- Build metadata ---

    metadata = payload.get('Metadata')
    media_type = metadata.get('type') if metadata else None

    if media_type == 'episode' and metadata:
      guids: list[dict[str, Any]] = metadata.get('Guid') or []
      raw_show = _canonicalize_plex_title(metadata['grandparentTitle'], guids, 'show')
      show_name = _vb.truncate_line(raw_show.upper(), _vb.model.cols, 'word')
      episode_ref = _media.format_episode_ref(metadata['parentIndex'], metadata['index'])
      episode_detail = _media.strip_leading_article((metadata.get('title') or '').upper())
      episode_line = f'{episode_ref} {episode_detail}'.strip()
    elif media_type == 'movie' and metadata:
      guids = metadata.get('Guid') or []
      raw_movie = _canonicalize_plex_title(metadata['title'], guids, 'movie')
      show_name = _vb.truncate_line(raw_movie.upper(), _vb.model.cols, 'word')
      episode_line = ''
    else:
      logger.debug('plex: no displayable metadata (type=%r)', media_type)
      show_name = ''
      episode_line = ''

    if event in _STOP_EVENTS:
      cfg = _load_template_config('stopped')
      has_media = bool(show_name)
      stop_data = {
        'templates': cfg['templates'],
        'variables': {'show_name': [[show_name]], 'episode_line': [[episode_line]]} if has_media else {},
        'truncation': cfg['truncation'],
      }
      priority = cfg['priority']
      hold = cfg['hold']
      timeout = cfg['timeout']
      debounce = _stop_debounce()

      def _enqueue_stopped() -> None:
        global _pending_stop_timer
        _pending_stop_timer = None
        if _state != _State.IDLE:
          logger.debug('plex: stop timer fired but state=%s, skipping', _state.value)
          return
        import scheduler as _sched

        hold_tag = _sched.current_hold_tag()
        if hold_tag != 'plex':
          logger.debug('plex: stop timer fired but board tag=%r, skipping', hold_tag)
          return
        logger.debug('plex: stop debounce elapsed, enqueueing stopped (has_media=%s)', has_media)
        _sched.enqueue(
          priority=priority,
          data=stop_data,
          hold=hold,
          timeout=timeout,
          name='webhook.plex',
          supersede_tag='plex',
        )
        _sched.fire_hold_interrupt(supersede_tag='plex')

      logger.debug('plex: stop debounce started (%ds)', debounce)
      _pending_stop_timer = threading.Timer(debounce, _enqueue_stopped)
      _pending_stop_timer.daemon = True
      _pending_stop_timer.start()
      return None

    if not show_name:
      logger.debug('plex: discarding %s: no show_name (media_type=%r)', event, media_type)
      return None

    template_name = 'paused' if event == _PAUSE_EVENT else 'now_playing'
    logger.debug('plex: enqueueing %s: %r (credential=%r)', template_name, show_name, credential_name)
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
    logger.error('Plex webhook error: %s', e)
    return None
