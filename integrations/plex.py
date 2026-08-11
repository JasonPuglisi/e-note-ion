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
# All three events are debounced with a 3-second window to suppress display
# flashes for rapid event sequences (e.g. play→pause, pause→resume, or
# stop→play between back-to-back episodes). Only the settled final state
# reaches the board.
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

# Debounce window applied to play, pause, and stop events.
_DEBOUNCE_SECS = 3

# Maps each handled event to the plex.json template it would display.
_EVENT_TEMPLATES: dict[str, str] = {
  'media.play': 'now_playing',
  'media.resume': 'now_playing',
  'media.pause': 'paused',
  'media.stop': 'stopped',
}


class _State(enum.Enum):
  IDLE = 'idle'
  PLAYING = 'playing'
  PAUSED = 'paused'


# Tracks the current Plex playback state. play/resume always transition to
# PLAYING. pause is only valid from PLAYING. stop is valid from PLAYING or
# PAUSED. Invalid transitions return None without firing any display update.
_state: _State = _State.IDLE

# Pending debounce timers for play, pause, and stop events. All three share
# the same _DEBOUNCE_SECS window. Incoming events cancel sibling timers so
# that only the settled final state reaches the board.
_pending_play_timer: threading.Timer | None = None
_pending_pause_timer: threading.Timer | None = None
_pending_stop_timer: threading.Timer | None = None

# Data captured for the currently-pending stop timer, stored at module level
# so that an incoming play event can rescue it into _saved_stop_data on cancel.
_pending_stop_data: dict[str, Any] | None = None

