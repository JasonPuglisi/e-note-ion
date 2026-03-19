"""Unit tests for integrations/ynab.py."""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

import integrations.ynab as ynab
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accounts_response(accounts: list[dict]) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = {'data': {'accounts': accounts}}
  resp.raise_for_status = MagicMock()
  return resp


def _txn_response(transactions: list[dict]) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = {'data': {'transactions': transactions}}
  resp.raise_for_status = MagicMock()
  return resp


def _account(balance: int, *, closed: bool = False, deleted: bool = False) -> dict:
  return {'balance': balance, 'closed': closed, 'deleted': deleted}


def _txn(amount: int, *, deleted: bool = False) -> dict:
  return {'amount': amount, 'deleted': deleted}


def _budgets_response(budgets: list[dict]) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = {'data': {'budgets': budgets}}
  resp.raise_for_status = MagicMock()
  return resp


def _patched_config(*, include_budget_id: bool = True) -> dict:
  cfg: dict = {'ynab': {'api_key': 'test-key'}}
  if include_budget_id:
    cfg['ynab']['budget_id'] = 'test-budget-id'
  return cfg


def _mock_fetch(accounts: list[dict], transactions: list[dict]):
  """Return a side_effect function that returns accounts then transactions."""
  responses = [_accounts_response(accounts), _txn_response(transactions)]
  return MagicMock(side_effect=responses)


# ---------------------------------------------------------------------------
# _fmt_dollars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'milliunits,expected',
  [
    (0, '$0'),
    (1_000_000, '$1,000'),
    (5_500_000, '$5,500'),
    (9_999_000, '$9,999'),
    (10_000_000, '$10K'),
    (10_500_000, '$10.5K'),
    (50_000_000, '$50K'),
    (50_500_000, '$50.5K'),
    (100_000_000, '$100K'),
    (124_832_000, '$124.8K'),
    (999_900_000, '$999.9K'),
    (999_950_000, '$1M'),
    (1_000_000_000, '$1M'),
    (1_200_000_000, '$1.2M'),
    (1_500_000_000, '$1.5M'),
    (10_000_000_000, '$10M'),
    (999_950_000_000, '$1B'),
    (1_000_000_000_000, '$1B'),
    (1_500_000_000_000, '$1.5B'),
    # Negative
    (-5_000_000, '$-5,000'),
    (-50_500_000, '$-50.5K'),
    (-1_200_000_000, '$-1.2M'),
    # Rounding to whole dollars
    (1_499, '$1'),
    (1_500, '$2'),
  ],
)
def test_fmt_dollars(milliunits: int, expected: str) -> None:
  assert ynab._fmt_dollars(milliunits) == expected


# ---------------------------------------------------------------------------
# _fmt_pct
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'delta,start,expected',
  [
    (2600, 100_000, '+2.6%'),
    (-300, 100_000, '-0.3%'),
    (3000, 100_000, '+3%'),
    (0, 100_000, '+0%'),
    (100, 0, '+$0'),
    (-100, 0, '-$0'),
    (100_000, 100_000, '+100%'),
    (-50_000, 100_000, '-50%'),
  ],
)
def test_fmt_pct(delta: int, start: int, expected: str) -> None:
  assert ynab._fmt_pct(delta, start) == expected


# ---------------------------------------------------------------------------
# get_variables
# ---------------------------------------------------------------------------


