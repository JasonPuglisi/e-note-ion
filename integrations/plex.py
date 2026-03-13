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


def _parse_guid_id(guids: list[dict[str, Any]], scheme: str) -> int | None:
  """Extract a numeric ID for the given scheme from a Plex Metadata.Guid array."""
  for guid in guids:
    guid_id = guid.get('id', '')
    if guid_id.startswith(scheme):
      try:
        return int(guid_id[len(scheme) :])
      except ValueError:
        pass
  return None


def _parse_guid_id_str(guids: list[dict[str, Any]], scheme: str) -> str | None:
  """Extract a string ID for the given scheme from a Plex Metadata.Guid array."""
  for guid in guids:
    guid_id = guid.get('id', '')
    if guid_id.startswith(scheme):
      value = guid_id[len(scheme) :]
      return value if value else None
  return None


def _canonicalize_plex_title(
  raw_title: str,
  guids: list[dict[str, Any]],
  media_type: str,
  season: int | None = None,
  episode: int | None = None,
) -> tuple[str, str | None]:
  """Return (canonical_title, tmdb_episode_title_or_None) from TMDb.

  For episodes, tries in order:
    1. tvdb:// guid (episode-level TVDb ID)
    2. imdb:// guid (episode-level IMDb ID)
    3. TMDb title search + S/E lookup (when season and episode are provided)

  Returns both the canonical show name and the TMDb episode title when
  resolved. The episode title is None for movies and when all lookups fail.

  For movies, the tmdb:// entry is the TMDb movie ID.
  Episode title is always None for movies.

  Falls back to (raw_title, None) when TMDb is unconfigured, the Guid array
  has no usable entry, or all lookups fail.

  media_type must be 'episode' or 'movie'.
  """
  import integrations.tmdb as _tmdb

  if not _tmdb.is_configured():
    return raw_title, None

  if media_type == 'episode':
    tvdb_id = _parse_guid_id(guids, 'tvdb://')
    if tvdb_id is not None:
      ep_result = _tmdb.find_episode_by_tvdb_id(tvdb_id)
      if ep_result is not None:
        _, _, ep_title, show_id, _ = ep_result
        canonical = _tmdb.get_show_title(show_id)
        if canonical:
          logger.debug('plex: tmdb canonical %r -> %r', raw_title, canonical)
        else:
          logger.debug('plex: tmdb show lookup failed for id=%d, using raw title %r', show_id, raw_title)
        return canonical if canonical else raw_title, ep_title or None
      logger.debug('plex: tvdb ep lookup failed for id=%d, using raw title %r', tvdb_id, raw_title)
      return raw_title, None
    # No tvdb:// guid — fall back to imdb://
    logger.debug('plex: no tvdb:// guid for episode %r, trying imdb://', raw_title)
    imdb_id = _parse_guid_id_str(guids, 'imdb://')
    if imdb_id:
      ep_result = _tmdb.find_episode_by_imdb_id(imdb_id)
      if ep_result is not None:
        _, _, ep_title, show_id, _ = ep_result
        canonical = _tmdb.get_show_title(show_id)
        if canonical:
          logger.debug('plex: tmdb canonical (via imdb) %r -> %r', raw_title, canonical)
        else:
          logger.debug('plex: tmdb imdb lookup show %d title failed, using raw title %r', show_id, raw_title)
        return canonical if canonical else raw_title, ep_title or None
      logger.debug('plex: imdb ep lookup failed for %r, using raw title %r', imdb_id, raw_title)
    else:
      logger.debug('plex: no imdb:// guid for episode %r, trying title search', raw_title)
    # Last resort: search TMDb by show title + S/E numbers
    if season is not None and episode is not None:
      show_id = _tmdb.search_show_by_title(raw_title)
      if show_id is not None:
        ep_result_se = _tmdb.get_episode_by_number(show_id, season, episode)
        if ep_result_se is not None:
          ep_title, tmdb_ep_id = ep_result_se
          group_pos = _tmdb.get_episode_group_position(show_id, tmdb_ep_id)
          if group_pos:
            season, episode = group_pos
          canonical = _tmdb.get_show_title(show_id)
          logger.debug(
            'plex: tmdb title-search %r -> show=%d S%dE%d %r',
            raw_title,
            show_id,
            season,
            episode,
            ep_title,
          )
          return canonical if canonical else raw_title, ep_title or None
      logger.debug('plex: title search for %r found nothing', raw_title)
    return raw_title, None

  else:  # movie
    tmdb_id = _parse_guid_id(guids, 'tmdb://')
    if tmdb_id is None:
      logger.debug('plex: no tmdb:// guid for %r, using raw title', raw_title)
      return raw_title, None
    canonical = _tmdb.get_movie_title(tmdb_id)
    if canonical:
      logger.debug('plex: tmdb canonical %r -> %r', raw_title, canonical)
    else:
      logger.debug('plex: tmdb lookup failed, using raw title %r', raw_title)
    return canonical if canonical else raw_title, None


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
      logger.debug(
        'plex: episode guids=%r guid=%r grandparentGuid=%r',
        guids,
        metadata.get('guid'),
        metadata.get('grandparentGuid'),
      )
      raw_show, tmdb_ep_title = _canonicalize_plex_title(
        metadata['grandparentTitle'],
        guids,
        'episode',
        season=metadata.get('parentIndex'),
        episode=metadata.get('index'),
      )
      show_name = _vb.truncate_line(raw_show.upper(), _vb.model.cols, 'ellipsis')
      episode_ref = _media.format_episode_ref(metadata['parentIndex'], metadata['index'])
      plex_ep_title = (metadata.get('title') or '').strip()
      episode_title = tmdb_ep_title or plex_ep_title
      episode_detail = _media.strip_leading_article(episode_title.upper())
      episode_line = f'{episode_ref} {episode_detail}'.strip()
    elif media_type == 'movie' and metadata:
      guids = metadata.get('Guid') or []
      raw_movie, _ = _canonicalize_plex_title(metadata['title'], guids, 'movie')
      show_name = _vb.truncate_line(raw_movie.upper(), _vb.model.cols, 'ellipsis')
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
