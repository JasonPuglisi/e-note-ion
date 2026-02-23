"""Integration tests for integrations/vestaboard.py — call the real Vestaboard API.

Run with: uv run pytest -m integration

Required env vars:
  VESTABOARD_VIRTUAL_API_KEY — Read/Write key for a virtual Vestaboard
                               (use a virtual board, not a physical one)
"""

import os

import pytest

import integrations.vestaboard as vb


@pytest.mark.integration
@pytest.mark.require_env('VESTABOARD_VIRTUAL_API_KEY')
def test_get_state_real_api(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_state() returns a valid VestaboardState from the live API."""
  monkeypatch.setenv('VESTABOARD_API_KEY', os.environ['VESTABOARD_VIRTUAL_API_KEY'])

  state = vb.get_state()

  assert isinstance(state.id, str) and state.id, 'state.id is empty'
  assert isinstance(state.appeared, str) and state.appeared, 'state.appeared is empty'
  assert isinstance(state.layout, list)
  assert len(state.layout) == vb.model.rows, f'layout has {len(state.layout)} rows, expected {vb.model.rows}'
  for row in state.layout:
    assert len(row) == vb.model.cols, f'row has {len(row)} cols, expected {vb.model.cols}'
    assert all(isinstance(code, int) for code in row), 'non-int code in row'


@pytest.mark.integration
@pytest.mark.require_env('VESTABOARD_VIRTUAL_API_KEY')
def test_set_state_real_api(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """set_state() successfully writes a message to the live virtual board."""
  monkeypatch.setenv('VESTABOARD_API_KEY', os.environ['VESTABOARD_VIRTUAL_API_KEY'])

  # Writes a fixed test message; no exception means success.
  vb.set_state([{'format': ['INTEGRATION TEST']}], {})