def test_get_variables_positive_delta(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  accounts = [_account(100_000_000), _account(50_000_000)]  # $100K + $50K
  transactions = [_txn(5_000_000)]  # +$5K this month

  with patch('integrations.ynab.fetch_with_retry', _mock_fetch(accounts, transactions)):
    result = ynab.get_variables()

  assert result['header'] == [['[G] NET WORTH']]
  assert result['amount'] == [['$150K']]
  assert '/' in result['delta'][0][0]
  assert result['delta'][0][0].startswith('+')
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_get_variables_negative_delta(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  accounts = [_account(100_000_000)]
  transactions = [_txn(-10_000_000)]  # -$10K this month

  with patch('integrations.ynab.fetch_with_retry', _mock_fetch(accounts, transactions)):
    result = ynab.get_variables()

  assert result['header'] == [['[R] NET WORTH']]
  assert result['delta'][0][0].startswith('-')
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_get_variables_zero_start_of_month(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  # Net worth = $5K, all from this month's transactions
  accounts = [_account(5_000_000)]
  transactions = [_txn(5_000_000)]

  with patch('integrations.ynab.fetch_with_retry', _mock_fetch(accounts, transactions)):
    result = ynab.get_variables()

  # start_of_month = 5000 - 5000 = 0 → fallback format
  assert '+$0' in result['delta'][0][0]
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_get_variables_negative_net_worth(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  accounts = [_account(-20_000_000)]  # -$20K (liabilities > assets)
  transactions = [_txn(1_000_000)]  # +$1K this month

  with patch('integrations.ynab.fetch_with_retry', _mock_fetch(accounts, transactions)):
    result = ynab.get_variables()

  assert result['header'] == [['[R] NET WORTH']]
  assert result['amount'] == [['$-20K']]
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_get_variables_filters_closed_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  accounts = [
    _account(100_000_000),
    _account(50_000_000, closed=True),
    _account(25_000_000, deleted=True),
  ]
  transactions = [_txn(1_000_000), _txn(-500_000, deleted=True)]

  with patch('integrations.ynab.fetch_with_retry', _mock_fetch(accounts, transactions)):
    result = ynab.get_variables()

  # Only the first account ($100K) should count
  assert result['amount'] == [['$100K']]
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_get_variables_large_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  accounts = [_account(1_500_000_000)]  # $1.5M
  transactions = [_txn(50_000_000)]  # +$50K

  with patch('integrations.ynab.fetch_with_retry', _mock_fetch(accounts, transactions)):
    result = ynab.get_variables()

  assert result['amount'] == [['$1.5M']]
  ynab._cache = None
  ynab._resolved_budget_id = None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_api_error_no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  ynab._cache = None
  ynab._resolved_budget_id = None

  with patch(
    'integrations.ynab.fetch_with_retry',
    side_effect=_requests.ConnectionError('refused'),
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='accounts request failed'):
      ynab.get_variables()

  ynab._cache = None
  ynab._resolved_budget_id = None


def test_api_error_serves_stale_cache(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  stale_value: dict = {
    'header': [['[G] NET WORTH']],
    'amount': [['$100K']],
    'delta': [['+5% / MAR']],
  }
  ynab._cache = CacheEntry(stale_value)
  ynab._cache.cached_at = time.monotonic() - ynab._CACHE_TTL - 1

  with patch(
    'integrations.ynab.fetch_with_retry',
    side_effect=_requests.ConnectionError('refused'),
  ):
    result = ynab.get_variables()

  assert result == stale_value
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_txn_error_serves_stale_cache(monkeypatch: pytest.MonkeyPatch) -> None:
  """Transaction fetch fails after accounts succeed — stale cache returned."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  stale_value: dict = {
    'header': [['[G] NET WORTH']],
    'amount': [['$50K']],
    'delta': [['+2% / JAN']],
  }
  ynab._cache = CacheEntry(stale_value)
  ynab._cache.cached_at = time.monotonic() - ynab._CACHE_TTL - 1

  # Accounts succeed, transactions fail
  accounts_resp = _accounts_response([_account(50_000_000)])
  call_count = 0

  def side_effect(*args: object, **kwargs: object) -> MagicMock:  # noqa: ARG001
    nonlocal call_count
    if call_count == 0:
      call_count += 1
      return accounts_resp
    raise _requests.ConnectionError('refused')

  with patch('integrations.ynab.fetch_with_retry', side_effect=side_effect):
    result = ynab.get_variables()

  assert result == stale_value
  ynab._cache = None
  ynab._resolved_budget_id = None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  cached_value: dict = {
    'header': [['[G] NET WORTH']],
    'amount': [['$100K']],
    'delta': [['+3% / MAR']],
  }
  ynab._cache = CacheEntry(cached_value)

  with patch('integrations.ynab.fetch_with_retry') as mock_fetch:
    result = ynab.get_variables()
    mock_fetch.assert_not_called()

  assert result == cached_value
  ynab._cache = None
  ynab._resolved_budget_id = None


# ---------------------------------------------------------------------------
# Budget auto-detection
# ---------------------------------------------------------------------------


def test_auto_detect_single_budget(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(include_budget_id=False))
  ynab._cache = None
  ynab._resolved_budget_id = None

  budgets_resp = _budgets_response([{'id': 'auto-id', 'name': 'My Budget'}])
  accounts = [_account(50_000_000)]
  transactions = [_txn(1_000_000)]

  responses = [budgets_resp, _accounts_response(accounts), _txn_response(transactions)]

  with patch('integrations.ynab.fetch_with_retry', MagicMock(side_effect=responses)):
    result = ynab.get_variables()

  assert result['amount'] == [['$50K']]
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_auto_detect_multiple_budgets_errors(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(include_budget_id=False))
  ynab._cache = None
  ynab._resolved_budget_id = None

  budgets_resp = _budgets_response(
    [
      {'id': 'id-1', 'name': 'Budget A'},
      {'id': 'id-2', 'name': 'Budget B'},
    ]
  )

  with patch('integrations.ynab.fetch_with_retry', return_value=budgets_resp):
    with pytest.raises(IntegrationDataUnavailableError, match='multiple budgets'):
      ynab.get_variables()

  ynab._cache = None
  ynab._resolved_budget_id = None


def test_auto_detect_no_budgets_errors(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(include_budget_id=False))
  ynab._cache = None
  ynab._resolved_budget_id = None

  budgets_resp = _budgets_response([])

  with patch('integrations.ynab.fetch_with_retry', return_value=budgets_resp):
    with pytest.raises(IntegrationDataUnavailableError, match='no budgets found'):
      ynab.get_variables()

  ynab._cache = None
  ynab._resolved_budget_id = None


def test_auto_detect_cached_on_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
  """Second call should reuse the resolved budget ID, not hit /budgets again."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(include_budget_id=False))
  ynab._cache = None
  ynab._resolved_budget_id = None

  budgets_resp = _budgets_response([{'id': 'auto-id', 'name': 'My Budget'}])
  accounts = [_account(10_000_000)]
  transactions = [_txn(0)]

  # First call: budgets + accounts + transactions = 3 requests
  responses_1 = [budgets_resp, _accounts_response(accounts), _txn_response(transactions)]
  with patch('integrations.ynab.fetch_with_retry', MagicMock(side_effect=responses_1)):
    ynab.get_variables()

  # Expire the data cache but keep the resolved budget ID
  ynab._cache = None

  # Second call: only accounts + transactions = 2 requests (no /budgets)
  responses_2 = [_accounts_response(accounts), _txn_response(transactions)]
  mock = MagicMock(side_effect=responses_2)
  with patch('integrations.ynab.fetch_with_retry', mock):
    ynab.get_variables()

  assert mock.call_count == 2
  ynab._cache = None
  ynab._resolved_budget_id = None


def test_auto_detect_skips_deleted_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config(include_budget_id=False))
  ynab._cache = None
  ynab._resolved_budget_id = None

  budgets_resp = _budgets_response(
    [
      {'id': 'deleted-id', 'name': 'Old Budget', 'deleted': True},
      {'id': 'active-id', 'name': 'Active Budget'},
    ]
  )
  accounts = [_account(10_000_000)]
  transactions = [_txn(0)]

  responses = [budgets_resp, _accounts_response(accounts), _txn_response(transactions)]
  with patch('integrations.ynab.fetch_with_retry', MagicMock(side_effect=responses)):
    result = ynab.get_variables()

  assert result['amount'] == [['$10K']]
  ynab._cache = None
  ynab._resolved_budget_id = None
