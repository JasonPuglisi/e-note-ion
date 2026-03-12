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
def find_episode_by_tvdb_id(tvdb_episode_id: int) -> tuple[int, int, str, int] | None:
  """Return (season, episode, title, show_id) from TMDb for a TVDb episode ID.

  Uses TMDb's /find endpoint with external_source=tvdb_id to resolve the
  canonical TMDb season and episode numbers. This corrects Trakt's TVDb-based
  single-flat-season numbering for anime to the proper multi-season form.
  The returned show_id is the TMDb series ID and can be passed to
  get_show_title() to retrieve the canonical show name.

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
      title: str = ep.get('name') or ''
      if season is not None and episode is not None and show_id is not None:
        logger.debug('TMDb: tvdb_ep %d → S%dE%d %r (show=%d)', tvdb_episode_id, season, episode, title, show_id)
        return (season, episode, title, show_id)
  except Exception as e:  # noqa: BLE001
    logger.debug('TMDb: find_episode_by_tvdb_id(%d) failed — %s', tvdb_episode_id, e)
  return None
