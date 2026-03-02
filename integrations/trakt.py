# integrations/trakt.py
#
# Trakt.tv integration — user calendar and now-playing.
#
# Authentication uses the OAuth device code flow (no browser redirect). On
# first use, the scheduler prints a short code and URL to stdout; the user
# visits the URL, enters the code, and tokens are written to config.toml.
# Access tokens expire every ~90 days; refresh happens automatically 24 hours
# before expiry, rotating the refresh token as required by Trakt's API.
#
# Required config.toml keys ([trakt]):
#   client_id     — from https://trakt.tv/oauth/applications/new
#   client_secret — from the same application page
#
# Written by auth flow (do not edit manually):
#   access_token  — current OAuth access token
#   refresh_token — token used to obtain a new access token
#   expires_at    — Unix timestamp when access_token expires
#
# Optional config.toml keys:
#   calendar_days — lookahead window in days (default 7, max 33)

import logging
import threading
import time
from datetime import datetime, timezone

import requests

import integrations.vestaboard as _vb
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

_TRAKT_API_BASE = 'https://api.trakt.tv'

# Prevents multiple concurrent auth background threads.
_auth_started = False
_auth_lock = threading.Lock()

# Last successfully fetched watching state; used to emit a stopped indicator
# when a subsequent poll returns 204 (nothing playing).
_last_watching_vars: dict[str, list[list[str]]] | None = None
# True after the first 204 following a watching session. The violet stopped
# indicator is held back until a second consecutive 204 so that back-to-back
# episode transitions (autoplay gap ~30 s) don't produce a false stopped card.
_stop_pending: bool = False
_watching_lock = threading.Lock()

# Last-known-good cache for calendar data. Served on transient API failures
# if the entry is within _CALENDAR_CACHE_TTL seconds of its fetch time.
# No cache for watching — stale now-playing data is actively misleading.
_calendar_cache: CacheEntry | None = None
_CALENDAR_CACHE_TTL = 3600  # 1 hour

# Cache for next-up data (last show in progress from watch history).
_next_up_cache: CacheEntry | None = None
_NEXT_UP_CACHE_TTL = 3600  # 1 hour

# Max number of recently-watched shows to probe for a next episode.
_NEXT_UP_MAX_SHOWS = 5


# --- Token management ---


def _store_tokens(tokens: dict) -> None:
  """Write access_token, refresh_token, and expires_at to config.toml."""
  import config as _config_mod

  expires_at = int(time.time()) + tokens.get('expires_in', 7776000)
  _config_mod.write_section_values(
    'trakt',
    {
      'access_token': tokens['access_token'],
      'refresh_token': tokens['refresh_token'],
      'expires_at': expires_at,
    },
  )


