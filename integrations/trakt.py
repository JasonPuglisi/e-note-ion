# integrations/trakt.py
#
# Trakt.tv integration — user calendar and now-playing.
#
# Authentication uses the OAuth device code flow (no browser redirect). On
# first use, the scheduler prints a short code and URL to stdout; the user
# visits the URL, enters the code, and tokens are written to config.toml.
# Access tokens expire every ~90 days; refresh happens automatically 1 hour
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

import threading
import time
from datetime import datetime

import requests

from exceptions import IntegrationDataUnavailableError

_TRAKT_API_BASE = 'https://api.trakt.tv'

# Prevents multiple concurrent auth background threads.
_auth_started = False
_auth_lock = threading.Lock()


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
    headers={'Content-Type': 'application/json'},
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Trakt token refresh failed: {e.response.status_code} {e.response.reason}') from None
  _store_tokens(r.json())


def _get_token() -> str:
  """Return a valid Trakt access token, refreshing if within 1 hour of expiry.

  Raises IntegrationDataUnavailableError if auth is pending (no tokens yet).
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
      if expires_at - time.time() < 3600:
        _refresh_token()
        access_token = _config_mod.get_optional('trakt', 'access_token')
    except ValueError:
      pass  # malformed expires_at — proceed with current token

  return access_token


def _request_headers(access_token: str, client_id: str) -> dict[str, str]:
  return {
    'Authorization': f'Bearer {access_token}',
    'trakt-api-version': '2',
    'trakt-api-key': client_id,
  }


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
      headers={'Content-Type': 'application/json'},
      timeout=10,
    )
    r.raise_for_status()
    data = r.json()

    device_code = data['device_code']
    user_code = data['user_code']
    verification_url = data['verification_url']
    interval: int = data.get('interval', 5)
    expires_in: int = data.get('expires_in', 600)

    print(f'Trakt auth required. Go to {verification_url} and enter: {user_code}')

    deadline = time.time() + expires_in
    poll_interval = interval

    while time.time() < deadline:
      time.sleep(poll_interval)
      r = requests.post(
        f'{_TRAKT_API_BASE}/oauth/device/token',
        json={'code': device_code, 'client_id': client_id, 'client_secret': client_secret},
        headers={'Content-Type': 'application/json'},
        timeout=10,
      )
      if r.status_code == 200:
        _store_tokens(r.json())
        print('Trakt auth successful. Tokens saved to config.toml.')
        return
      elif r.status_code == 400:
        continue  # pending — user hasn't approved yet
      elif r.status_code == 410:
        print('Error: Trakt auth code expired — restart the container to try again.')
        return
      elif r.status_code == 418:
        print('Error: Trakt auth denied — restart the container to try again.')
        return
      elif r.status_code == 429:
        poll_interval = poll_interval * 2  # back off on rate limit

    print('Error: Trakt auth timed out — restart the container to try again.')
  except Exception as e:  # noqa: BLE001
    print(f'Error during Trakt auth: {e}')


def preflight() -> None:
  """Called at startup. Initiates the auth flow if tokens are absent."""
  import config as _config_mod

  if not _config_mod.get_optional('trakt', 'access_token'):
    _ensure_authenticated()


def _ensure_authenticated() -> None:
  """Start the auth background thread if not already started."""
  global _auth_started
  with _auth_lock:
    if _auth_started:
      return
    _auth_started = True
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

  today = datetime.now().strftime('%Y-%m-%d')
  r = requests.get(
    f'{_TRAKT_API_BASE}/calendars/my/shows/{today}/{days}',
    headers=_request_headers(token, client_id),
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Trakt calendar API error: {e.response.status_code} {e.response.reason}') from None

  entries = r.json()
  if not entries:
    raise IntegrationDataUnavailableError('No upcoming episodes in calendar window')

  entries.sort(key=lambda e: e['first_aired'])
  entry = entries[0]

  show_name = entry['show']['title'].upper()
  ep = entry['episode']
  episode_ref = _format_episode_ref(ep['season'], ep['number'])
  episode_title = _strip_leading_article((ep.get('title') or '').upper())

  # Convert UTC first_aired → display timezone (config [scheduler].timezone,
  # or system local if unset).
  aired_dt = datetime.fromisoformat(entry['first_aired'].replace('Z', '+00:00'))
  local_dt = aired_dt.astimezone(_config_mod.get_timezone())
  air_day = local_dt.strftime('%a').upper()[:3]
  air_time = f'{local_dt.hour}:{local_dt.minute:02d}'

  return {
    'show_name': [[show_name]],
    'episode_ref': [[episode_ref]],
    'air_day': [[air_day]],
    'air_time': [[air_time]],
    'episode_title': [[episode_title]],
  }


def get_variables_watching() -> dict[str, list[list[str]]]:
  """Fetch what the user is currently watching on Trakt.

  Returns variables: show_name, episode_ref, episode_title.
  For movies: episode_ref = 'MOVIE', episode_title = ''.
  All values are uppercased.

  Raises IntegrationDataUnavailableError if nothing is currently playing (204).
  """
  import config as _config_mod

  token = _get_token()
  client_id = _config_mod.get('trakt', 'client_id')

  r = requests.get(
    f'{_TRAKT_API_BASE}/users/me/watching',
    headers=_request_headers(token, client_id),
    timeout=10,
  )

  if r.status_code == 204:
    raise IntegrationDataUnavailableError('Nothing currently playing')

  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Trakt watching API error: {e.response.status_code} {e.response.reason}') from None

  data = r.json()
  media_type = data.get('type')

  if media_type == 'episode':
    show_name = data['show']['title'].upper()
    ep = data['episode']
    episode_ref = _format_episode_ref(ep['season'], ep['number'])
    episode_title = _strip_leading_article((ep.get('title') or '').upper())
  elif media_type == 'movie':
    show_name = data['movie']['title'].upper()
    episode_ref = 'MOVIE'
    episode_title = ''
  else:
    raise IntegrationDataUnavailableError(f'Unknown media type: {media_type!r}')

  return {
    'show_name': [[show_name]],
    'episode_ref': [[episode_ref]],
    'episode_title': [[episode_title]],
  }
