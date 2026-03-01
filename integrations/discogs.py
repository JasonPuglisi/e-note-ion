# integrations/discogs.py
#
# Discogs collection integration.
#
# Picks a random record from the user's Discogs collection and returns it
# as a variables dict for use with content templates. The username is
# resolved automatically from the personal access token via GET /oauth/identity
# and cached for the process lifetime — no username config key required.
#
# At most two API calls are made per fire: one to read the total collection
# size from pagination metadata, one to fetch the randomly selected page
# (reused if the random offset falls on page 1). Results are cached for
# _COLLECTION_CACHE_TTL to avoid repeated calls on rapid re-fetches.
#
# Selection is uniformly random: a position (0..total-1) is chosen, then
# mapped to (page, index). Every record in the collection has equal
# probability 1/total regardless of page size.
#
# Required config.toml keys ([discogs]):
#   token     — Personal access token (read-only)
#               https://www.discogs.com/settings/developers
#
# Optional config.toml keys:
#   folder_id — Collection folder ID (default: '0' = all releases)

import random
from typing import Any

import requests

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, user_agent

_API_BASE = 'https://api.discogs.com'
_PER_PAGE = 50

# Module-level identity cache: resolved username from /oauth/identity.
# None = not yet populated.
_username_cache: str | None = None

# Last-known-good cache for collection data. Served on transient API failures
# if the entry is within _COLLECTION_CACHE_TTL seconds of its fetch time.
_collection_cache: CacheEntry | None = None
_COLLECTION_CACHE_TTL = 24 * 3600  # 24 hours


def _headers(token: str) -> dict[str, str]:
  """Build request headers including auth and User-Agent."""
  return {
    'Authorization': f'Discogs token={token}',
    'User-Agent': user_agent(),
  }


def _resolve_username(token: str) -> str:
  """Resolve the Discogs username from the personal access token.

  Calls GET /oauth/identity once and caches the result for the process
  lifetime. Raises IntegrationDataUnavailableError on failure.
  """
  global _username_cache

  if _username_cache is not None:
    return _username_cache

  try:
    r = fetch_with_retry(
      'GET',
      f'{_API_BASE}/oauth/identity',
      headers=_headers(token),
      timeout=10,
    )
    r.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'Discogs: identity request failed — {e}') from None

  username = r.json().get('username', '')
  if not username:
    raise IntegrationDataUnavailableError('Discogs: identity response missing username')

  _username_cache = username
  return username


def _strip_article(name: str) -> str:
  """Strip a leading 'The ' (case-insensitive) from a name."""
  if name.upper().startswith('THE '):
    return name[4:]
  return name


def _format_artist(release: dict[str, Any]) -> str:
  """Extract and format the primary artist name from a Discogs release dict.

  Uses the first artist only (most prominent). Strips Discogs disambiguator
  suffixes like ' (2)' and leading 'The '.
  """
  artists = release.get('basic_information', {}).get('artists', [])
  if not artists:
    return 'UNKNOWN ARTIST'
  name = artists[0].get('name', '').strip()
  # Strip Discogs disambiguator suffixes e.g. 'David Bowie (2)' → 'David Bowie'
  if ' (' in name:
    name = name[: name.rfind(' (')]
  return _strip_article(name).upper()


def _format_album(release: dict[str, Any]) -> str:
  """Format the album title from a Discogs release dict.

  Strips leading 'The ' and uppercases.
  """
  title = release.get('basic_information', {}).get('title', 'UNKNOWN ALBUM')
  return _strip_article(title).upper()


def get_variables() -> dict[str, list[list[str]]]:
  """Pick a random record from the Discogs collection.

  Resolves the username via /oauth/identity on first call (cached). Then
  makes at most two collection API calls: one to get the total collection
  size, one to fetch the randomly selected page (reused if offset falls on
  page 1).

  On API failure within the cache TTL, returns the last-known-good result.
  Raises IntegrationDataUnavailableError on cold-start failure or expired cache.
  """
  global _collection_cache

  import config as _config_mod

  token = _config_mod.get('discogs', 'token')
  folder_id = _config_mod.get_optional('discogs', 'folder_id') or '0'

  username = _resolve_username(token)
  url = f'{_API_BASE}/users/{username}/collection/folders/{folder_id}/releases'
  headers = _headers(token)

  try:
    r = fetch_with_retry(
      'GET',
      url,
      headers=headers,
      params={'page': 1, 'per_page': _PER_PAGE},
      timeout=10,
    )
    r.raise_for_status()
    data = r.json()

    total = data.get('pagination', {}).get('items', 0)
    if total == 0:
      raise IntegrationDataUnavailableError('Discogs: collection is empty')

    # Pick a uniformly random position across the entire collection, then
    # derive which page and index within that page it falls on.
    offset = random.randint(0, total - 1)  # nosec S311 — not a security context
    page = offset // _PER_PAGE + 1
    index = offset % _PER_PAGE

    if page == 1:
      releases = data.get('releases', [])
    else:
      r2 = fetch_with_retry(
        'GET',
        url,
        headers=headers,
        params={'page': page, 'per_page': _PER_PAGE},
        timeout=10,
      )
      r2.raise_for_status()
      releases = r2.json().get('releases', [])

    # Safety: guard against a stale total count (collection changed mid-call).
    if index >= len(releases):
      index = len(releases) - 1

    release = releases[index]

  except requests.RequestException as e:
    if isinstance(e, requests.HTTPError):
      msg = f'Discogs API error: {e.response.status_code} {e.response.reason}'
    else:
      msg = str(e)
    print(f'Discogs: collection request failed — {msg}')
    if _collection_cache is not None and _collection_cache.is_valid(_COLLECTION_CACHE_TTL):
      return _collection_cache.value
    raise IntegrationDataUnavailableError(f'Discogs: collection request failed — {msg}') from None

  result: dict[str, list[list[str]]] = {
    'album': [[_format_album(release)]],
    'artist': [[_format_artist(release)]],
  }
  _collection_cache = CacheEntry(result)
  return result
