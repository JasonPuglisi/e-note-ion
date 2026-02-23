"""Integration tests for integrations/bart.py — call the real BART API.

Run with: uv run pytest -m integration

Required env vars:
  BART_API_KEY  — free BART API key
"""

import os

import pytest

import config as _cfg
import integrations.bart as bart
import integrations.vestaboard as vb


@pytest.mark.integration
@pytest.mark.require_env('BART_API_KEY')
def test_get_variables_real_api(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns a valid variables dict from the live BART API."""
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'bart': {
        'api_key': os.environ['BART_API_KEY'],
        'station': 'MLPT',
        'line1_dest': 'DALY',
      }
    },
  )
  # Reset the color cache so each run fetches fresh data.
  bart._dest_color_cache = None

  result = bart.get_variables()

  assert 'station' in result, 'missing station key'
  assert 'line1' in result, 'missing line1 key'

  # station: single-option list containing a non-empty string.
  assert len(result['station']) == 1
  assert len(result['station'][0]) == 1
  assert result['station'][0][0], 'station name is empty'

  # line1: single-option list; rendered text fits within display width.
  assert len(result['line1']) == 1
  assert len(result['line1'][0]) == 1
  line = result['line1'][0][0]
  assert isinstance(line, str)
  assert vb.display_len(line) <= vb.model.cols, (
    f'line1 exceeds model cols ({vb.display_len(line)} > {vb.model.cols}): {line!r}'
  )
