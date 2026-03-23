# integrations/qbittorrent.py
#
# qBittorrent seeding stats integration.
#
# Fetches the list of actively seeding torrents from the qBittorrent Web API
# v2 and returns the count and total size for display. Connects over the local
# network — do not expose the qBittorrent Web UI to the internet.
#
# Required config.toml keys ([qbittorrent]):
#   url      — Web UI base URL (e.g. "http://192.168.1.50:8080")
#   username — Web UI username
#   password — Web UI password
#
# Optional config.toml keys:
#   verify_tls — set to false to skip TLS certificate verification
#                (e.g. for self-signed certs). Default: true.

import logging
import warnings

import requests
import urllib3

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry

logger = logging.getLogger(__name__)

# Cache TTL: 30 minutes. Seeding stats change slowly.
_CACHE_TTL = 30 * 60

_cache: CacheEntry | None = None


def _fmt_size(size_bytes: int) -> str:
  """Format a byte count as a human-readable size string.

  Uses TB for >= 1 TB, GB otherwise. Rounds to one decimal place and drops
  a trailing '.0' (e.g. 1.2 TB, 14 TB, 850 GB).
  """
  tb = size_bytes / (1024**4)
  if tb >= 1.0:
    rounded = round(tb, 1)
    if rounded == int(rounded):
      return f'{int(rounded)} TB'
    return f'{rounded} TB'
  gb = size_bytes / (1024**3)
  rounded = round(gb, 1)
  if rounded == int(rounded):
    return f'{int(rounded)} GB'
  return f'{rounded} GB'


def _login(base_url: str, username: str, password: str, *, verify: bool = True) -> requests.Session:
  """Authenticate with the qBittorrent Web API and return a session."""
  session = requests.Session()
  session.verify = verify
  r = session.post(
    f'{base_url}/api/v2/auth/login',
    data={'username': username, 'password': password},
    timeout=10,
  )
  r.raise_for_status()
  if r.text.strip() != 'Ok.':
    raise IntegrationDataUnavailableError('qBittorrent: login failed — check credentials')
  return session


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch seeding stats and return variables for template rendering.

  Returns keys: header, count, size.
  Raises IntegrationDataUnavailableError when there are no seeding torrents
  or the API is unreachable.
  """
  global _cache

  import config as _config_mod

  if _cache is not None and _cache.is_valid(_CACHE_TTL):
    logger.debug('qBittorrent: cache hit')
    return _cache.value

  base_url = _config_mod.get('qbittorrent', 'url').rstrip('/')
  username = _config_mod.get('qbittorrent', 'username')
  password = _config_mod.get('qbittorrent', 'password')
  verify = _config_mod.get_optional_bool('qbittorrent', 'verify_tls', default=True)

  try:
    with warnings.catch_warnings():
      if not verify:
        warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
      session = _login(base_url, username, password, verify=verify)
  except requests.RequestException as e:
    if _cache is not None:
      logger.warning('qBittorrent: login failed — serving stale cache — %s', e)
      return _cache.value
    raise IntegrationDataUnavailableError(f'qBittorrent: login failed — {e}') from None

  try:
    with warnings.catch_warnings():
      if not verify:
        warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
      r = fetch_with_retry(
        'GET',
        f'{base_url}/api/v2/torrents/info',
        params={'filter': 'seeding'},
        cookies=session.cookies,
        timeout=10,
        verify=verify,
      )
    r.raise_for_status()
  except requests.RequestException as e:
    if _cache is not None:
      logger.warning('qBittorrent: API request failed — serving stale cache — %s', e)
      return _cache.value
    raise IntegrationDataUnavailableError(f'qBittorrent: API request failed — {e}') from None

  torrents = r.json()
  if not torrents:
    raise IntegrationDataUnavailableError('qBittorrent: no seeding torrents')

  count = len(torrents)
  total_size = sum(t.get('size', 0) for t in torrents)

  result: dict[str, list[list[str]]] = {
    'header': [['[B] TORRENTS']],
    'count': [[f'{count} SEEDING']],
    'size': [[_fmt_size(total_size)]],
  }

  _cache = CacheEntry(result)
  logger.debug('qBittorrent: %d seeding, %s', count, _fmt_size(total_size))
  return result
