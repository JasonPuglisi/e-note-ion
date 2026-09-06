import os
from pathlib import Path
from typing import Generator

import pytest

import health as _health_mod
import integrations.vestaboard as vestaboard


@pytest.fixture(autouse=True)
def reset_vestaboard_model() -> Generator[None, None, None]:
  """Reset the active board model to NOTE before every test."""
  original = vestaboard.model
  yield
  vestaboard.model = original


@pytest.fixture(autouse=True)
def _isolate_health_log(tmp_path: Path) -> None:
  """Point the health log at a temp directory for every test.

  Lives here rather than in tests/core/ so it covers the whole suite: health
  state persists through data/health.jsonl, so without this a test that records
  an error writes it to the real file and the *next* test's health.init()
  loads it back, producing failures that depend on execution order.
  """
  _health_mod._LOG_DIR = tmp_path
  _health_mod._LOG_PATH = tmp_path / 'health.jsonl'


@pytest.fixture(autouse=True)
def _reset_health_state() -> Generator[None, None, None]:
  """Reset health state and stop any periodic log timers after each test."""
  yield
  _health_mod.reset()


@pytest.fixture
def require_env(request: pytest.FixtureRequest) -> None:
  """Skip the test if any env vars listed in @pytest.mark.require_env are unset."""
  marker = request.node.get_closest_marker('require_env')
  if marker is None:
    return
  for var in marker.args:
    if not os.environ.get(var, '').strip():
      pytest.skip(f'{var!r} not set')