# Stop data saved when a stop debounce is cancelled by an incoming play event.
# If the play debounce is subsequently cancelled by another stop, this data
# (the original stopped episode) is used instead of the new stop's metadata.
# Cleared only when the play debounce fires (NOW PLAYING confirmed shown).
_saved_stop_data: dict[str, Any] | None = None


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
) -> tuple[str, str | None, int | None, int | None]:
  """Return (canonical_title, tmdb_episode_title, canonical_season, canonical_episode).

  For episodes, tries in order:
    1. tvdb:// guid (episode-level TVDb ID)
    2. imdb:// guid (episode-level IMDb ID)
    3. TMDb title search + S/E lookup (when season and episode are provided)

  When TMDb resolves the episode via tvdb:// or imdb://, the returned season
  and episode reflect TMDb's broadcast-season numbering (via the type-6
  episode group when available, falling back to TMDb base data). This corrects
  flat-numbered anime libraries (e.g. Re:Zero on TVDb) to canonical broadcast
  seasons. Callers should fall back to Plex's parentIndex/index when the
  returned season/episode are None.

  For movies, the tmdb:// entry is the TMDb movie ID. Episode title and
  canonical S/E are always None for movies.

  Falls back to (raw_title, None, None, None) when TMDb is unconfigured, the
  Guid array has no usable entry, or all lookups fail.

  media_type must be 'episode' or 'movie'.
  """
  import integrations.tmdb as _tmdb

  if not _tmdb.is_configured():
    return raw_title, None, None, None

  if media_type == 'episode':
    tvdb_id = _parse_guid_id(guids, 'tvdb://')
    if tvdb_id is not None:
      ep_result = _tmdb.find_episode_by_tvdb_id(tvdb_id)
      if ep_result is not None:
        res_season, res_episode, ep_title, show_id, tmdb_ep_id = ep_result
        group_pos = _tmdb.get_episode_group_position(show_id, tmdb_ep_id)
        if group_pos:
          res_season, res_episode = group_pos
        canonical = _tmdb.get_show_title(show_id)
        if canonical:
          logger.debug('plex: tmdb canonical %r -> %r S%dE%d', raw_title, canonical, res_season, res_episode)
        else:
          logger.debug('plex: tmdb show lookup failed for id=%d, using raw title %r', show_id, raw_title)
        return canonical if canonical else raw_title, ep_title or None, res_season, res_episode
      logger.debug('plex: tvdb ep lookup failed for id=%d, using raw title %r', tvdb_id, raw_title)
      return raw_title, None, None, None
    # No tvdb:// guid — fall back to imdb://
    logger.debug('plex: no tvdb:// guid for episode %r, trying imdb://', raw_title)
    imdb_id = _parse_guid_id_str(guids, 'imdb://')
    if imdb_id:
      ep_result = _tmdb.find_episode_by_imdb_id(imdb_id)
      if ep_result is not None:
        res_season, res_episode, ep_title, show_id, tmdb_ep_id = ep_result
        group_pos = _tmdb.get_episode_group_position(show_id, tmdb_ep_id)
        if group_pos:
          res_season, res_episode = group_pos
        canonical = _tmdb.get_show_title(show_id)
        if canonical:
          logger.debug('plex: tmdb canonical (via imdb) %r -> %r S%dE%d', raw_title, canonical, res_season, res_episode)
        else:
          logger.debug('plex: tmdb imdb lookup show %d title failed, using raw title %r', show_id, raw_title)
        return canonical if canonical else raw_title, ep_title or None, res_season, res_episode
      logger.debug('plex: imdb ep lookup failed for %r, using raw title %r', imdb_id, raw_title)
    else:
      logger.debug('plex: no imdb:// guid for episode %r, trying title search', raw_title)
    # Last resort: search TMDb by show title + S/E numbers.
    # Two sub-strategies after finding the show:
    #   1. Base season lookup (/tv/{id}/season/{s}/episode/{e}) — works when
    #      Plex and TMDb use the same season numbering.
    #   2. Episode-group lookup — works when Plex uses broadcast-season
    #      numbers (TVDb convention) but TMDb base data uses a flat single
    #      season (e.g. Frieren, JJK). The type-6 group already cached by
    #      get_episode_group_position re-used here at no extra cost.
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
            'plex: tmdb title-search (base) %r → show=%d S%dE%d %r',
            raw_title,
            show_id,
            season,
            episode,
            ep_title,
          )
          return canonical if canonical else raw_title, ep_title or None, season, episode
        # Base lookup failed (show uses flat/different season numbering) — try episode group.
        ep_result_grp = _tmdb.find_episode_in_group(show_id, season, episode)
        if ep_result_grp is not None:
          ep_title, _ = ep_result_grp
          canonical = _tmdb.get_show_title(show_id)
          logger.debug(
            'plex: tmdb title-search (group) %r → show=%d S%dE%d %r',
            raw_title,
            show_id,
            season,
            episode,
            ep_title,
          )
          return canonical if canonical else raw_title, ep_title or None, season, episode
      logger.debug('plex: title search for %r found nothing', raw_title)
    return raw_title, None, None, None

  else:  # movie
    tmdb_id = _parse_guid_id(guids, 'tmdb://')
    if tmdb_id is None:
      logger.debug('plex: no tmdb:// guid for %r, using raw title', raw_title)
      return raw_title, None, None, None
    canonical = _tmdb.get_movie_title(tmdb_id)
    if canonical:
      logger.debug('plex: tmdb canonical %r -> %r', raw_title, canonical)
    else:
      logger.debug('plex: tmdb lookup failed, using raw title %r', raw_title)
    return canonical if canonical else raw_title, None, None, None


def _load_template_config(template_name: str) -> dict[str, Any]:
  """Return effective config for a webhook-only template from plex.json.

  Applies any [plex.schedules.<template_name>] overrides from config.toml
  on top of the JSON defaults, matching the behaviour of scheduled templates.
  """
  import config as _config_mod
  import scheduler as _sched

  with open(_PLEX_JSON_PATH) as f:
    content = json.load(f)
  template = content['templates'][template_name]
  schedule = template['schedule']

  override = _config_mod.get_schedule_override(f'plex.{template_name}')

  effective: dict[str, Any] = {
    'hold': schedule['hold'],
    'timeout': schedule['timeout'],
    'priority': template['priority'],
    'truncation': template.get('truncation', 'hard'),
    'templates': template.get('templates', []),
    # Resolved here (not left to the scheduler's load-time registry) because
    # plex is typically run webhook-only, with plex.json never loaded.
    'private': _sched.resolve_private(template, override, f'plex.{template_name}'),
  }

  for field in ('hold', 'timeout'):
    val = override.get(field)
    if isinstance(val, int) and val >= 0:
      effective[field] = val
  priority_val = override.get('priority')
  if isinstance(priority_val, int) and 0 <= priority_val <= 10:
    effective['priority'] = priority_val

  return effective


