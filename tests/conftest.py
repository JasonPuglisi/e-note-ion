import os
from typing import Generator

import pytest

import integrations.vestaboard as vestaboard


@pytest.fixture(autouse=True)
def reset_vestaboard_model() -> Generator[None, None, None]:
  """Reset the active board model to NOTE before every test."""
  original = vestaboard.model
  yield
  vestaboard.model = original


@pytest.fixture
def require_env(request: pytest.FixtureRequest) -> None:
  """Skip the test if any env vars listed in @pytest.mark.require_env are unset."""
  marker = request.node.get_closest_marker('require_env')
  if marker is None:
    return
  for var in marker.args:
    if not os.environ.get(var, '').strip():
      pytest.skip(f'{var!r} not set')
