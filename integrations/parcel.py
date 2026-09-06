# integrations/parcel.py
#
# Upcoming package delivery integration via Parcel (parcelapp.net).
#
# Fetches active deliveries from the Parcel REST API and returns the
# soonest-arriving package for display. Requires Parcel Premium and an
# API key generated at web.parcelapp.net.
#
# Required config.toml keys ([parcel]):
#   api_key — API key from web.parcelapp.net

import logging
from datetime import date, datetime

import requests

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, raise_for_credentials, user_agent

logger = logging.getLogger(__name__)

_API_URL = 'https://api.parcel.app/external/deliveries/'

# Cache TTL: 30 minutes. The Parcel API is server-cached and rate-limited
# to 20 requests/hour; a generous local TTL avoids unnecessary calls
# during cron + refresh cycles.
_CACHE_TTL = 30 * 60

_cache: CacheEntry | None = None

# Carrier code → color tag mapping. Prefix-matched for Amazon variants.
_CARRIER_COLORS: dict[str, str] = {
  'usps': '[B]',
  'ups': '[O]',
  'fedex': '[V]',
  'dhl': '[Y]',
  'ontrac': '[B]',
  'laser': '[B]',
}

# Amazon carrier codes all start with 'amzl'.
_AMAZON_PREFIX = 'amzl'
_AMAZON_COLOR = '[B]'

_DEFAULT_COLOR = '[O]'

# Parcel API status codes that represent active deliveries.
_ACTIVE_STATUSES = {2, 3, 4, 8}  # in transit, pickup, out for delivery, info received

# Status code for "out for delivery".
_OUT_FOR_DELIVERY = 4


def _carrier_color(carrier_code: str) -> str:
  """Return the display color tag for a carrier code."""
  code = carrier_code.lower()
  if code.startswith(_AMAZON_PREFIX):
    return _AMAZON_COLOR
  return _CARRIER_COLORS.get(code, _DEFAULT_COLOR)


def _detail_line(status_code: int, date_expected: str | None) -> str:
  """Build the detail line (row 3) from status and expected date."""
  if status_code == _OUT_FOR_DELIVERY:
    return 'DELIVERING'
  if not date_expected:
    return ''
  try:
    expected = datetime.strptime(date_expected[:10], '%Y-%m-%d').date()
  except ValueError:
    return ''
  today = date.today()
  delta = (expected - today).days
  if delta <= 0:
    return 'TODAY'
  if delta == 1:
    return 'TOMORROW'
  return f'IN {delta} DAYS'


def _sort_key(delivery: dict) -> tuple:
  """Sort key: soonest date first (nulls last), then alphabetical name."""
  date_expected = delivery.get('date_expected') or ''
  description = (delivery.get('description') or '').upper()
  # Empty dates sort after all real dates.
  return (0 if date_expected else 1, date_expected, description)


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch active deliveries and return variables for the soonest one.

  Returns keys: status_line, description, detail.
  Raises IntegrationDataUnavailableError when there are no active deliveries.
  """
  global _cache

  import config as _config_mod

  api_key = _config_mod.get('parcel', 'api_key')

  if _cache is not None and _cache.is_valid(_CACHE_TTL):
    logger.debug('Parcel: cache hit')
    return _cache.value

  try:
    r = fetch_with_retry(
      'GET',
      _API_URL,
      params={'filter_mode': 'active'},
      headers={'api-key': api_key},
      timeout=10,
    )
    r.raise_for_status()
  except requests.RequestException as e:
    logger.warning('Parcel: API request failed — %s', e)
    if _cache is not None:
      logger.debug('Parcel: serving stale cache after error')
      return _cache.value
    raise IntegrationDataUnavailableError(f'Parcel: API request failed — {e}') from None

  deliveries = r.json().get('deliveries', [])

  # Filter to active statuses only.
  active = [d for d in deliveries if d.get('status_code') in _ACTIVE_STATUSES]

  if not active:
    raise IntegrationDataUnavailableError('Parcel: no active deliveries', expected=True)

  active.sort(key=_sort_key)
  chosen = active[0]

  carrier_code = chosen.get('carrier_code') or ''
  color = _carrier_color(carrier_code)
  description = (chosen.get('description') or 'PACKAGE').upper()
  detail = _detail_line(
    chosen.get('status_code', 0),
    chosen.get('date_expected'),
  )

  result: dict[str, list[list[str]]] = {
    'status_line': [[f'{color} ON THE WAY']],
    'description': [[description]],
    'detail': [[detail]],
  }

  _cache = CacheEntry(result)
  logger.debug('Parcel: selected %r (%s)', description, carrier_code)
  return result


def preflight() -> None:
  """Validate the Parcel API key at startup (#503)."""
  import config as _config_mod

  api_key = _config_mod.get('parcel', 'api_key')
  r = fetch_with_retry('GET', _API_URL, headers={'api-key': api_key, 'User-Agent': user_agent()}, timeout=10)
  raise_for_credentials(r, 'parcel')