def _refresh_token() -> None:
  """Exchange the current refresh token for a new access/refresh token pair."""
  import config as _config_mod

  logger.debug('Trakt: refreshing access token')
  client_id = _config_mod.get('trakt', 'client_id')
  client_secret = _config_mod.get('trakt', 'client_secret')
  refresh_token = _config_mod.get('trakt', 'refresh_token')

  r = requests.post(
    f'{_TRAKT_API_BASE}/oauth/token',
    json={
      'refresh_token': refresh_token,
      'client_id': client_id,
      'client_secret': client_secret,
      'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
      'grant_type': 'refresh_token',
    },
    headers={'Content-Type': 'application/json', 'User-Agent': user_agent()},
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Trakt token refresh failed: {e.response.status_code} {e.response.reason}') from None
  _store_tokens(r.json())
  logger.debug('Trakt: token refreshed successfully')


def _clear_tokens() -> None:
  """Clear stored tokens from config (in-memory and on disk)."""
  import config as _config_mod

  _config_mod.write_section_values('trakt', {'access_token': '', 'refresh_token': '', 'expires_at': ''})  # nosec B105 — empty strings intentionally clear stored tokens


def _get_token() -> str:
  """Return a valid Trakt access token, refreshing if within 24 hours of expiry.

  Raises IntegrationDataUnavailableError if auth is pending (no tokens yet) or
  if a token refresh fails (tokens cleared; re-auth flow started).
  """
  import config as _config_mod

  access_token = _config_mod.get_optional('trakt', 'access_token')
  if not access_token:
    _ensure_authenticated()
    raise IntegrationDataUnavailableError('Trakt auth pending — check logs for instructions')

  expires_at_str = _config_mod.get_optional('trakt', 'expires_at')
  if expires_at_str:
    try:
      expires_at = int(expires_at_str)
    except ValueError:
      pass  # malformed expires_at — proceed with current token
    else:
      secs_remaining = expires_at - time.time()
      if secs_remaining < 86400:
        logger.debug('Trakt: access token expires in %.0fs — triggering refresh', secs_remaining)
        try:
          _refresh_token()
        except requests.HTTPError as e:
          logger.warning(
            'Trakt: token refresh failed (%s) — clearing tokens and re-starting '
            'auth flow. Check logs for the new device code and URL.',
            e,
          )
          _clear_tokens()
          _ensure_authenticated()
          raise IntegrationDataUnavailableError(
            'Trakt auth pending — token refresh failed, re-authentication required'
          ) from None
        access_token = _config_mod.get_optional('trakt', 'access_token')

  return access_token


def _request_headers(access_token: str, client_id: str) -> dict[str, str]:
  return {
    'Authorization': f'Bearer {access_token}',
    'trakt-api-version': '2',
    'trakt-api-key': client_id,
    'User-Agent': user_agent(),
  }


def _handle_api_401() -> str:
  """Called when a Trakt API request returns 401.

  Attempts a token refresh. If the refresh succeeds, returns the new access
  token so the caller can retry the request. If the refresh fails (e.g. token
  revoked), clears stored tokens, starts re-auth, and raises
  IntegrationDataUnavailableError so the worker skips this cycle gracefully.
  """
  import config as _config_mod

  logger.warning('Trakt: received 401 — attempting token refresh')
  try:
    _refresh_token()
  except requests.HTTPError as e:
    logger.warning('Trakt: token refresh after 401 failed (%s) — clearing tokens and re-starting auth flow', e)
    _clear_tokens()
    _ensure_authenticated()
    raise IntegrationDataUnavailableError('Trakt auth pending — token invalid, re-authentication required') from None
  new_token = _config_mod.get_optional('trakt', 'access_token')
  if not new_token:
    raise IntegrationDataUnavailableError('Trakt auth pending — token unavailable after refresh')
  logger.debug('Trakt: token refreshed after 401 — retrying request')
  return new_token


# --- OAuth device code flow ---


def _run_auth_flow() -> None:
  """Background thread: device code flow → writes tokens to config.toml."""
  import config as _config_mod

  try:
    client_id = _config_mod.get('trakt', 'client_id')
    client_secret = _config_mod.get('trakt', 'client_secret')

    r = requests.post(
      f'{_TRAKT_API_BASE}/oauth/device/code',
      json={'client_id': client_id},
      headers={'Content-Type': 'application/json', 'User-Agent': user_agent()},
      timeout=10,
    )
    r.raise_for_status()
    data = r.json()

    device_code = data['device_code']
    user_code = data['user_code']
    verification_url = data['verification_url']
    interval: int = data.get('interval', 5)
    expires_in: int = data.get('expires_in', 600)

    logger.info('Trakt auth required. Go to %s and enter: %s', verification_url, user_code)

    deadline = time.time() + expires_in
    poll_interval = interval

    while time.time() < deadline:
      time.sleep(poll_interval)
      r = requests.post(
        f'{_TRAKT_API_BASE}/oauth/device/token',
        json={'code': device_code, 'client_id': client_id, 'client_secret': client_secret},
        headers={'Content-Type': 'application/json', 'User-Agent': user_agent()},
        timeout=10,
      )
      if r.status_code == 200:
        _store_tokens(r.json())
        logger.info('Trakt auth successful. Tokens saved to config.toml.')
        return
      elif r.status_code == 400:
        logger.debug('Trakt: auth pending — waiting for user approval (poll_interval=%ds)', poll_interval)
        continue  # pending — user hasn't approved yet
      elif r.status_code == 410:
        logger.error('Trakt auth code expired — restart the container to try again.')
        return
      elif r.status_code == 418:
        logger.error('Trakt auth denied — restart the container to try again.')
        return
      elif r.status_code == 429:
        poll_interval = poll_interval * 2  # back off on rate limit
        logger.debug('Trakt: auth rate-limited — backing off to %ds', poll_interval)

    logger.error('Trakt auth timed out — restart the container to try again.')
  except Exception as e:  # noqa: BLE001
    logger.error('Error during Trakt auth: %s', e)


def preflight() -> None:
  """Called at startup. Initiates the auth flow if tokens are absent.

  Proactively refreshes near-expiry tokens before any poll fires, so a brief
  scheduler restart near the expiry window doesn't cause the token to lapse.
  """
  import config as _config_mod

  access_token = _config_mod.get_optional('trakt', 'access_token')
  if not access_token:
    _ensure_authenticated()
    return

  expires_at_str = _config_mod.get_optional('trakt', 'expires_at')
  if expires_at_str:
    try:
      expires_at = int(expires_at_str)
    except ValueError:
      return
    secs_remaining = expires_at - time.time()
    if secs_remaining < 86400:
      logger.info('Trakt: access token expires in %.0fs — refreshing at startup', secs_remaining)
      try:
        _refresh_token()
      except requests.HTTPError as e:
        logger.warning('Trakt: startup token refresh failed (%s) — clearing tokens', e)
        _clear_tokens()
        _ensure_authenticated()


def _ensure_authenticated() -> None:
  """Start the auth background thread if not already started."""
  global _auth_started
  with _auth_lock:
    if _auth_started:
      logger.debug('Trakt: auth flow already in progress — not starting another')
      return
    _auth_started = True
  logger.debug('Trakt: starting auth background thread')
  threading.Thread(target=_run_auth_flow, daemon=True, name='trakt-auth').start()


# --- Integration functions ---


_LEADING_ARTICLES = ('THE ', 'AN ', 'A ')


def _format_episode_ref(season: int, number: int) -> str:
  """Return a compact episode ref, e.g. S9E8 (no zero-padding)."""
  return f'S{season}E{number}'


def _strip_leading_article(title: str) -> str:
  """Remove a leading article (A, An, The) from an uppercased title."""
  for article in _LEADING_ARTICLES:
    if title.startswith(article):
      return title[len(article) :]
  return title


def get_variables_calendar() -> dict[str, list[list[str]]]:
  """Fetch the next upcoming episode from the user's Trakt calendar.

  Returns variables: show_name, episode_ref (e.g. S01E02), air_day (e.g. MON),
  air_time (e.g. 20:00), episode_title. All values are uppercased.

  Raises IntegrationDataUnavailableError if the calendar window is empty.
  """
  import config as _config_mod

  token = _get_token()
  client_id = _config_mod.get('trakt', 'client_id')

  days_str = _config_mod.get_optional('trakt', 'calendar_days', '7')
  try:
    days = max(1, min(33, int(days_str)))
  except ValueError:
    days = 7

  global _calendar_cache

  today = datetime.now().strftime('%Y-%m-%d')
  url = f'{_TRAKT_API_BASE}/calendars/my/shows/{today}/{days}'
  try:
    r = fetch_with_retry('GET', url, headers=_request_headers(token, client_id), timeout=10)
    if r.status_code == 401:
      token = _handle_api_401()
      r = fetch_with_retry('GET', url, headers=_request_headers(token, client_id), timeout=10)
    r.raise_for_status()
  except requests.RequestException as e:
    if isinstance(e, requests.HTTPError):
      msg = f'Trakt calendar API error: {e.response.status_code} {e.response.reason}'
    else:
      msg = str(e)
    logger.warning('Trakt: calendar request failed — %s', msg)
    if _calendar_cache is not None and _calendar_cache.is_valid(_CALENDAR_CACHE_TTL):
      return _calendar_cache.value
    raise IntegrationDataUnavailableError(f'Trakt: calendar request failed — {msg}') from None

  entries = r.json()
  if not entries:
    raise IntegrationDataUnavailableError('No upcoming episodes in calendar window')

  now = datetime.now(timezone.utc)
  future_entries = [
    e for e in entries if e.get('first_aired') and datetime.fromisoformat(e['first_aired'].replace('Z', '+00:00')) > now
  ]
  if not future_entries:
    raise IntegrationDataUnavailableError('No upcoming episodes in calendar window')

  future_entries.sort(key=lambda e: e['first_aired'])
  entry = future_entries[0]

  show_name = _vb.truncate_line(entry['show']['title'].upper(), _vb.model.cols, 'word')
  ep = entry['episode']
  episode_ref = _format_episode_ref(ep['season'], ep['number'])
  episode_title = _strip_leading_article((ep.get('title') or '').upper())

  # Convert UTC first_aired → display timezone (config [scheduler].timezone,
  # or system local if unset).
  aired_dt = datetime.fromisoformat(entry['first_aired'].replace('Z', '+00:00'))
  local_dt = aired_dt.astimezone(_config_mod.get_timezone())
  air_day = local_dt.strftime('%a').upper()[:3]
  air_time = f'{local_dt.hour:02d}:{local_dt.minute:02d}'

  result = {
    'show_name': [[show_name]],
    'episode_ref': [[episode_ref]],
    'air_day': [[air_day]],
    'air_time': [[air_time]],
    'episode_title': [[episode_title]],
  }
  logger.debug('Trakt: fetched calendar — next episode: %s %s (%s)', show_name, episode_ref, air_day)
  _calendar_cache = CacheEntry(result)
  return result


def clear_watching_state() -> None:
  """Clear the cached watching state.

  Called by plex.py when a Plex event fires, preventing a stale Trakt stopped
  indicator from appearing after Plex has already handled the stop.
  """
  global _last_watching_vars, _stop_pending
  with _watching_lock:
    _last_watching_vars = None
    _stop_pending = False
  logger.debug('Trakt: watching state cleared (Plex event received)')


def get_variables_watching() -> dict[str, list[list[str]]]:
  """Fetch what the user is currently watching on Trakt.

  Returns variables: status_line, show_name, episode_ref, episode_title.
  status_line is '[G] NOW PLAYING' when playing, '[V] NOW PLAYING' on the
  second consecutive poll that returns 204 after playback ends (violet = Trakt
  brand colour; the one-poll debounce prevents false stopped cards during
  back-to-back episode transitions).
  For movies: episode_ref = 'MOVIE', episode_title = ''.
  All values are uppercased.

  Raises IntegrationDataUnavailableError if nothing is currently playing and
  no prior state was cached.
  """
  global _last_watching_vars, _stop_pending
  import config as _config_mod

  token = _get_token()
  client_id = _config_mod.get('trakt', 'client_id')

  url = f'{_TRAKT_API_BASE}/users/me/watching'
  try:
    r = fetch_with_retry('GET', url, headers=_request_headers(token, client_id), timeout=10)
    if r.status_code == 401:
      token = _handle_api_401()
      r = fetch_with_retry('GET', url, headers=_request_headers(token, client_id), timeout=10)
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'Trakt: watching request failed — {e}') from None

  if r.status_code == 204:
    with _watching_lock:
      last = _last_watching_vars
      pending = _stop_pending
      if last is not None:
        if pending:
          # Second consecutive 204 — genuine stop confirmed; emit indicator.
          logger.debug('Trakt: second consecutive 204 — emitting stopped indicator')
          _last_watching_vars = None
          _stop_pending = False
        else:
          # First 204 after a watching session — debounce: skip this cycle so
          # a back-to-back episode transition doesn't produce a false stop card.
          logger.debug('Trakt: first 204 after watching session — debouncing stop')
          _stop_pending = True
    if last is not None and pending:
      stopped = dict(last)
      stopped['status_line'] = [['[V] NOW PLAYING']]
      return stopped
    raise IntegrationDataUnavailableError('Nothing currently playing')

  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise IntegrationDataUnavailableError(
      f'Trakt watching API error: {e.response.status_code} {e.response.reason}'
    ) from None

  data = r.json()
  media_type = data.get('type')

  if media_type == 'episode':
    show_name = _vb.truncate_line(data['show']['title'].upper(), _vb.model.cols, 'word')
    ep = data['episode']
    episode_ref = _format_episode_ref(ep['season'], ep['number'])
    episode_title = _strip_leading_article((ep.get('title') or '').upper())
  elif media_type == 'movie':
    show_name = _vb.truncate_line(data['movie']['title'].upper(), _vb.model.cols, 'word')
    episode_ref = 'MOVIE'
    episode_title = ''
  else:
    raise IntegrationDataUnavailableError(f'Unknown media type: {media_type!r}')

  result = {
    'status_line': [['[G] NOW PLAYING']],
    'show_name': [[show_name]],
    'episode_ref': [[episode_ref]],
    'episode_title': [[episode_title]],
  }
  logger.debug('Trakt: watching %s %r (%s)', media_type, show_name, episode_ref)
  with _watching_lock:
    _last_watching_vars = result
    _stop_pending = False
  return result


