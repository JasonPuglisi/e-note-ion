import os
from pathlib import Path

import pytest

_INTEGRATION_VARS: list[tuple[str, str]] = [
  ('VESTABOARD_VIRTUAL_API_KEY', 'Vestaboard integration'),
  ('CALENDAR_URL', 'Calendar integration (ICS mode)'),
  ('CALENDAR_CALDAV_URL', 'Calendar integration (CalDAV mode)'),
  ('CALENDAR_USERNAME', 'Calendar integration (CalDAV mode)'),
  ('CALENDAR_PASSWORD', 'Calendar integration (CalDAV mode)'),
  ('BART_API_KEY', 'BART integration'),
  ('TRAKT_CLIENT_ID', 'Trakt integration'),
  ('TRAKT_CLIENT_SECRET', 'Trakt integration'),
  ('TRAKT_ACCESS_TOKEN', 'Trakt integration'),
  ('DISCOGS_TOKEN', 'Discogs integration'),
]

_skipped = 0


def _load_dotenv() -> None:
  """Load .env from the project root into os.environ if the file exists.

  Only sets variables that are not already present in the environment, so
  CI secrets (set as real env vars) always take precedence over .env values.
  Simple key=value parser — no external dependency needed.
  """
  env_path = Path(__file__).parent.parent.parent / '.env'
  if not env_path.exists():
    return
  for line in env_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
      continue
    key, _, value = line.partition('=')
    key = key.strip()
    value = value.strip().strip('"').strip("'")
    if key and key not in os.environ:
      os.environ[key] = value


_load_dotenv()


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
