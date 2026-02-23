import os

import pytest

_INTEGRATION_VARS: list[tuple[str, str]] = [
  ('BART_API_KEY', 'BART integration'),
  ('BART_STATION', 'BART integration'),
  ('BART_LINE_1_DEST', 'BART integration'),
  ('VESTABOARD_VIRTUAL_API_KEY', 'Vestaboard integration'),
]

_skipped = 0


@pytest.fixture(scope='session', autouse=True)
def _integration_env_summary() -> None:
  """Print a setup table at session start showing which integration env vars are set."""
  rows = [(var, desc, '✓ set' if os.environ.get(var, '').strip() else '✗ missing') for var, desc in _INTEGRATION_VARS]
  col = max(len(v) for v, *_ in rows)
  print('\nIntegration test env vars:')
  for var, desc, status in rows:
    print(f'  {var.ljust(col)}  {status}  ({desc})')
  print()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
  """Track integration tests skipped due to missing env vars."""
  global _skipped
  if report.skipped and report.when in ('setup', 'call'):
    _skipped += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
  """Exit non-zero if any integration test was skipped — likely a missing env var."""
  if exitstatus == pytest.ExitCode.OK and _skipped > 0:
    print(f'\nWARNING: {_skipped} integration test(s) skipped — required env vars may be missing.')
    session.exitstatus = pytest.ExitCode.NO_TESTS_COLLECTED
