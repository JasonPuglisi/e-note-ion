# integrations/google.py
#
# Shared Google OAuth 2.0 device code flow for Google API integrations.
#
# Handles authentication, token storage, and automatic refresh. Any
# integration that needs a Google API token calls get_token() with the
# required OAuth scope. Tokens are stored in the [google] section of
# config.toml and shared across all Google integrations.
#
# If additional scopes are needed (e.g. a new integration), delete the
# stored tokens and restart — the auth flow re-initiates with the new scope.
#
# Required config.toml keys ([google]):
#   client_id     — from Google Cloud Console (OAuth 2.0 Client ID, TV/device type)
#   client_secret — from the same client
#
# Written by auth flow (do not edit manually):
#   access_token  — current OAuth access token
#   refresh_token — token used to obtain a new access token
#   expires_at    — Unix timestamp when access_token expires

import logging
import threading
import time

import requests

from exceptions import IntegrationDataUnavailableError
from integrations.http import user_agent

logger = logging.getLogger(__name__)

_DEVICE_CODE_URL = 'https://oauth2.googleapis.com/device/code'
_TOKEN_URL = 'https://oauth2.googleapis.com/token'  # nosec B105 — URL, not a password

# Prevents multiple concurrent auth background threads.
_auth_started = False
_auth_lock = threading.Lock()

# Scope requested during the most recent auth flow attempt, set by
# get_token() so _run_auth_flow() knows which scope to request.
_pending_scope: str = ''


# --- Token management ---


def _store_tokens(tokens: dict) -> None:
  """Write access_token, refresh_token, and expires_at to config.toml."""
  import config as _config_mod

  expires_at = int(time.time()) + tokens.get('expires_in', 3600)
  values: dict[str, str | int] = {
    'access_token': tokens['access_token'],
    'expires_at': expires_at,
  }
  if 'refresh_token' in tokens:
    values['refresh_token'] = tokens['refresh_token']
  _config_mod.write_section_values('google', values)


def _refresh_token() -> None:
  """Exchange the current refresh token for a new access token."""
  import config as _config_mod

  logger.debug('Google: refreshing access token')
  client_id = _config_mod.get('google', 'client_id')
  client_secret = _config_mod.get('google', 'client_secret')
  refresh_token = _config_mod.get('google', 'refresh_token')

  r = requests.post(
    _TOKEN_URL,
    data={
      'client_id': client_id,
      'client_secret': client_secret,
      'refresh_token': refresh_token,
      'grant_type': 'refresh_token',
    },
    headers={'User-Agent': user_agent()},
    timeout=10,
  )
  try:
    r.raise_for_status()
  except requests.HTTPError:
    raise requests.HTTPError(f'Google token refresh failed: {r.status_code} {r.reason}') from None
  _store_tokens(r.json())
  logger.debug('Google: token refreshed successfully')


def _clear_tokens() -> None:
  """Clear stored tokens from config (in-memory and on disk)."""
  import config as _config_mod

  _config_mod.write_section_values(
    'google',
    {'access_token': '', 'refresh_token': '', 'expires_at': ''},  # nosec B105 — empty strings intentionally clear stored tokens
  )


def get_token(scope: str) -> str:
  """Return a valid Google access token, refreshing if within 5 minutes of expiry.

  *scope* is used only when triggering a new device code auth flow (no tokens
  stored yet or refresh failed). It is not checked against existing tokens —
  if a scope change is needed, clear the stored tokens and restart.

  Raises IntegrationDataUnavailableError if auth is pending or refresh fails.
  """
  import config as _config_mod

  access_token = _config_mod.get_optional('google', 'access_token')
  if not access_token:
    _ensure_authenticated(scope)
    raise IntegrationDataUnavailableError('Google auth pending — check logs for instructions')

  expires_at_str = _config_mod.get_optional('google', 'expires_at')
  if expires_at_str:
    try:
      expires_at = int(expires_at_str)
    except ValueError:
      pass  # malformed expires_at — proceed with current token
    else:
      secs_remaining = expires_at - time.time()
      if secs_remaining < 300:
        logger.debug('Google: access token expires in %.0fs — triggering refresh', secs_remaining)
        try:
          _refresh_token()
        except requests.HTTPError as e:
          logger.warning(
            'Google: token refresh failed (%s) — clearing tokens and re-starting '
            'auth flow. Check logs for the new device code and URL.',
            e,
          )
          _clear_tokens()
          _ensure_authenticated(scope)
          raise IntegrationDataUnavailableError(
            'Google auth pending — token refresh failed, re-authentication required'
          ) from None
        access_token = _config_mod.get_optional('google', 'access_token')

  return access_token


