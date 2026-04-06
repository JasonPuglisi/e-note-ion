"""Shared fixtures for core tests."""

from pathlib import Path

import pytest

import health as _health_mod


@pytest.fixture(autouse=True)
def _isolate_health_log(tmp_path: Path) -> None:
  """Point the health log at a temp directory so tests don't touch the
  real filesystem or interfere with each other."""
  _health_mod._LOG_DIR = tmp_path
  _health_mod._LOG_PATH = tmp_path / 'health.jsonl'
