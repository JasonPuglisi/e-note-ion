"""Startup credential validation (#503)."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import config as _config_mod
import health as _health_mod
import integrations.http as _http
from exceptions import IntegrationDataUnavailableError

# (module, config section, minimal config to satisfy config.get)
PREFLIGHT_INTEGRATIONS = [
  ('integrations.discogs', {'discogs': {'api_key': 'k'}}),
  ('integrations.ynab', {'ynab': {'api_key': 'k'}}),
  ('integrations.uptimerobot', {'uptimerobot': {'api_key': 'k'}}),
  ('integrations.bart', {'bart': {'api_key': 'k', 'station': 'EMBR'}}),
  ('integrations.parcel', {'parcel': {'api_key': 'k'}}),
  ('integrations.tmdb', {'tmdb': {'api_key': 'k'}}),
]


@pytest.mark.parametrize(('module', 'cfg'), PREFLIGHT_INTEGRATIONS)
def test_preflight_accepts_a_working_credential(
  module: str, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
  mod = __import__(module, fromlist=['preflight'])
  monkeypatch.setattr(_config_mod, '_config', cfg)
  with patch(f'{module}.fetch_with_retry', return_value=MagicMock(status_code=200)):
    mod.preflight()


@pytest.mark.parametrize(('module', 'cfg'), PREFLIGHT_INTEGRATIONS)
@pytest.mark.parametrize('status', [401, 403])
def test_preflight_raises_credential_error_on_rejection(
  module: str, cfg: dict[str, Any], status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
  mod = __import__(module, fromlist=['preflight'])
  monkeypatch.setattr(_config_mod, '_config', cfg)
  with patch(f'{module}.fetch_with_retry', return_value=MagicMock(status_code=status)):
    with pytest.raises(_http.CredentialError):
      mod.preflight()


def test_tmdb_preflight_is_a_noop_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
  """TMDb is optional — an absent token is a choice, not a fault."""
  import integrations.tmdb as tmdb

  monkeypatch.setattr(_config_mod, '_config', {})
  with patch('integrations.tmdb.fetch_with_retry') as fetch:
    tmdb.preflight()
  assert not fetch.called


# --- how the scheduler reacts ---


def _run_preflight(mod: Any) -> None:
  """Mirror scheduler.py's preflight loop for one integration."""
  import scheduler as _sched

  with patch.object(_sched, '_get_integration', return_value=mod):
    with patch.object(_sched, 'load_content'):
      try:
        mod.preflight()
      except _http.CredentialError as e:
        _sched.logger.error('%s', _http.redact(str(e)))
        _health_mod.record_error('probe', _http.redact(str(e)))
      except Exception as e:  # noqa: BLE001
        _sched.logger.warning('preflight for %r failed: %s', 'probe', _http.redact(str(e)))


def test_credential_failure_marks_the_integration_unhealthy() -> None:
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('probe')

  mod = MagicMock()
  mod.preflight.side_effect = _http.CredentialError('probe: credential rejected (HTTP 401)')
  _run_preflight(mod)

  assert _health_mod.get_summary()['integrations']['probe']['status'] == 'error'
  assert _health_mod.get_summary()['status'] != 'healthy'


def test_a_transient_failure_does_not_mark_unhealthy() -> None:
  """A 500 or a timeout at boot is not an expired token."""
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('probe')

  mod = MagicMock()
  mod.preflight.side_effect = TimeoutError('connection timed out')
  _run_preflight(mod)

  assert _health_mod.get_summary()['integrations']['probe']['status'] == 'unknown'
  assert _health_mod.get_summary()['status'] == 'healthy'


def test_expected_empty_does_not_mark_unhealthy() -> None:
  """The uptimerobot case: "all monitors up" is the healthy state.

  Marking it unhealthy would make /health red on a perfectly good system every
  time nothing is wrong.
  """
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('probe')

  mod = MagicMock()
  mod.preflight.side_effect = IntegrationDataUnavailableError('all monitors up', expected=True)
  _run_preflight(mod)

  assert _health_mod.get_summary()['status'] == 'healthy'


def test_credential_error_text_is_redacted() -> None:
  """Preflight errors reach /health and health.jsonl, so URLs must be scrubbed."""
  _health_mod.reset()
  _health_mod.init()
  _health_mod.register('probe')

  mod = MagicMock()
  mod.preflight.side_effect = _http.CredentialError(
    'probe: rejected by https://api.example.com/v1/me?token=SECRETVALUE'
  )
  _run_preflight(mod)

  detail = _health_mod.get_summary()['integrations']['probe']
  assert 'SECRETVALUE' not in (detail['last_error_message'] or '')