def _cancel_pending_timers() -> None:
  """Cancel all pending debounce timers and clear their captured data."""
  global _pending_play_timer, _pending_pause_timer, _pending_stop_timer
  global _pending_stop_data, _saved_stop_data

  for timer in (_pending_play_timer, _pending_pause_timer, _pending_stop_timer):
    if timer is not None:
      timer.cancel()
  _pending_play_timer = None
  _pending_pause_timer = None
  _pending_stop_timer = None
  _pending_stop_data = None
  _saved_stop_data = None


def _is_public_and_private(event: str) -> bool:
  """Return whether public mode is active and this event's template is private."""
  import public as _public_mod

  if not _public_mod.is_public():
    return False
  template_name = _EVENT_TEMPLATES.get(event)
  if template_name is None:
    return False
  try:
    return bool(_load_template_config(template_name)['private'])
  except Exception as e:  # noqa: BLE001 — fail closed: suppress on config error
    logger.warning('plex: could not resolve private flag for %r (%s); suppressing in public mode', template_name, e)
    return True


def handle_webhook(payload: dict[str, Any], credential_name: str | None = None) -> WebhookMessage | None:
  """Process a Plex webhook event and schedule a debounced display update.

  Enforces a state machine (IDLE → PLAYING ↔ PAUSED → IDLE):
  - play/resume: always valid; transition to PLAYING.
  - pause: only valid from PLAYING; ignored in IDLE or PAUSED.
  - stop: only valid from PLAYING or PAUSED; ignored in IDLE.

  All three event types are debounced: the display update is enqueued
  asynchronously after _DEBOUNCE_SECS seconds. Incoming events cancel
  sibling timers so that rapid self-cancelling sequences (play→pause,
  pause→resume, stop→play between episodes) produce no display flashes.

  While public mode is active and the event's template is private, the event
  is discarded outright: no timer, no enqueue, no board interrupt. State is
  reset to IDLE and re-established by the next event once public mode ends.

  Always returns None — the scheduler receives "Discarded" for every Plex
  webhook and the actual enqueueing is handled by the timer callbacks.
  """
  global _state, _pending_play_timer, _pending_pause_timer, _pending_stop_timer
  global _pending_stop_data, _saved_stop_data

  try:
    event = payload.get('event', '')
    if event not in _HANDLED_EVENTS:
      return None

    logger.debug('plex: %s (state=%s)', event, _state.value)

    # Cleared before the public-mode check below so Trakt's view of playback
    # stays accurate even while the display is suppressed — otherwise a stale
    # watching state could surface once public mode ends.
    try:
      import integrations.trakt as _trakt

      _trakt.clear_watching_state()
    except ImportError:
      pass

    # Public mode: plex content is private, so nothing will reach the board.
    # Return before scheduling any timer — a fired timer would enqueue a
    # message that is dropped at pop time, but only after fire_hold_interrupt()
    # had already cut short whatever public content was holding the board.
    # Also skips the TMDb lookups in _build_metadata.
    if _is_public_and_private(event):
      _cancel_pending_timers()
      _state = _State.IDLE
      logger.debug('plex: discarding %s: public mode active and template is private', event)
      return None

    # --- State machine transition and timer management ---

    if event in _PLAY_EVENTS:
      # Cancel all sibling timers. When cancelling a pending stop, rescue its
      # data into _saved_stop_data so a subsequent quick stop can reuse it.
      if _pending_play_timer is not None:
        _pending_play_timer.cancel()
        _pending_play_timer = None
      if _pending_pause_timer is not None:
        _pending_pause_timer.cancel()
        _pending_pause_timer = None
      if _pending_stop_timer is not None:
        _pending_stop_timer.cancel()
        _pending_stop_timer = None
        _saved_stop_data = _pending_stop_data
        _pending_stop_data = None
        logger.debug('plex: cancelled pending stop timer (play/resume arrived)')
      _state = _State.PLAYING

      # Build metadata.
      metadata = payload.get('Metadata')
      media_type = metadata.get('type') if metadata else None
      show_name_rows, episode_line = _build_metadata(metadata, media_type, event)

      if not show_name_rows:
        logger.debug('plex: discarding %s: no show_name (media_type=%r)', event, media_type)
        return None

      cfg = _load_template_config('now_playing')
      play_data = {
        'templates': cfg['templates'],
        'variables': {
          'show_name': [show_name_rows],
          'episode_line': [[episode_line]],
        },
        'truncation': cfg['truncation'],
      }
      if cfg['private']:
        play_data['private'] = True
      priority = cfg['priority']
      hold = cfg['hold']
      timeout = cfg['timeout']
      captured_show_name = show_name_rows  # closure needs the local value

      def _enqueue_now_playing() -> None:
        global _pending_play_timer, _saved_stop_data
        _pending_play_timer = None
        _saved_stop_data = None  # NOW PLAYING confirmed shown; discard rescued stop data
        if _state != _State.PLAYING:
          logger.debug('plex: play timer fired but state=%s, skipping', _state.value)
          return
        if not captured_show_name:  # defensive re-check
          logger.debug('plex: play timer fired but show_name empty, skipping')
          return
        import scheduler as _sched

        logger.debug('plex: play debounce elapsed, enqueueing now_playing (credential=%r)', credential_name)
        _sched.enqueue(
          priority=priority,
          data=play_data,
          hold=hold,
          timeout=timeout,
          name='webhook.plex',
          indefinite=True,
          supersede_tag='plex',
        )
        _sched.fire_hold_interrupt(supersede_tag='plex')

      logger.debug('plex: play debounce started (%ds)', _DEBOUNCE_SECS)
      _pending_play_timer = threading.Timer(_DEBOUNCE_SECS, _enqueue_now_playing)
      _pending_play_timer.daemon = True
      _pending_play_timer.start()
      return None

    elif event == _PAUSE_EVENT:
      if _state != _State.PLAYING:
        logger.debug('plex: discarding %s: state=%s, expected playing', event, _state.value)
        return None
      # Cancel pending play timer. Do NOT touch _saved_stop_data — it is only
      # cleared when the play debounce fires (NOW PLAYING confirmed shown).
      if _pending_play_timer is not None:
        _pending_play_timer.cancel()
        _pending_play_timer = None
        logger.debug('plex: cancelled pending play timer (pause arrived)')
      _state = _State.PAUSED

      # Build metadata.
      metadata = payload.get('Metadata')
      media_type = metadata.get('type') if metadata else None
      show_name_rows, episode_line = _build_metadata(metadata, media_type, event)

      if not show_name_rows:
        logger.debug('plex: discarding %s: no show_name (media_type=%r)', event, media_type)
        return None

      cfg = _load_template_config('paused')
      pause_data = {
        'templates': cfg['templates'],
        'variables': {
          'show_name': [show_name_rows],
          'episode_line': [[episode_line]],
        },
        'truncation': cfg['truncation'],
      }
      if cfg['private']:
        pause_data['private'] = True
      priority = cfg['priority']
      hold = cfg['hold']
      timeout = cfg['timeout']

      def _enqueue_paused() -> None:
        global _pending_pause_timer
        _pending_pause_timer = None
        if _state != _State.PAUSED:
          logger.debug('plex: pause timer fired but state=%s, skipping', _state.value)
          return
        import scheduler as _sched

        hold_tag = _sched.current_hold_tag()
        if hold_tag != 'plex':
          logger.debug('plex: pause timer fired but board tag=%r, skipping', hold_tag)
          return
        logger.debug('plex: pause debounce elapsed, enqueueing paused (credential=%r)', credential_name)
        _sched.enqueue(
          priority=priority,
          data=pause_data,
          hold=hold,
          timeout=timeout,
          name='webhook.plex',
          indefinite=True,
          supersede_tag='plex',
        )
        _sched.fire_hold_interrupt(supersede_tag='plex')

      logger.debug('plex: pause debounce started (%ds)', _DEBOUNCE_SECS)
      _pending_pause_timer = threading.Timer(_DEBOUNCE_SECS, _enqueue_paused)
      _pending_pause_timer.daemon = True
      _pending_pause_timer.start()
      return None

    elif event in _STOP_EVENTS:
      # Cancel any stale pending stop timer (defensive — e.g. duplicate stop).
      if _pending_stop_timer is not None:
        _pending_stop_timer.cancel()
        _pending_stop_timer = None
        _pending_stop_data = None
      # Cancel play and pause timers.
      if _pending_play_timer is not None:
        _pending_play_timer.cancel()
        _pending_play_timer = None
        logger.debug('plex: cancelled pending play timer (stop arrived)')
      if _pending_pause_timer is not None:
        _pending_pause_timer.cancel()
        _pending_pause_timer = None
        logger.debug('plex: cancelled pending pause timer (stop arrived)')
      if _state == _State.IDLE:
        logger.debug('plex: discarding %s: state=idle', event)
        return None
      _state = _State.IDLE

      # Build metadata from the current stop event.
      metadata = payload.get('Metadata')
      media_type = metadata.get('type') if metadata else None
      show_name_rows, episode_line = _build_metadata(metadata, media_type, event)

      cfg = _load_template_config('stopped')
      has_media = bool(show_name_rows)
      new_stop_data = {
        'templates': cfg['templates'],
        'variables': {'show_name': [show_name_rows], 'episode_line': [[episode_line]]} if has_media else {},
        'truncation': cfg['truncation'],
      }
      if cfg['private']:
        new_stop_data['private'] = True

      # Prefer rescued stop data from an earlier cancelled stop debounce
      # (e.g. ep1-stop → ep2-play → ep2-stop: show ep1's stopped card).
      effective_stop_data = _saved_stop_data if _saved_stop_data is not None else new_stop_data
      _saved_stop_data = None  # consumed regardless
      _pending_stop_data = effective_stop_data

      priority = cfg['priority']
      hold = cfg['hold']
      timeout = cfg['timeout']

      def _enqueue_stopped() -> None:
        global _pending_stop_timer, _pending_stop_data
        _pending_stop_timer = None
        _pending_stop_data = None
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
          data=effective_stop_data,
          hold=hold,
          timeout=timeout,
          name='webhook.plex',
          supersede_tag='plex',
        )
        _sched.fire_hold_interrupt(supersede_tag='plex')

      logger.debug('plex: stop debounce started (%ds)', _DEBOUNCE_SECS)
      _pending_stop_timer = threading.Timer(_DEBOUNCE_SECS, _enqueue_stopped)
      _pending_stop_timer.daemon = True
      _pending_stop_timer.start()
      return None

  except Exception as e:  # noqa: BLE001
    logger.error('Plex webhook error: %s', e)
    return None

  return None  # unreachable but satisfies type checker


