from typing import Generator

import pytest

import integrations.vestaboard as vestaboard


@pytest.fixture(autouse=True)
def reset_vestaboard_model() -> Generator[None, None, None]:
  """Reset the active board model to NOTE before every test."""
  original = vestaboard.model
  yield
  vestaboard.model = original
