"""Integration tests for integrations/parcel.py.

Requires a Parcel Premium API key.

Run with: uv run pytest -m integration -k parcel -v

Required env vars:
  PARCEL_API_KEY — API key from web.parcelapp.net
"""

import os

import pytest

import config as _config_mod
import integrations.parcel as pc
from exceptions import IntegrationDataUnavailableError


@pytest.mark.integration
@pytest.mark.require_env('PARCEL_API_KEY')
def test_get_variables_or_no_deliveries(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid variables or raises for no active deliveries.

  Both outcomes are valid — the user may or may not have packages in transit.
  The test verifies the API call succeeds either way.
  """
  monkeypatch.setattr(
    _config_mod,
    '_config',
    {'parcel': {'api_key': os.environ['PARCEL_API_KEY']}},
  )
  pc._cache = None

  try:
    result = pc.get_variables()
  except IntegrationDataUnavailableError as e:
    # No active deliveries — valid state.
    assert 'no active deliveries' in str(e)
    pc._cache = None
    return

  # Active deliveries found — verify shape.
  assert set(result.keys()) == {'status_line', 'description', 'detail'}
  for key in ('status_line', 'description', 'detail'):
    assert len(result[key]) == 1, f'{key}: expected 1 option'
    assert len(result[key][0]) == 1, f'{key}: expected 1 line'

  status_line = result['status_line'][0][0]
  assert 'ON THE WAY' in status_line, f'unexpected status_line: {status_line!r}'
  assert status_line.startswith('['), f'status_line missing color tag: {status_line!r}'

  description = result['description'][0][0]
  assert description, 'description is empty'

  pc._cache = None
