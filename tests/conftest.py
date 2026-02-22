import os
from typing import Generator

# Must be set before vestaboard is imported anywhere, since it reads
# VESTABOARD_API_KEY at module level and exits if absent.
os.environ.setdefault('VESTABOARD_API_KEY', 'test-key')

import pytest

import integrations.vestaboard as vestaboard


@pytest.fixture(autouse=True)
def reset_vestaboard_model() -> Generator[None, None, None]:
  """Reset the active board model to NOTE before every test."""
  original = vestaboard.model
  yield
  vestaboard.model = original