# --- OAuth device code flow ---


def _run_auth_flow() -> None:
  """Background thread: Google OAuth device code flow → writes tokens to config.toml."""
  import config as _config_mod

  try:
    client_id = _config_mod.get('google', 'client_id')
    client_secret = _config_mod.get('google', 'client_secret')

    r = requests.post(
      _DEVICE_CODE_URL,
      data={'client_id': client_id, 'scope': _pending_scope},
      headers={'User-Agent': user_agent()},
      timeout=10,
    )
    r.raise_for_status()
    data = r.json()

    device_code = data['device_code']
    user_code = data['user_code']
    verification_url = data['verification_url']
    interval: int = data.get('interval', 5)
    expires_in: int = data.get('expires_in', 1800)

    logger.info('Google auth required. Go to %s and enter: %s', verification_url, user_code)

    deadline = time.time() + expires_in
    poll_interval = interval

    while time.time() < deadline:
      time.sleep(poll_interval)
      r = requests.post(
        _TOKEN_URL,
        data={
          'client_id': client_id,
          'client_secret': client_secret,
          'device_code': device_code,
          'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        },
        headers={'User-Agent': user_agent()},
        timeout=10,
      )
      if r.status_code == 200:
        _store_tokens(r.json())
        logger.info('Google auth successful. Tokens saved to config.toml.')
        return

      error = r.json().get('error', '')
      if error == 'authorization_pending':
        logger.debug('Google: auth pending — waiting for user approval (poll_interval=%ds)', poll_interval)
        continue
      elif error == 'slow_down':
        poll_interval = poll_interval + 5
        logger.debug('Google: auth rate-limited — backing off to %ds', poll_interval)
      elif error in ('access_denied', 'expired_token'):
        logger.error('Google auth %s — restart the container to try again.', error.replace('_', ' '))
        return
      else:
        logger.error('Google auth error: %s', error)
        return

    logger.error('Google auth timed out — restart the container to try again.')
  except Exception as e:  # noqa: BLE001
    logger.error('Error during Google auth: %s', e)


def preflight(scope: str) -> None:
  """Called at startup by integrations that need Google auth.

  Initiates the auth flow if tokens are absent. Proactively refreshes
  near-expiry tokens before any poll fires.
  """
  import config as _config_mod

  access_token = _config_mod.get_optional('google', 'access_token')
  if not access_token:
    _ensure_authenticated(scope)
    return

  expires_at_str = _config_mod.get_optional('google', 'expires_at')
  if expires_at_str:
    try:
      expires_at = int(expires_at_str)
    except ValueError:
      return
    secs_remaining = expires_at - time.time()
    if secs_remaining < 300:
      logger.info('Google: access token expires in %.0fs — refreshing at startup', secs_remaining)
      try:
        _refresh_token()
      except requests.HTTPError as e:
        logger.warning('Google: startup token refresh failed (%s) — clearing tokens', e)
        _clear_tokens()
        _ensure_authenticated(scope)


def _ensure_authenticated(scope: str) -> None:
  """Start the auth background thread if not already started."""
  global _auth_started, _pending_scope
  with _auth_lock:
    if _auth_started:
      logger.debug('Google: auth flow already in progress — not starting another')
      return
    _auth_started = True
    _pending_scope = scope
  logger.debug('Google: starting auth background thread')
  threading.Thread(target=_run_auth_flow, daemon=True, name='google-auth').start()