def _build_metadata(
  metadata: dict[str, Any] | None,
  media_type: str | None,
  event: str,
) -> tuple[list[str], str]:
  """Parse metadata from a Plex payload and return (show_name_rows, episode_line).

  show_name_rows is a list of display rows (1 row for episodes, up to 2 rows
  for movies). Returns ([], '') for non-video media or missing/malformed
  metadata.
  """
  if media_type == 'episode' and metadata:
    guids: list[dict[str, Any]] = metadata.get('Guid') or []
    logger.debug(
      'plex: episode guids=%r guid=%r grandparentGuid=%r',
      guids,
      metadata.get('guid'),
      metadata.get('grandparentGuid'),
    )
    raw_show, tmdb_ep_title, canonical_season, canonical_episode = _canonicalize_plex_title(
      metadata['grandparentTitle'],
      guids,
      'episode',
      season=metadata.get('parentIndex'),
      episode=metadata.get('index'),
    )
    show_name_rows = [_vb.truncate_line(raw_show.upper(), _vb.model.cols, 'ellipsis')]
    # Prefer TMDb's broadcast-season numbering when canonicalization succeeded;
    # otherwise fall back to Plex's parentIndex/index for non-anime libraries
    # and the path where TMDb returned no usable mapping.
    if canonical_episode is not None:
      ref_season = canonical_season
      ref_episode = canonical_episode
    else:
      ref_season = metadata.get('parentIndex')
      ref_episode = metadata['index']
    episode_ref = _media.format_episode_ref(ref_season, ref_episode)
    plex_ep_title = (metadata.get('title') or '').strip()
    episode_title = tmdb_ep_title or plex_ep_title
    episode_detail = _media.strip_leading_article_if_needed(episode_title.upper(), _vb.model.cols, f'{episode_ref} ')
    episode_line = f'{episode_ref} {episode_detail}'.strip()
  elif media_type == 'movie' and metadata:
    guids = metadata.get('Guid') or []
    raw_movie, _, _, _ = _canonicalize_plex_title(metadata['title'], guids, 'movie')
    show_name_rows = _media.wrap_title_to_rows(raw_movie.upper(), _vb.model.cols, 2)
    episode_line = ''
  else:
    logger.debug('plex: no displayable metadata (type=%r)', media_type)
    show_name_rows = []
    episode_line = ''

  return show_name_rows, episode_line
