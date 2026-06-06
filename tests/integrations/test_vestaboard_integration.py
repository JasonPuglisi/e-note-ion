"""Integration tests for integrations/vestaboard.py — call the real Vestaboard API.

Run with: uv run pytest -m integration

Required env vars:
  VESTABOARD_VIRTUAL_API_KEY — Read/Write key for a virtual Vestaboard
                               (use a virtual board, not a physical one)
"""

import os
import time

import pytest

import config as _config_mod
import integrations.vestaboard as vb


@pytest.mark.integration
@pytest.mark.require_env('VESTABOARD_VIRTUAL_API_KEY')
def test_set_state_real_api(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """set_state() successfully writes a message to the live virtual board."""
  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': os.environ['VESTABOARD_VIRTUAL_API_KEY']}})

  # Make each run's content unique so the board doesn't 409 on duplicate
  # content. Nanosecond entropy avoids collisions even when two concurrent
  # main pushes run this suite against the same virtual board at once — the
  # old `int(time.time()) % 10000` collided when both hit the same second.
  token = time.time_ns() % 10**8
  try:
    vb.set_state([{'format': [f'TEST {token}']}], {})
  except vb.DuplicateContentError:
    # A 409 still proves set_state reached the API and the payload was
    # accepted — the board just already shows this exact content. That's a
    # pass for a write-path smoke test, so don't let it flake the suite.
    pass


@pytest.mark.integration
@pytest.mark.require_env('VESTABOARD_VIRTUAL_API_KEY')
def test_get_state_real_api(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_state() returns a valid VestaboardState from the live API.

  Relies on test_set_state_real_api having run first so the board has state.
  """
  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': os.environ['VESTABOARD_VIRTUAL_API_KEY']}})

  state = vb.get_state()

  assert isinstance(state.id, str) and state.id, 'state.id is empty'
  assert state.appeared is not None, 'state.appeared is missing'
  assert isinstance(state.layout, list)
  assert len(state.layout) == vb.model.rows, f'layout has {len(state.layout)} rows, expected {vb.model.rows}'
  for row in state.layout:
    assert len(row) == vb.model.cols, f'row has {len(row)} cols, expected {vb.model.cols}'
    assert all(isinstance(code, int) for code in row), 'non-int code in row'
