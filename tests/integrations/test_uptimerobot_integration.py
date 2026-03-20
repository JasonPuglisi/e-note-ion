import os

import pytest

import config as _cfg
import integrations.uptimerobot as uptimerobot


@pytest.mark.integration
@pytest.mark.require_env('UPTIMEROBOT_API_KEY')
def test_get_variables(require_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
  """get_variables() returns valid data or raises unavailable from the real API.

  When all monitors are up (the normal case), IntegrationDataUnavailableError
  is raised — this is expected and considered a passing result. When any
  monitor is down, the returned variables are validated for shape and content.
  """
  from exceptions import IntegrationDataUnavailableError

  monkeypatch.setattr(_cfg, '_config', {'uptimerobot': {'api_key': os.environ['UPTIMEROBOT_API_KEY']}})
  uptimerobot._cache = None
  uptimerobot._first_seen_down.clear()

  try:
    result = uptimerobot.get_variables()
  except IntegrationDataUnavailableError:
    # All monitors up — expected in normal operation.
    return
  finally:
    uptimerobot._cache = None
    uptimerobot._first_seen_down.clear()

  # If we got here, at least one monitor is down.
  assert set(result.keys()) == {'monitor', 'detail'}
  for key in result:
    assert len(result[key]) == 1, f'{key}: expected 1 option'
    assert len(result[key][0]) == 1, f'{key}: expected 1 line'

  monitor = result['monitor'][0][0]
  assert monitor == monitor.upper(), f'monitor name not uppercased: {monitor!r}'

  detail = result['detail'][0][0]
  assert detail.startswith('DOWN '), f'unexpected detail format: {detail!r}'
