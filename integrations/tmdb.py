# integrations/tmdb.py
#
# TMDb (The Movie Database) — canonical media metadata authority.
#
# Used by the Plex and Trakt integrations to normalise show/movie titles and
# resolve correct season/episode numbers. Particularly valuable for anime,
# where Trakt uses TVDb's single-flat-season convention while TMDb uses the
# canonical multi-season structure.
#
# Optional — only active when [tmdb] api_read_access_token is present in
# config.toml. When unconfigured, all public functions return None and callers
# fall back to their native integration data.
#
# Required config.toml keys ([tmdb]):
#   api_read_access_token — read access token from
#                           https://www.themoviedb.org/settings/api

import functools
import logging

from integrations.http import fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

_TMDB_API_BASE = 'https://api.themoviedb.org/3'


def is_configured() -> bool:
  """Return True if the TMDb API read access token is set in config."""
  import config as _config_mod

  return bool(_config_mod.get_optional('tmdb', 'api_read_access_token'))


def _request_headers() -> dict[str, str]:
  import config as _config_mod

  token = _config_mod.get('tmdb', 'api_read_access_token')
  return {
    'Authorization': f'Bearer {token}',
    'Accept': 'application/json',
    'User-Agent': user_agent(),
  }


@functools.lru_cache(maxsize=256)
def get_show_title(tmdb_show_id: int) -> str | None:
  """Return the canonical show name from TMDb, or None on failure.

  Result is cached in-memory by show ID for the lifetime of the process.
  """
  try:
    r = fetch_with_retry(
      'GET',
      f'{_TMDB_API_BASE}/tv/{tmdb_show_id}',
      headers=_request_headers(),
      timeout=10,
    )
    r.raise_for_status()
    title: str | None = r.json().get('name')
    if title:
      logger.debug('TMDb: show %d → %r', tmdb_show_id, title)
      return title
  except Exception as e:  # noqa: BLE001
    logger.debug('TMDb: get_show_title(%d) failed — %s', tmdb_show_id, e)
  return None


@functools.lru_cache(maxsize=256)
def get_movie_title(tmdb_movie_id: int) -> str | None:
  """Return the canonical movie title from TMDb, or None on failure.

  Result is cached in-memory by movie ID for the lifetime of the process.
  """
  try:
    r = fetch_with_retry(
      'GET',
      f'{_TMDB_API_BASE}/movie/{tmdb_movie_id}',
      headers=_request_headers(),
      timeout=10,
    )
    r.raise_for_status()
    title: str | None = r.json().get('title')
    if title:
      logger.debug('TMDb: movie %d → %r', tmdb_movie_id, title)
      return title
  except Exception as e:  # noqa: BLE001
    logger.debug('TMDb: get_movie_title(%d) failed — %s', tmdb_movie_id, e)
  return None


@functools.lru_cache(maxsize=512)
def find_episode_by_tvdb_id(tvdb_episode_id: int) -> tuple[int, int, str, int, int] | None:
  """Return (season, episode, title, show_id, tmdb_episode_id) from TMDb for a TVDb episode ID.

  Uses TMDb's /find endpoint with external_source=tvdb_id to resolve the
  canonical TMDb season and episode numbers. This corrects Trakt's TVDb-based
  single-flat-season numbering for anime to the proper multi-season form.
  The returned show_id is the TMDb series ID and can be passed to
  get_show_title() to retrieve the canonical show name. The tmdb_episode_id
  can be passed to get_episode_group_position() for a more precise S/E number.

  Result is cached in-memory by TVDb episode ID for the lifetime of the
  process. Returns None if the lookup fails or returns no results.
  """
  try:
    r = fetch_with_retry(
      'GET',
      f'{_TMDB_API_BASE}/find/{tvdb_episode_id}',
      headers=_request_headers(),
      params={'external_source': 'tvdb_id'},
      timeout=10,
    )
    r.raise_for_status()
    results = r.json().get('tv_episode_results', [])
    if results:
      ep = results[0]
      season = ep.get('season_number')
      episode = ep.get('episode_number')
      show_id = ep.get('show_id')
      tmdb_episode_id = ep.get('id')
      title: str = ep.get('name') or ''
      if season is not None and episode is not None and show_id is not None and tmdb_episode_id is not None:
        logger.debug(
          'TMDb: tvdb_ep %d → S%dE%d %r (show=%d, tmdb_ep=%d)',
          tvdb_episode_id,
          season,
          episode,
          title,
          show_id,
          tmdb_episode_id,
        )
        return (season, episode, title, show_id, tmdb_episode_id)
  except Exception as e:  # noqa: BLE001
    logger.debug('TMDb: find_episode_by_tvdb_id(%d) failed — %s', tvdb_episode_id, e)
  return None


@functools.lru_cache(maxsize=128)
def _get_type6_group_id(show_id: int) -> str | None:
  """Return the TMDb episode group ID for the type-6 (TV seasons) group, or None."""
  try:
    r = fetch_with_retry(
      'GET',
      f'{_TMDB_API_BASE}/tv/{show_id}/episode_groups',
      headers=_request_headers(),
      timeout=10,
    )
    r.raise_for_status()
    for group in r.json().get('results', []):
      if group.get('type') == 6:
        return group['id']
  except Exception as e:  # noqa: BLE001
    logger.debug('TMDb: _get_type6_group_id(%d) failed — %s', show_id, e)
  return None


@functools.lru_cache(maxsize=128)
def _get_episode_group(group_id: str) -> list[dict] | None:
  """Return the groups list for a TMDb episode group ID, or None on failure."""
  try:
    r = fetch_with_retry(
      'GET',
      f'{_TMDB_API_BASE}/tv/episode_group/{group_id}',
      headers=_request_headers(),
      timeout=10,
    )
    r.raise_for_status()
    return r.json().get('groups', [])
  except Exception as e:  # noqa: BLE001
    logger.debug('TMDb: _get_episode_group(%r) failed — %s', group_id, e)
  return None


def get_episode_group_position(show_id: int, tmdb_episode_id: int) -> tuple[int, int] | None:
  """Return (season, episode) from the type-6 episode group for a TMDb episode.

  Looks up the show's type-6 (TV broadcast seasons) episode group and finds
  the episode by its TMDb ID. Returns (group.order, episode.order + 1) using
  the group's positional order, skipping groups at order 0 (Specials).

  This gives the canonical broadcast season/episode number for shows like
  Frieren where TMDb's base data collapses all episodes into Season 1 but
  the episode group correctly models them as separate seasons.

  Result is not cached directly (callers cache via lru_cache on helpers).
  Returns None if no type-6 group exists, the episode is not found, or
  any request fails.
  """
  group_id = _get_type6_group_id(show_id)
  if not group_id:
    return None
  groups = _get_episode_group(group_id)
  if not groups:
    return None
  for group in groups:
    group_order = group.get('order', 0)
    if group_order == 0:
      continue  # skip Specials
    for ep in group.get('episodes', []):
      if ep.get('id') == tmdb_episode_id:
        episode_number = ep.get('order', 0) + 1
        logger.debug(
          'TMDb: episode group position for ep %d → S%dE%d (group %r)',
          tmdb_episode_id,
          group_order,
          episode_number,
          group_id,
        )
        return (group_order, episode_number)
  return None
