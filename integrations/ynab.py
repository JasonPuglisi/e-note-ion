# integrations/ynab.py
#
# YNAB (You Need A Budget) net worth tracker.
#
# Fetches all account balances and current-month transactions from the
# YNAB API v1 to compute net worth and month-over-month percent change.
#
# Required config.toml keys ([ynab]):
#   api_key   — personal access token (free for all YNAB subscribers)
#
# Optional config.toml keys:
#   budget_id — budget UUID. Omit if you have only one budget (auto-detected).

import logging
from datetime import date

import requests

from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

_BASE_URL = 'https://api.ynab.com/v1'

# Cache TTL: 30 minutes. Account balances change infrequently.
_CACHE_TTL = 30 * 60

_cache: CacheEntry | None = None
_resolved_budget_id: str | None = None


def _headers(api_key: str) -> dict[str, str]:
  return {
    'Authorization': f'Bearer {api_key}',
    'User-Agent': user_agent(),
  }


def _fmt_dollars(milliunits: int) -> str:
  """Format milliunits as a dollar string.

  Rules:
  - Always round to whole dollars (no cents).
  - Under $10,000: full number with commas ($9,999).
  - $10,000–$999,999: K suffix with up to one decimal, drop .0 ($10K, $50.5K).
  - $1,000,000+: M suffix with up to one decimal, drop .0 ($1M, $1.5M).
  - $1,000,000,000+: B suffix with up to one decimal, drop .0 ($1B).
  - Negative values: prefix with - ($-5,000, $-50.5K).
  """
  dollars = round(milliunits / 1000)
  negative = dollars < 0
  abs_dollars = abs(dollars)
  prefix = '-' if negative else ''

  if abs_dollars >= 1_000_000_000:
    val = round(abs_dollars / 1_000_000_000, 1)
    if val >= 1000:
      # Overflow — shouldn't happen, but safety
      s = f'${prefix}{val:.0f}B'
    elif val == int(val):
      s = f'${prefix}{int(val)}B'
    else:
      s = f'${prefix}{val}B'
  elif abs_dollars >= 1_000_000:
    val = round(abs_dollars / 1_000_000, 1)
    if val >= 1000:
      # Rounded up to 1B
      s = f'${prefix}1B'
    elif val == int(val):
      s = f'${prefix}{int(val)}M'
    else:
      s = f'${prefix}{val}M'
  elif abs_dollars >= 10_000:
    val = round(abs_dollars / 1_000, 1)
    if val >= 1000:
      # Rounded up to 1M
      s = f'${prefix}1M'
    elif val == int(val):
      s = f'${prefix}{int(val)}K'
    else:
      s = f'${prefix}{val}K'
  else:
    s = f'${prefix}{abs_dollars:,}'

  return s


def _fmt_pct(delta: int, start: int) -> str:
  """Format month-over-month change as a percent string.

  Returns e.g. '+2.6%', '-0.3%', '+0%'. One decimal, drop .0.
  Falls back to '+$0' when start-of-month net worth is zero.
  """
  if start == 0:
    sign = '+' if delta >= 0 else '-'
    return f'{sign}$0'

  pct = (delta / start) * 100
  rounded = round(pct, 1)

  if rounded == 0:
    return '+0%'

  sign = '+' if rounded > 0 else ''
  if rounded == int(rounded):
    return f'{sign}{int(rounded)}%'
  return f'{sign}{rounded}%'


def _resolve_budget_id(api_key: str) -> str:
  """Return the budget ID from config, or auto-detect if only one budget exists."""
  global _resolved_budget_id

  import config as _config_mod

  explicit = _config_mod.get_optional('ynab', 'budget_id')
  if explicit:
    return explicit

  if _resolved_budget_id is not None:
    return _resolved_budget_id

  try:
    resp = fetch_with_retry(
      'GET',
      f'{_BASE_URL}/budgets',
      headers=_headers(api_key),
      timeout=10,
    )
    resp.raise_for_status()
  except requests.RequestException as e:
    raise IntegrationDataUnavailableError(f'YNAB: failed to list budgets — {e}') from None

  budgets = [b for b in resp.json().get('data', {}).get('budgets', []) if not b.get('deleted')]
  if not budgets:
    raise IntegrationDataUnavailableError('YNAB: no budgets found')
  if len(budgets) > 1:
    names = ', '.join(b.get('name', b['id']) for b in budgets)
    raise IntegrationDataUnavailableError(f'YNAB: multiple budgets found ({names}) — set budget_id in config.toml')

  bid: str = budgets[0]['id']
  _resolved_budget_id = bid
  logger.info('YNAB: auto-detected budget %s (%s)', budgets[0].get('name', ''), bid)
  return bid


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch YNAB data and return variables for template rendering.

  Returns keys: header, amount, delta.
  Raises IntegrationDataUnavailableError when the API is unreachable.
  """
  global _cache

  import config as _config_mod

  if _cache is not None and _cache.is_valid(_CACHE_TTL):
    logger.debug('YNAB: cache hit')
    return _cache.value

  api_key = _config_mod.get('ynab', 'api_key')
  budget_id = _resolve_budget_id(api_key)
  hdrs = _headers(api_key)

  # Fetch accounts
  try:
    accounts_resp = fetch_with_retry(
      'GET',
      f'{_BASE_URL}/budgets/{budget_id}/accounts',
      headers=hdrs,
      timeout=10,
    )
    accounts_resp.raise_for_status()
  except requests.RequestException as e:
    if _cache is not None:
      logger.warning('YNAB: accounts request failed — serving stale cache — %s', e)
      return _cache.value
    raise IntegrationDataUnavailableError(f'YNAB: accounts request failed — {e}') from None

  accounts_data = accounts_resp.json().get('data', {}).get('accounts', [])

  # Sum all non-closed, non-deleted account balances (milliunits)
  net_worth = sum(a['balance'] for a in accounts_data if not a.get('closed') and not a.get('deleted'))

  # Fetch current month transactions for delta
  first_of_month = date.today().replace(day=1).isoformat()
  try:
    txn_resp = fetch_with_retry(
      'GET',
      f'{_BASE_URL}/budgets/{budget_id}/transactions',
      headers=hdrs,
      params={'since_date': first_of_month},
      timeout=10,
    )
    txn_resp.raise_for_status()
  except requests.RequestException as e:
    if _cache is not None:
      logger.warning('YNAB: transactions request failed — serving stale cache — %s', e)
      return _cache.value
    raise IntegrationDataUnavailableError(f'YNAB: transactions request failed — {e}') from None

  transactions = txn_resp.json().get('data', {}).get('transactions', [])

  # Monthly delta = sum of all non-deleted transaction amounts
  monthly_delta = sum(t['amount'] for t in transactions if not t.get('deleted'))

  # Start-of-month net worth = current - delta
  start_of_month = net_worth - monthly_delta

  # Format
  color = '[G]' if monthly_delta >= 0 else '[R]'
  if net_worth < 0 and monthly_delta >= 0:
    color = '[R]'

  amount_line = _fmt_dollars(net_worth)
  month_abbr = date.today().strftime('%b').upper()
  pct_line = f'{_fmt_pct(monthly_delta, start_of_month)} / {month_abbr}'

  result: dict[str, list[list[str]]] = {
    'header': [[f'{color} NET WORTH']],
    'amount': [[amount_line]],
    'delta': [[pct_line]],
  }

  _cache = CacheEntry(result)
  logger.debug('YNAB: net_worth=%s, delta=%s', amount_line, pct_line)
  return result
