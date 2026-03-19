# integrations/unraid.py
#
# Unraid server status integration.
#
# Fetches array capacity and system uptime from the Unraid GraphQL API
# (available on Unraid 7.2+). Connects over the local network — do not
# expose the Unraid API to the internet.
#
# Required config.toml keys ([unraid]):
#   url     — server base URL (e.g. "http://192.168.1.10")
#   api_key — API key from Settings → Management Access → API Keys
#
# Optional config.toml keys:
#   verify_tls — set to false to skip TLS certificate verification
#                (e.g. for self-signed certs). Default: true.

import logging
import warnings
from datetime import datetime, timezone

import requests
import urllib3

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry

logger = logging.getLogger(__name__)

# Cache TTL: 30 minutes. Array capacity and uptime change slowly.
_CACHE_TTL = 30 * 60

_cache: CacheEntry | None = None

_QUERY = """\
{
  info { os { uptime } }
  array {
    state
    capacity { kilobytes { used, total } }
  }
}"""


def _fmt_size(size_bytes: int) -> str:
  """Format a byte count as a human-readable TB/GB string (decimal/SI units).

  Uses TB (10**12) for >= 1 TB, GB (10**9) otherwise. Rounds to one decimal
  place and drops a trailing '.0' (e.g. 1.2 TB, 14 TB, 850 GB).
  """
  tb = size_bytes / 10**12
  if tb >= 1.0:
    rounded = round(tb, 1)
    if rounded == int(rounded):
      return f'{int(rounded)} TB'
    return f'{rounded} TB'
  gb = size_bytes / 10**9
  rounded = round(gb, 1)
  if rounded == int(rounded):
    return f'{int(rounded)} GB'
  return f'{rounded} GB'


def _fmt_uptime(seconds: int) -> str:
  """Format uptime as '#M #D #H' — months, days, hours.

  No weeks, nothing finer than hours, no zero-padding, skip zero-value
  components. Months are approximated as 30 days.
  """
  months, remainder = divmod(seconds, 30 * 24 * 3600)
  days, remainder = divmod(remainder, 24 * 3600)
  hours = remainder // 3600

  parts: list[str] = []
  if months:
    parts.append(f'{months}M')
  if days:
    parts.append(f'{days}D')
  # Always show hours if no other components, so bare "UP 0H" is possible.
  if hours or not parts:
    parts.append(f'{hours}H')

  return f'UP {" ".join(parts)}'


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch Unraid status and return variables for template rendering.

  Returns keys: header, capacity, uptime.
  Raises IntegrationDataUnavailableError when the API is unreachable.
  """
  global _cache

  import config as _cfg

  if _cache is not None and _cache.is_valid(_CACHE_TTL):
    logger.debug('Unraid: cache hit')
    return _cache.value

  base_url = _cfg.get('unraid', 'url').rstrip('/')
  api_key = _cfg.get('unraid', 'api_key')
  verify = _cfg.get_optional_bool('unraid', 'verify_tls', default=True)

  try:
    with warnings.catch_warnings():
      if not verify:
        warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)
      r = fetch_with_retry(
        'POST',
        f'{base_url}/graphql',
        headers={'x-api-key': api_key},
        json={'query': _QUERY},
        timeout=10,
        verify=verify,
      )
    r.raise_for_status()
  except requests.RequestException as e:
    if _cache is not None:
      logger.warning('Unraid: API request failed — serving stale cache — %s', e)
      return _cache.value
    raise IntegrationDataUnavailableError(f'Unraid: API request failed — {e}') from None

  data = r.json().get('data')
  if not data:
    errors = r.json().get('errors', [])
    msg = errors[0].get('message', 'unknown') if errors else 'no data'
    raise IntegrationDataUnavailableError(f'Unraid: GraphQL error — {msg}')

  # Uptime — the API returns a boot timestamp (ISO 8601), not seconds.
  uptime_raw = data.get('info', {}).get('os', {}).get('uptime')
  if uptime_raw is not None:
    try:
      boot_time = datetime.fromisoformat(str(uptime_raw).replace('Z', '+00:00'))
      uptime_secs = int((datetime.now(timezone.utc) - boot_time).total_seconds())
      uptime_line = _fmt_uptime(max(0, uptime_secs))
    except ValueError, TypeError:
      logger.warning('Unraid: could not parse uptime %r', uptime_raw)
      uptime_line = ''
  else:
    uptime_line = ''

  # Array
  array_state = data.get('array', {}).get('state', '').upper()
  kb = data.get('array', {}).get('capacity', {}).get('kilobytes', {})
  used = kb.get('used')
  total = kb.get('total')

  if array_state in ('STOPPED', 'DEGRADED'):
    capacity_line = f'[R] {array_state}'
  elif used is not None and total is not None:
    capacity_line = f'{_fmt_size(int(used) * 1000)} / {_fmt_size(int(total) * 1000)}'
  else:
    capacity_line = ''

  result: dict[str, list[list[str]]] = {
    'header': [['[O] UNRAID']],
    'capacity': [[capacity_line]],
    'uptime': [[uptime_line]],
  }

  _cache = CacheEntry(result)
  logger.debug('Unraid: array=%s, %s', array_state, uptime_line)
  return result