def get_variables_next_up() -> dict[str, list[list[str]]]:
  """Fetch the next unwatched episode for the most recently watched show.

  Calls /users/me/watched/shows (sorted by last_watched_at desc), then probes
  /shows/{id}/progress/watched for each show in order until one has a
  non-null next_episode. Returns at most _NEXT_UP_MAX_SHOWS API round-trips.

  Returns variables: show_name, episode_ref (e.g. S3E1), episode_title.
  All values are uppercased.

  Raises IntegrationDataUnavailableError if no in-progress show is found or
  if the API is unreachable and the cache is expired / cold.
  """
  import config as _config_mod

  global _next_up_cache

  token = _get_token()
  client_id = _config_mod.get('trakt', 'client_id')

  watched_url = f'{_TRAKT_API_BASE}/users/me/watched/shows'
  try:
    r = fetch_with_retry('GET', watched_url, headers=_request_headers(token, client_id), timeout=10)
    if r.status_code == 401:
      token = _handle_api_401()
      r = fetch_with_retry('GET', watched_url, headers=_request_headers(token, client_id), timeout=10)
    r.raise_for_status()
  except requests.RequestException as e:
    if isinstance(e, requests.HTTPError):
      msg = f'Trakt watched/shows API error: {e.response.status_code} {e.response.reason}'
    else:
      msg = str(e)
    logger.warning('Trakt: next-up watched request failed — %s', msg)
    if _next_up_cache is not None and _next_up_cache.is_valid(_NEXT_UP_CACHE_TTL):
      return _next_up_cache.value
    raise IntegrationDataUnavailableError(f'Trakt: next-up watched request failed — {msg}') from None

  shows = r.json()[:_NEXT_UP_MAX_SHOWS]
  if not shows:
    raise IntegrationDataUnavailableError('No watched shows found')

  for entry in shows:
    trakt_id = entry['show']['ids']['trakt']
    progress_url = f'{_TRAKT_API_BASE}/shows/{trakt_id}/progress/watched'
    try:
      r2 = fetch_with_retry('GET', progress_url, headers=_request_headers(token, client_id), timeout=10)
      if r2.status_code == 401:
        token = _handle_api_401()
        r2 = fetch_with_retry('GET', progress_url, headers=_request_headers(token, client_id), timeout=10)
      r2.raise_for_status()
    except requests.RequestException as e:
      if isinstance(e, requests.HTTPError):
        msg = f'Trakt progress API error: {e.response.status_code} {e.response.reason}'
      else:
        msg = str(e)
      logger.warning('Trakt: next-up progress request failed — %s', msg)
      if _next_up_cache is not None and _next_up_cache.is_valid(_NEXT_UP_CACHE_TTL):
        return _next_up_cache.value
      raise IntegrationDataUnavailableError(f'Trakt: next-up progress request failed — {msg}') from None

    ep = r2.json().get('next_episode')
    if ep is not None:
      show_name = _vb.truncate_line(entry['show']['title'].upper(), _vb.model.cols, 'word')
      episode_ref = _format_episode_ref(ep['season'], ep['number'])
      episode_title = _strip_leading_article((ep.get('title') or '').upper())
      result = {
        'show_name': [[show_name]],
        'episode_ref': [[episode_ref]],
        'episode_title': [[episode_title]],
      }
      logger.debug('Trakt: next up — %s %s', show_name, episode_ref)
      _next_up_cache = CacheEntry(result)
      return result

  raise IntegrationDataUnavailableError('No next episode found in watched shows')
