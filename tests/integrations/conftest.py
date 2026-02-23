import os

import pytest

_INTEGRATION_VARS: list[tuple[str, str]] = [
  ('BART_API_KEY', 'BART integration'),
  ('BART_STATION', 'BART integration'),
  ('BART_LINE_1_DEST', 'BART integration'),
  ('VESTABOARD_VIRTUAL_API_KEY', 'Vestaboard integration'),
]


@pytest.fixture(scope='session', autouse=True)
def _integration_env_summary() -> None:
  """Print a setup table at session start showing which integration env vars are set."""
  rows = [(var, desc, '✓ set' if os.environ.get(var, '').strip() else '✗ missing') for var, desc in _INTEGRATION_VARS]
  col = max(len(v) for v, *_ in rows)
  print('\nIntegration test env vars:')
  for var, desc, status in rows:
    print(f'  {var.ljust(col)}  {status}  ({desc})')
  print()
