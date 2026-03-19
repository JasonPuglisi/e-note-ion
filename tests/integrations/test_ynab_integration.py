"""Integration tests for integrations/ynab.py.

Requires a YNAB personal access token.

Run with: uv run pytest -m integration -k ynab -v

Required env vars:
  YNAB_API_KEY   — personal access token from YNAB Settings → Developer Settings

Optional env vars:
  YNAB_BUDGET_ID — budget UUID (auto-detected when you have one budget)
"""

import os

import pytest

import config as _cfg
import integrations.ynab as ynab


@pytest.mark.integration
@pytest.mark.require_env('YNAB_API_KEY')
def test_get_variables(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid net worth data from the real YNAB API."""
  cfg: dict = {'ynab': {'api_key': os.environ['YNAB_API_KEY']}}
  budget_id = os.environ.get('YNAB_BUDGET_ID', '').strip()
  if budget_id:
    cfg['ynab']['budget_id'] = budget_id

  monkeypatch.setattr(_cfg, '_config', cfg)
  ynab._cache = None
  ynab._resolved_budget_id = None

  result = ynab.get_variables()

  assert set(result.keys()) == {'header', 'amount', 'delta'}
  for key in ('header', 'amount', 'delta'):
    assert len(result[key]) == 1, f'{key}: expected 1 option'
    assert len(result[key][0]) == 1, f'{key}: expected 1 line'

  header = result['header'][0][0]
  assert 'NET WORTH' in header, f'unexpected header: {header!r}'
  assert header.startswith('['), f'header missing color tag: {header!r}'

  amount = result['amount'][0][0]
  assert amount.startswith('$'), f'amount missing $ prefix: {amount!r}'

  delta = result['delta'][0][0]
  assert '/' in delta, f'delta missing month separator: {delta!r}'

  ynab._cache = None
  ynab._resolved_budget_id = None
