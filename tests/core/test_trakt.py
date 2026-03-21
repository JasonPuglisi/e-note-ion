"""Unit tests for integrations/trakt.py (mocked — no real API calls)."""

import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import config as _cfg
import integrations.trakt as trakt
import integrations.vestaboard as vb
from exceptions import IntegrationDataUnavailableError


@pytest.fixture(autouse=True)
def reset_trakt_state() -> None:
  """Reset module-level state between tests."""
  trakt._auth_started = False
  trakt._calendar_cache = None
  trakt._next_up_cache = None
  trakt._last_watching_vars = None
  trakt._stop_pending = False


@pytest.fixture()
def config_with_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  """Return a tmp config.toml with valid trakt tokens and patch _config."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "test-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 200000}\n'  # well above 24h threshold
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'test-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 200000,
      }
    },
  )
  monkeypatch.chdir(tmp_path)
  return cfg_file


@pytest.fixture()
def config_without_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  """Return a tmp config.toml without trakt tokens."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text('[trakt]\nclient_id = "test-id"\nclient_secret = "test-secret"\n')
  monkeypatch.setattr(_cfg, '_config', {'trakt': {'client_id': 'test-id', 'client_secret': 'test-secret'}})
  monkeypatch.chdir(tmp_path)
  return cfg_file


# --- _get_token ---


def test_preflight_starts_auth_when_no_tokens(config_without_tokens: Path) -> None:
  with patch.object(trakt, '_ensure_authenticated') as mock_auth:
    trakt.preflight()
  mock_auth.assert_called_once()


def test_preflight_skips_auth_when_tokens_present(config_with_tokens: Path) -> None:
  with patch.object(trakt, '_ensure_authenticated') as mock_auth:
    with patch.object(trakt, '_refresh_token'):  # tokens far-future; refresh not expected
      trakt.preflight()
  mock_auth.assert_not_called()


def test_preflight_refreshes_near_expiry_token_at_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """preflight() calls _refresh_token when token is within 24 hours of expiry."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 3600}\n'  # 1 hour — inside 24h window
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 3600,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  with patch.object(trakt, '_refresh_token') as mock_refresh:
    trakt.preflight()

  mock_refresh.assert_called_once()


def test_preflight_no_refresh_when_token_far_from_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """preflight() does not call _refresh_token when token expires far in the future."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 200000}\n'  # well outside 24h
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 200000,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  with patch.object(trakt, '_refresh_token') as mock_refresh:
    trakt.preflight()

  mock_refresh.assert_not_called()


def test_preflight_refresh_failure_clears_tokens_and_triggers_reauth(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """preflight() clears tokens and starts re-auth if startup refresh fails."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 3600}\n'
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 3600,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  mock_resp = MagicMock()
  mock_resp.status_code = 400
  mock_resp.reason = 'Bad Request'

  with patch.object(trakt, '_refresh_token', side_effect=requests.HTTPError(response=mock_resp)):
    with patch.object(trakt, '_ensure_authenticated') as mock_auth:
      trakt.preflight()

  mock_auth.assert_called_once()
  assert _cfg._config['trakt']['access_token'] == ''
  assert _cfg._config['trakt']['refresh_token'] == ''


def test_get_token_returns_access_token(config_with_tokens: Path) -> None:
  result = trakt._get_token()
  assert result == 'test-access'


def test_unauthenticated_raises_unavailable(config_without_tokens: Path) -> None:
  with patch.object(trakt, '_run_auth_flow'):  # prevent actual HTTP
    with pytest.raises(IntegrationDataUnavailableError, match='auth pending'):
      trakt._get_token()


def test_token_refresh_not_called_when_fresh(config_with_tokens: Path) -> None:
  with patch.object(trakt, '_refresh_token') as mock_refresh:
    trakt._get_token()
  mock_refresh.assert_not_called()


def test_token_refresh_called_when_near_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 100}\n'  # within 24-hour threshold
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 100,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  def fake_refresh() -> None:
    _cfg._config['trakt']['access_token'] = 'new-access'

  with patch.object(trakt, '_refresh_token', side_effect=fake_refresh) as mock_refresh:
    token = trakt._get_token()

  mock_refresh.assert_called_once()
  assert token == 'new-access'


def test_get_token_refresh_called_within_24h_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """Token with 20 hours remaining (outside old 1h window) should still trigger refresh."""
  cfg_file = tmp_path / 'config.toml'
  expires = int(time.time()) + 72000  # 20 hours — inside 24h, outside old 1h
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {expires}\n'
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': expires,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  with patch.object(trakt, '_refresh_token') as mock_refresh:
    trakt._get_token()

  mock_refresh.assert_called_once()


def test_get_token_refresh_failure_clears_tokens_and_triggers_reauth(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """HTTPError from _refresh_token clears tokens and starts re-auth."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 100}\n'
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 100,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  mock_resp = MagicMock()
  mock_resp.status_code = 400
  mock_resp.reason = 'Bad Request'

  with patch.object(trakt, '_refresh_token', side_effect=requests.HTTPError(response=mock_resp)):
    with patch.object(trakt, '_ensure_authenticated') as mock_auth:
      with pytest.raises(IntegrationDataUnavailableError, match='re-authentication required'):
        trakt._get_token()

  mock_auth.assert_called_once()
  assert _cfg._config['trakt']['access_token'] == ''
  assert _cfg._config['trakt']['refresh_token'] == ''


def test_get_token_refresh_failure_raises_unavailable_not_http_error(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """Worker sees IntegrationDataUnavailableError (graceful skip), not raw HTTPError."""
  cfg_file = tmp_path / 'config.toml'
  cfg_file.write_text(
    '[trakt]\n'
    'client_id = "test-id"\n'
    'client_secret = "test-secret"\n'
    'access_token = "old-access"\n'
    'refresh_token = "test-refresh"\n'
    f'expires_at = {int(time.time()) + 100}\n'
  )
  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'trakt': {
        'client_id': 'test-id',
        'client_secret': 'test-secret',
        'access_token': 'old-access',
        'refresh_token': 'test-refresh',
        'expires_at': int(time.time()) + 100,
      }
    },
  )
  monkeypatch.chdir(tmp_path)

  mock_resp = MagicMock()
  mock_resp.status_code = 400
  mock_resp.reason = 'Bad Request'

  with patch.object(trakt, '_refresh_token', side_effect=requests.HTTPError(response=mock_resp)):
    with patch.object(trakt, '_ensure_authenticated'):
      with pytest.raises(IntegrationDataUnavailableError):
        trakt._get_token()
      # confirm no HTTPError leaks out
      try:
        trakt._get_token()  # tokens now cleared → raises auth pending
      except IntegrationDataUnavailableError:
        pass
      except requests.HTTPError:
        pytest.fail('HTTPError leaked out of _get_token — worker would log ERROR instead of WARNING')


# --- _handle_api_401 ---


def test_handle_api_401_skips_refresh_when_token_not_expired(
  config_with_tokens: Path,
) -> None:
  """_handle_api_401 treats the 401 as transient when expires_at is far in the future."""
  with patch.object(trakt, '_refresh_token') as mock_refresh:
    with pytest.raises(IntegrationDataUnavailableError, match='transient 401'):
      trakt._handle_api_401()

  mock_refresh.assert_not_called()


def test_handle_api_401_refreshes_when_token_near_expiry(
  config_with_tokens: Path,
) -> None:
  """_handle_api_401 refreshes when expires_at is within the grace window."""
  _cfg._config['trakt']['expires_at'] = int(time.time()) + 600  # 10 min — within 1h grace

  def fake_refresh() -> None:
    _cfg._config['trakt']['access_token'] = 'refreshed-token'

  with patch.object(trakt, '_refresh_token', side_effect=fake_refresh):
    token = trakt._handle_api_401()

  assert token == 'refreshed-token'


def test_handle_api_401_refresh_failure_clears_tokens_and_triggers_reauth(
  config_with_tokens: Path,
) -> None:
  """_handle_api_401 clears tokens and starts re-auth when refresh fails."""
  _cfg._config['trakt']['expires_at'] = int(time.time()) + 600  # near-expiry to trigger refresh path
  mock_resp = MagicMock()
  mock_resp.status_code = 400
  mock_resp.reason = 'Bad Request'

  with patch.object(trakt, '_refresh_token', side_effect=requests.HTTPError(response=mock_resp)):
    with patch.object(trakt, '_ensure_authenticated') as mock_auth:
      with pytest.raises(IntegrationDataUnavailableError, match='re-authentication required'):
        trakt._handle_api_401()

  mock_auth.assert_called_once()
  assert _cfg._config['trakt']['access_token'] == ''


def test_get_variables_calendar_401_retries_with_refreshed_token(config_with_tokens: Path) -> None:
  """A 401 from the calendar endpoint triggers a token refresh and retries when near expiry."""
  _cfg._config['trakt']['expires_at'] = int(time.time()) + 600  # near-expiry

  unauth = MagicMock()
  unauth.status_code = 401

  ok = MagicMock()
  ok.status_code = 200
  ok.raise_for_status.return_value = None
  ok.json.return_value = _CALENDAR_RESPONSE

  def fake_refresh() -> None:
    _cfg._config['trakt']['access_token'] = 'new-token'

  with patch.object(trakt, '_refresh_token', side_effect=fake_refresh):
    with patch('integrations.trakt.fetch_with_retry', side_effect=[unauth, ok]):
      result = trakt.get_variables_calendar()

  assert result['show_name'] == [['GREAT SHOW']]


def test_get_variables_watching_401_retries_with_refreshed_token(config_with_tokens: Path) -> None:
  """A 401 from the watching endpoint triggers a token refresh and retries when near expiry."""
  _cfg._config['trakt']['expires_at'] = int(time.time()) + 600  # near-expiry

  unauth = MagicMock()
  unauth.status_code = 401

  ok = MagicMock()
  ok.status_code = 200
  ok.raise_for_status.return_value = None
  ok.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 1, 'title': 'Pilot'},
  }

  def fake_refresh() -> None:
    _cfg._config['trakt']['access_token'] = 'new-token'

  with patch.object(trakt, '_refresh_token', side_effect=fake_refresh):
    with patch('integrations.trakt.fetch_with_retry', side_effect=[unauth, ok]):
      result = trakt.get_variables_watching()

  assert result['show_name'] == [['MY SHOW']]


def test_get_variables_next_up_401_retries_with_refreshed_token(config_with_tokens: Path) -> None:
  """A 401 from the watched/shows endpoint triggers a token refresh and retries when near expiry."""
  _cfg._config['trakt']['expires_at'] = int(time.time()) + 600  # near-expiry

  unauth = MagicMock()
  unauth.status_code = 401

  watched = _mock_watched_ok([_WATCHED_SHOWS_RESPONSE[0]])
  progress = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  def fake_refresh() -> None:
    _cfg._config['trakt']['access_token'] = 'new-token'

  with patch.object(trakt, '_refresh_token', side_effect=fake_refresh):
    with patch('integrations.trakt.fetch_with_retry', side_effect=[unauth, watched, progress]):
      result = trakt.get_variables_next_up()

  assert result['show_name'] == [['BREAKING BAD']]


# --- _write_tokens / _store_tokens ---


def test_store_tokens_writes_to_config(config_without_tokens: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  fake_tokens = {'access_token': 'a', 'refresh_token': 'r', 'expires_in': 7776000}
  trakt._store_tokens(fake_tokens)

  text = config_without_tokens.read_text()
  assert 'access_token = "a"' in text
  assert 'refresh_token = "r"' in text
  assert 'expires_at = ' in text


def test_write_tokens_errors_on_missing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.chdir(tmp_path)
  # No config.toml in tmp_path
  monkeypatch.setattr(_cfg, '_config', {'trakt': {}})
  with pytest.raises(FileNotFoundError):
    _cfg.write_section_values('trakt', {'access_token': 'x'})


# --- token refresh HTTP ---


def test_token_refresh_updates_config(config_with_tokens: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = {
    'access_token': 'refreshed-access',
    'refresh_token': 'refreshed-refresh',
    'expires_in': 7776000,
  }

  with patch('requests.post', return_value=mock_response):
    trakt._refresh_token()

  assert _cfg._config['trakt']['access_token'] == 'refreshed-access'
  assert _cfg._config['trakt']['refresh_token'] == 'refreshed-refresh'


def test_token_refresh_http_error_raised(config_with_tokens: Path) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 401
  mock_response.reason = 'Unauthorized'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('requests.post', return_value=mock_response):
    with pytest.raises(requests.HTTPError, match='401'):
      trakt._refresh_token()


def test_token_refresh_http_error_does_not_leak_secret(config_with_tokens: Path) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 401
  mock_response.reason = 'Unauthorized'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('requests.post', return_value=mock_response):
    with pytest.raises(requests.HTTPError) as exc_info:
      trakt._refresh_token()

  assert 'test-secret' not in str(exc_info.value)
  assert 'test-refresh' not in str(exc_info.value)


# --- auth flow ---


def test_auth_thread_writes_tokens_on_success(
  config_without_tokens: Path,
) -> None:
  code_response = MagicMock()
  code_response.status_code = 200
  code_response.json.return_value = {
    'device_code': 'dc',
    'user_code': 'UC',
    'verification_url': 'https://trakt.tv/activate',
    'expires_in': 600,
    'interval': 1,
  }

  token_response = MagicMock()
  token_response.status_code = 200
  token_response.json.return_value = {
    'access_token': 'new-access',
    'refresh_token': 'new-refresh',
    'expires_in': 7776000,
  }

  with patch('requests.post', side_effect=[code_response, token_response]):
    with patch('time.sleep'):
      trakt._run_auth_flow()

  text = config_without_tokens.read_text()
  assert 'access_token = "new-access"' in text


def test_auth_thread_logs_error_on_expired(config_without_tokens: Path, caplog: pytest.LogCaptureFixture) -> None:
  code_response = MagicMock()
  code_response.status_code = 200
  code_response.json.return_value = {
    'device_code': 'dc',
    'user_code': 'UC',
    'verification_url': 'https://trakt.tv/activate',
    'expires_in': 600,
    'interval': 1,
  }

  expired_response = MagicMock()
  expired_response.status_code = 410

  with patch('requests.post', side_effect=[code_response, expired_response]):
    with patch('time.sleep'):
      trakt._run_auth_flow()

  assert 'expired' in caplog.text.lower()


def test_auth_thread_logs_error_on_denied(config_without_tokens: Path, caplog: pytest.LogCaptureFixture) -> None:
  code_response = MagicMock()
  code_response.status_code = 200
  code_response.json.return_value = {
    'device_code': 'dc',
    'user_code': 'UC',
    'verification_url': 'https://trakt.tv/activate',
    'expires_in': 600,
    'interval': 1,
  }

  denied_response = MagicMock()
  denied_response.status_code = 418

  with patch('requests.post', side_effect=[code_response, denied_response]):
    with patch('time.sleep'):
      trakt._run_auth_flow()

  assert 'denied' in caplog.text.lower()


# --- _canonicalize_episode ---


def test_canonicalize_episode_no_tmdb_uses_trakt_data(monkeypatch: pytest.MonkeyPatch) -> None:
  """Without TMDb configured, returns Trakt native title and episode numbers."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {})  # no [tmdb] section

  show_data = {'title': 'Attack on Titan', 'ids': {'tmdb': 1429}}
  ep_data = {'season': 1, 'number': 45, 'title': 'Above and Below', 'ids': {'tvdb': 8765432}}

  show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  assert show_name == 'ATTACK ON TITAN'
  assert season == 1
  assert number == 45
  assert ep_title == 'Above and Below'


def test_canonicalize_episode_with_tmdb_uses_canonical_data(monkeypatch: pytest.MonkeyPatch) -> None:
  """With TMDb configured, returns canonical title and re-numbered episode."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'Attack on Titan', 'ids': {'tmdb': 1429}}
  ep_data = {'season': 1, 'number': 45, 'title': 'Wrong Title', 'ids': {'tvdb': 8765432}}

  with (
    patch('integrations.tmdb.get_show_title', return_value='Attack on Titan') as mock_show,
    patch('integrations.tmdb.find_episode_by_tvdb_id', return_value=(4, 16, 'Above and Below', 1429, 99001)) as mock_ep,
    patch('integrations.tmdb.get_episode_group_position', return_value=None),
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_show.assert_called_once_with(1429)
  mock_ep.assert_called_once_with(8765432)
  assert show_name == 'ATTACK ON TITAN'
  assert season == 4
  assert number == 16
  assert ep_title == 'Above and Below'


def test_canonicalize_episode_tmdb_show_lookup_fails_uses_trakt_title(monkeypatch: pytest.MonkeyPatch) -> None:
  """When TMDb show lookup returns None, falls back to Trakt title."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'My Show', 'ids': {'tmdb': 9999}}
  ep_data = {'season': 2, 'number': 5, 'title': 'Pilot', 'ids': {}}

  with patch('integrations.tmdb.get_show_title', return_value=None):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  assert show_name == 'MY SHOW'
  assert season == 2
  assert number == 5


def test_canonicalize_episode_missing_ids_skips_tmdb(monkeypatch: pytest.MonkeyPatch) -> None:
  """When show/episode have no ids dict, TMDb lookups are skipped gracefully."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'Great Show'}
  ep_data = {'season': 2, 'number': 5, 'title': 'The One With The Test'}

  with (
    patch('integrations.tmdb.get_show_title') as mock_show,
    patch('integrations.tmdb.find_episode_by_tvdb_id') as mock_ep,
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_show.assert_not_called()
  mock_ep.assert_not_called()
  assert show_name == 'GREAT SHOW'
  assert season == 2
  assert number == 5


def test_canonicalize_episode_show_id_mismatch_uses_trakt_se(monkeypatch: pytest.MonkeyPatch) -> None:
  """When resolved show_id doesn't match, falls back to Trakt S/E."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'My Anime', 'ids': {'tmdb': 1111}}
  ep_data = {'season': 1, 'number': 5, 'title': 'Episode Five', 'ids': {'tvdb': 5555555}}

  with (
    patch('integrations.tmdb.get_show_title', return_value='My Anime'),
    patch('integrations.tmdb.find_episode_by_tvdb_id', return_value=(3, 7, 'Wrong Show Ep', 9999, 88001)),
    patch('integrations.tmdb.get_episode_group_position') as mock_group,
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_group.assert_not_called()
  assert season == 1
  assert number == 5
  assert ep_title == 'Episode Five'


def test_canonicalize_episode_missing_tmdb_show_id_skips_episode_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
  """When show has no tmdb ID, episode resolution via TVDb is skipped."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'No TMDb Show', 'ids': {'trakt': 42}}
  ep_data = {'season': 2, 'number': 3, 'title': 'An Episode', 'ids': {'tvdb': 7777777}}

  with patch('integrations.tmdb.find_episode_by_tvdb_id') as mock_ep:
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_ep.assert_not_called()
  assert season == 2
  assert number == 3


def test_canonicalize_episode_uses_episode_group_position(monkeypatch: pytest.MonkeyPatch) -> None:
  """Episode group position overrides base TMDb S/E when available."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'Frieren', 'ids': {'tmdb': 209867}}
  ep_data = {'season': 1, 'number': 36, 'title': 'A Magnificent End', 'ids': {'tvdb': 11447771}}

  with (
    patch('integrations.tmdb.get_show_title', return_value="Frieren: Beyond Journey's End"),
    patch('integrations.tmdb.find_episode_by_tvdb_id', return_value=(1, 36, 'A Magnificent End', 209867, 6855841)),
    patch('integrations.tmdb.get_episode_group_position', return_value=(2, 8)) as mock_group,
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_group.assert_called_once_with(209867, 6855841)
  assert season == 2
  assert number == 8
  assert ep_title == 'A Magnificent End'


def test_canonicalize_episode_falls_back_to_tmdb_base_when_group_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
  """When episode group returns None, uses base TMDb S/E."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'Some Show', 'ids': {'tmdb': 5555}}
  ep_data = {'season': 1, 'number': 10, 'title': 'Ep Ten', 'ids': {'tvdb': 1234567}}

  with (
    patch('integrations.tmdb.get_show_title', return_value='Some Show'),
    patch('integrations.tmdb.find_episode_by_tvdb_id', return_value=(2, 3, 'Ep Ten', 5555, 77777)),
    patch('integrations.tmdb.get_episode_group_position', return_value=None),
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  assert season == 2
  assert number == 3


def test_canonicalize_episode_tmdb_empty_ep_title_keeps_trakt_title(monkeypatch: pytest.MonkeyPatch) -> None:
  """When TMDb returns an empty episode title, the Trakt title is kept."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'Frieren', 'ids': {'tmdb': 209867}}
  ep_data = {'season': 2, 'number': 9, 'title': 'Episode 9', 'ids': {'tvdb': 11447772}}

  with (
    patch('integrations.tmdb.get_show_title', return_value="Frieren: Beyond Journey's End"),
    patch('integrations.tmdb.find_episode_by_tvdb_id', return_value=(1, 37, '', 209867, 6855842)),
    patch('integrations.tmdb.get_episode_group_position', return_value=(2, 9)),
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  assert season == 2
  assert number == 9
  assert ep_title == 'Episode 9'  # Trakt title preserved — TMDb title was empty


def test_canonicalize_episode_uses_imdb_fallback_when_no_tvdb_id(monkeypatch: pytest.MonkeyPatch) -> None:
  """When tvdb_ep_id is absent, imdb is tried as a fallback for S/E and title."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'Jujutsu Kaisen', 'ids': {'tmdb': 95479}}
  ep_data = {'season': 3, 'number': 4, 'title': 'Episode 4', 'ids': {'imdb': 'tt39370459'}}

  with (
    patch('integrations.tmdb.get_show_title', return_value='JUJUTSU KAISEN'),
    patch('integrations.tmdb.find_episode_by_tvdb_id') as mock_tvdb,
    patch(
      'integrations.tmdb.find_episode_by_imdb_id',
      return_value=(1, 51, 'Perfect Preparation', 95479, 6827061),
    ) as mock_imdb,
    patch('integrations.tmdb.get_episode_group_position', return_value=(3, 4)),
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_tvdb.assert_not_called()
  mock_imdb.assert_called_once_with('tt39370459')
  assert season == 3
  assert number == 4
  assert ep_title == 'Perfect Preparation'


def test_canonicalize_episode_imdb_fallback_show_id_mismatch_keeps_trakt_se(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """When imdb fallback resolves a different show, Trakt S/E and title are kept."""
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  show_data = {'title': 'My Anime', 'ids': {'tmdb': 1111}}
  ep_data = {'season': 2, 'number': 3, 'title': 'Real Title', 'ids': {'imdb': 'tt99999999'}}

  with (
    patch('integrations.tmdb.get_show_title', return_value='My Anime'),
    patch('integrations.tmdb.find_episode_by_tvdb_id') as mock_tvdb,
    patch('integrations.tmdb.find_episode_by_imdb_id', return_value=(1, 5, 'Wrong Show Ep', 9999, 88001)),
    patch('integrations.tmdb.get_episode_group_position') as mock_group,
  ):
    show_name, season, number, ep_title = trakt._canonicalize_episode(show_data, ep_data)  # noqa: SLF001

  mock_tvdb.assert_not_called()
  mock_group.assert_not_called()
  assert season == 2
  assert number == 3
  assert ep_title == 'Real Title'


# --- _canonicalize_movie ---


def test_canonicalize_movie_no_tmdb_uses_trakt_title(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {})

  result = trakt._canonicalize_movie({'title': 'Inception', 'ids': {'tmdb': 27205}})  # noqa: SLF001

  assert result == 'Inception'


def test_canonicalize_movie_with_tmdb_uses_canonical_title(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})

  with patch('integrations.tmdb.get_movie_title', return_value='Inception') as mock_movie:
    result = trakt._canonicalize_movie({'title': 'inception', 'ids': {'tmdb': 27205}})  # noqa: SLF001

  mock_movie.assert_called_once_with(27205)
  assert result == 'Inception'


# --- get_variables_calendar ---


_CALENDAR_RESPONSE = [
  {
    'first_aired': '2099-09-16T01:00:00.000Z',
    'episode': {
      'season': 2,
      'number': 5,
      'title': 'The One With The Test',
    },
    'show': {'title': 'Great Show'},
  }
]

_CALENDAR_RESPONSE_ALL_PAST = [
  {
    'first_aired': '2000-01-01T01:00:00.000Z',
    'episode': {
      'season': 1,
      'number': 1,
      'title': 'Pilot',
    },
    'show': {'title': 'Old Show'},
  }
]


def test_get_variables_calendar_returns_expected_vars(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = _CALENDAR_RESPONSE

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    result = trakt.get_variables_calendar()

  assert result['show_name'] == [['GREAT SHOW']]
  assert result['episode_ref'] == [['S2E5']]
  assert result['episode_title'] == [['ONE WITH THE TEST']]
  assert 'air_day' in result
  assert re.match(r'^\d{2}:\d{2}$', result['air_time'][0][0])


def test_get_variables_calendar_empty_raises_unavailable(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = []

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_calendar()


def test_get_variables_calendar_all_past_raises_unavailable(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = _CALENDAR_RESPONSE_ALL_PAST

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_calendar()


def test_get_variables_calendar_skips_past_entries(
  config_with_tokens: Path,
) -> None:
  """Past entries are skipped; the next future entry is returned."""
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = _CALENDAR_RESPONSE_ALL_PAST + _CALENDAR_RESPONSE

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    result = trakt.get_variables_calendar()

  assert result['show_name'] == [['GREAT SHOW']]
  assert result['episode_ref'] == [['S2E5']]


def test_get_variables_calendar_http_error_raised(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 403
  mock_response.reason = 'Forbidden'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError, match='403'):
      trakt.get_variables_calendar()


def test_get_variables_calendar_http_error_does_not_leak_client_id(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 401
  mock_response.reason = 'Unauthorized'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError) as exc_info:
      trakt.get_variables_calendar()

  assert 'test-id' not in str(exc_info.value)
  assert 'test-access' not in str(exc_info.value)


# --- get_variables_watching ---


def test_get_variables_watching_episode_returns_vars(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 3, 'title': 'Pilot'},
  }

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    result = trakt.get_variables_watching()

  assert result['status_line'] == [['[G] NOW PLAYING']]
  assert result['show_name'] == [['MY SHOW']]
  assert result['episode_ref'] == [['S1E3']]
  assert result['episode_title'] == [['PILOT']]


def test_get_variables_watching_movie_returns_vars(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = {
    'type': 'movie',
    'movie': {'title': 'Inception'},
  }

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    result = trakt.get_variables_watching()

  assert result['status_line'] == [['[G] NOW PLAYING']]
  assert result['show_name'] == [['INCEPTION']]
  assert result['episode_ref'] == [['MOVIE']]
  assert result['episode_title'] == [['']]


def test_get_variables_watching_204_raises_unavailable(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 204

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError, match='Nothing currently playing'):
      trakt.get_variables_watching()


def test_get_variables_watching_first_204_after_playing_skips(
  config_with_tokens: Path,
) -> None:
  # Regression: #273 — first 204 after watching is debounced to avoid a false
  # stopped card during back-to-back episode transitions.
  play_response = MagicMock()
  play_response.status_code = 200
  play_response.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 3, 'title': 'Pilot'},
  }
  stop_response = MagicMock()
  stop_response.status_code = 204

  with patch('integrations.trakt.fetch_with_retry', side_effect=[play_response, stop_response]):
    trakt.get_variables_watching()
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()  # first 204 — debounced, not shown yet

  assert trakt._stop_pending is True


def test_get_variables_watching_second_204_returns_violet_stopped_state(
  config_with_tokens: Path,
) -> None:
  play_response = MagicMock()
  play_response.status_code = 200
  play_response.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 3, 'title': 'Pilot'},
  }
  stop_response = MagicMock()
  stop_response.status_code = 204

  with patch('integrations.trakt.fetch_with_retry', side_effect=[play_response, stop_response, stop_response]):
    trakt.get_variables_watching()
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()  # first 204 — skip
    result = trakt.get_variables_watching()  # second consecutive 204 — emit

  assert result['status_line'] == [['[V] NOW PLAYING']]
  assert result['show_name'] == [['MY SHOW']]
  assert result['episode_ref'] == [['S1E3']]
  assert result['episode_title'] == [['PILOT']]


def test_get_variables_watching_state_cleared_after_stopped(
  config_with_tokens: Path,
) -> None:
  play_response = MagicMock()
  play_response.status_code = 200
  play_response.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 3, 'title': 'Pilot'},
  }
  stop_response = MagicMock()
  stop_response.status_code = 204

  with patch(
    'integrations.trakt.fetch_with_retry',
    side_effect=[play_response, stop_response, stop_response, stop_response],
  ):
    trakt.get_variables_watching()
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()  # first 204 — skip
    trakt.get_variables_watching()  # second 204 — violet indicator, clears cache
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()  # no prior state — raises


def test_get_variables_watching_play_after_first_204_resets_pending(
  config_with_tokens: Path,
) -> None:
  # Back-to-back: play → first 204 (skip) → play (next episode) → green card.
  # _stop_pending is reset so a future genuine stop still shows the indicator.
  play_response = MagicMock()
  play_response.status_code = 200
  play_response.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 3, 'title': 'Pilot'},
  }
  stop_response = MagicMock()
  stop_response.status_code = 204

  with patch('integrations.trakt.fetch_with_retry', side_effect=[play_response, stop_response, play_response]):
    trakt.get_variables_watching()
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()  # first 204 — skip
    result = trakt.get_variables_watching()  # new episode scrobbled

  assert result['status_line'] == [['[G] NOW PLAYING']]
  assert trakt._stop_pending is False


def test_clear_watching_state_resets_cached_vars(config_with_tokens: Path) -> None:
  play_response = MagicMock()
  play_response.status_code = 200
  play_response.json.return_value = {
    'type': 'episode',
    'show': {'title': 'My Show'},
    'episode': {'season': 1, 'number': 3, 'title': 'Pilot'},
  }
  stop_response = MagicMock()
  stop_response.status_code = 204

  with patch('integrations.trakt.fetch_with_retry', return_value=play_response):
    trakt.get_variables_watching()

  assert trakt._last_watching_vars is not None
  trakt.clear_watching_state()
  assert trakt._last_watching_vars is None

  # After clear, 204 should raise rather than return stopped state.
  with patch('integrations.trakt.fetch_with_retry', return_value=stop_response):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()


def test_get_variables_watching_state_reset_on_new_play(
  config_with_tokens: Path,
) -> None:
  play_response = MagicMock()
  play_response.status_code = 200
  play_response.json.return_value = {
    'type': 'movie',
    'movie': {'title': 'Inception'},
  }

  # Two successful polls — second should return fresh green state, not violet.
  with patch('integrations.trakt.fetch_with_retry', side_effect=[play_response, play_response]):
    trakt.get_variables_watching()
    result = trakt.get_variables_watching()

  assert result['status_line'] == [['[G] NOW PLAYING']]
  assert result['show_name'] == [['INCEPTION']]


def test_get_variables_watching_http_error_does_not_leak_client_id(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 401
  mock_response.reason = 'Unauthorized'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError) as exc_info:
      trakt.get_variables_watching()

  assert 'test-id' not in str(exc_info.value)
  assert 'test-access' not in str(exc_info.value)


def test_get_variables_calendar_long_show_name_truncated(
  config_with_tokens: Path,
) -> None:
  """A show name longer than model.cols must be ellipsis-truncated, not left to wrap."""
  long_title = 'Star Trek The Next Generation'
  long_response = [
    {
      'first_aired': '2099-09-16T01:00:00.000Z',
      'episode': {'season': 1, 'number': 1, 'title': 'Pilot'},
      'show': {'title': long_title},
    }
  ]
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = long_response

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    result = trakt.get_variables_calendar()

  show_name = result['show_name'][0][0]
  upper = long_title.upper()
  assert vb.display_len(show_name) <= vb.model.cols
  assert show_name.endswith('...')
  assert upper.startswith(show_name[:-3])


def test_get_variables_watching_long_show_name_truncated(
  config_with_tokens: Path,
) -> None:
  """A show name longer than model.cols must be ellipsis-truncated, not left to wrap."""
  long_title = 'Star Trek The Next Generation'
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = {
    'type': 'episode',
    'show': {'title': long_title},
    'episode': {'season': 1, 'number': 1, 'title': 'Encounter At Farpoint'},
  }

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    result = trakt.get_variables_watching()

  show_name = result['show_name'][0][0]
  upper = long_title.upper()
  assert vb.display_len(show_name) <= vb.model.cols
  assert show_name.endswith('...')
  assert upper.startswith(show_name[:-3])


# --- calendar cache ---


def test_calendar_cache_hit_within_ttl_returns_cached_value(config_with_tokens: Path) -> None:
  """On API failure within TTL, cached calendar data is returned instead of raising."""
  mock_ok = MagicMock()
  mock_ok.status_code = 200
  mock_ok.raise_for_status.return_value = None
  mock_ok.json.return_value = _CALENDAR_RESPONSE

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_ok):
    trakt.get_variables_calendar()

  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    result = trakt.get_variables_calendar()

  assert result['show_name'] == [['GREAT SHOW']]


def test_calendar_cache_expired_raises_unavailable(config_with_tokens: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """On API failure with an expired cache, raises IntegrationDataUnavailableError."""
  mock_ok = MagicMock()
  mock_ok.status_code = 200
  mock_ok.raise_for_status.return_value = None
  mock_ok.json.return_value = _CALENDAR_RESPONSE

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_ok):
    trakt.get_variables_calendar()

  assert trakt._calendar_cache is not None
  monkeypatch.setattr(trakt._calendar_cache, 'cached_at', time.monotonic() - trakt._CALENDAR_CACHE_TTL - 1)

  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_calendar()


def test_calendar_cache_cold_start_raises_unavailable(config_with_tokens: Path) -> None:
  """With no cache and API down, raises IntegrationDataUnavailableError."""
  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_calendar()


def test_calendar_cache_updated_on_success(config_with_tokens: Path) -> None:
  """Successful calendar fetch writes to the cache."""
  mock_ok = MagicMock()
  mock_ok.status_code = 200
  mock_ok.raise_for_status.return_value = None
  mock_ok.json.return_value = _CALENDAR_RESPONSE

  assert trakt._calendar_cache is None
  with patch('integrations.trakt.fetch_with_retry', return_value=mock_ok):
    trakt.get_variables_calendar()
  assert trakt._calendar_cache is not None
  assert trakt._calendar_cache.value['show_name'] == [['GREAT SHOW']]


# --- watching: no cache ---


def test_watching_network_error_raises_unavailable(config_with_tokens: Path) -> None:
  """Network error on watching endpoint raises IntegrationDataUnavailableError (no cache)."""
  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError, match='watching request failed'):
      trakt.get_variables_watching()


# --- get_variables_next_up ---


_WATCHED_SHOWS_RESPONSE = [
  {
    'show': {'title': 'Breaking Bad', 'ids': {'trakt': 1}},
    'last_watched_at': '2099-01-01T00:00:00.000Z',
  },
  {
    'show': {'title': 'The Wire', 'ids': {'trakt': 2}},
    'last_watched_at': '2099-01-02T00:00:00.000Z',
  },
]

_PROGRESS_WITH_NEXT = {
  'next_episode': {
    'season': 3,
    'number': 1,
    'title': 'Box Cutter',
    'first_aired': '2000-01-01T00:00:00.000Z',
  }
}

_PROGRESS_WITH_UNAIRED = {
  'next_episode': {
    'season': 4,
    'number': 1,
    'title': 'Future Episode',
    'first_aired': '2099-12-31T00:00:00.000Z',
  }
}

_PROGRESS_WITH_NO_AIR_DATE = {
  'next_episode': {
    'season': 5,
    'number': 1,
    'title': 'TBA',
  }
}

_PROGRESS_COMPLETED = {'next_episode': None}


def _mock_watched_ok(shows: list | None = None) -> MagicMock:
  r = MagicMock()
  r.status_code = 200
  r.raise_for_status.return_value = None
  r.json.return_value = shows if shows is not None else _WATCHED_SHOWS_RESPONSE
  return r


def _mock_progress_ok(data: dict) -> MagicMock:
  r = MagicMock()
  r.status_code = 200
  r.raise_for_status.return_value = None
  r.json.return_value = data
  return r


def test_get_variables_next_up_returns_expected_vars(config_with_tokens: Path) -> None:
  watched = _mock_watched_ok([_WATCHED_SHOWS_RESPONSE[0]])
  progress = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress]):
    result = trakt.get_variables_next_up()

  assert result['show_name'] == [['BREAKING BAD']]
  assert result['episode_ref'] == [['S3E1']]
  assert result['episode_title'] == [['BOX CUTTER']]


def test_get_variables_next_up_skips_completed_show_tries_next(config_with_tokens: Path) -> None:
  watched = _mock_watched_ok(_WATCHED_SHOWS_RESPONSE)
  progress_done = _mock_progress_ok(_PROGRESS_COMPLETED)
  progress_next = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress_done, progress_next]):
    result = trakt.get_variables_next_up()

  assert result['show_name'] == [['THE WIRE']]
  assert result['episode_ref'] == [['S3E1']]


def test_get_variables_next_up_all_completed_raises_unavailable(config_with_tokens: Path) -> None:
  shows = [
    {'show': {'title': f'Show {i}', 'ids': {'trakt': i}}, 'last_watched_at': '2099-01-01T00:00:00.000Z'}
    for i in range(5)
  ]
  watched = _mock_watched_ok(shows)
  progress_done = _mock_progress_ok(_PROGRESS_COMPLETED)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched] + [progress_done] * 5):
    with pytest.raises(IntegrationDataUnavailableError, match='No next episode'):
      trakt.get_variables_next_up()


def test_get_variables_next_up_empty_watched_list_raises_unavailable(config_with_tokens: Path) -> None:
  watched = _mock_watched_ok([])

  with patch('integrations.trakt.fetch_with_retry', return_value=watched):
    with pytest.raises(IntegrationDataUnavailableError, match='No watched shows'):
      trakt.get_variables_next_up()


def test_get_variables_next_up_watched_api_error_raises_unavailable(config_with_tokens: Path) -> None:
  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError, match='next-up watched request failed'):
      trakt.get_variables_next_up()


def test_get_variables_next_up_progress_api_error_raises_unavailable(config_with_tokens: Path) -> None:
  watched = _mock_watched_ok([_WATCHED_SHOWS_RESPONSE[0]])

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, requests.ConnectionError()]):
    with pytest.raises(IntegrationDataUnavailableError, match='next-up progress request failed'):
      trakt.get_variables_next_up()


def test_get_variables_next_up_long_show_name_truncated(config_with_tokens: Path) -> None:
  long_title = 'Star Trek The Next Generation'
  shows = [{'show': {'title': long_title, 'ids': {'trakt': 1}}, 'last_watched_at': '2099-01-01T00:00:00.000Z'}]
  watched = _mock_watched_ok(shows)
  progress = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress]):
    result = trakt.get_variables_next_up()

  show_name = result['show_name'][0][0]
  upper = long_title.upper()
  assert vb.display_len(show_name) <= vb.model.cols
  assert show_name.endswith('...')
  assert upper.startswith(show_name[:-3])


def test_get_variables_next_up_does_not_leak_credentials(config_with_tokens: Path) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 401
  mock_response.reason = 'Unauthorized'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('integrations.trakt.fetch_with_retry', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError) as exc_info:
      trakt.get_variables_next_up()

  assert 'test-id' not in str(exc_info.value)
  assert 'test-access' not in str(exc_info.value)


def test_next_up_cache_hit_on_api_failure(config_with_tokens: Path) -> None:
  watched = _mock_watched_ok([_WATCHED_SHOWS_RESPONSE[0]])
  progress = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress]):
    trakt.get_variables_next_up()

  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    result = trakt.get_variables_next_up()

  assert result['show_name'] == [['BREAKING BAD']]


def test_next_up_cache_expired_raises_unavailable(config_with_tokens: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  watched = _mock_watched_ok([_WATCHED_SHOWS_RESPONSE[0]])
  progress = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress]):
    trakt.get_variables_next_up()

  assert trakt._next_up_cache is not None
  monkeypatch.setattr(trakt._next_up_cache, 'cached_at', time.monotonic() - trakt._NEXT_UP_CACHE_TTL - 1)

  with patch('integrations.trakt.fetch_with_retry', side_effect=requests.ConnectionError()):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_next_up()


def test_get_variables_next_up_skips_unaired_episode(config_with_tokens: Path) -> None:
  """Unaired next episode is skipped; falls through to the next show."""
  watched = _mock_watched_ok(_WATCHED_SHOWS_RESPONSE)
  progress_unaired = _mock_progress_ok(_PROGRESS_WITH_UNAIRED)
  progress_aired = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress_unaired, progress_aired]):
    result = trakt.get_variables_next_up()

  assert result['show_name'] == [['THE WIRE']]
  assert result['episode_ref'] == [['S3E1']]


def test_get_variables_next_up_skips_no_air_date(config_with_tokens: Path) -> None:
  """Episode with no first_aired is treated as unaired and skipped."""
  watched = _mock_watched_ok(_WATCHED_SHOWS_RESPONSE)
  progress_no_date = _mock_progress_ok(_PROGRESS_WITH_NO_AIR_DATE)
  progress_aired = _mock_progress_ok(_PROGRESS_WITH_NEXT)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched, progress_no_date, progress_aired]):
    result = trakt.get_variables_next_up()

  assert result['show_name'] == [['THE WIRE']]


def test_get_variables_next_up_all_unaired_raises_unavailable(config_with_tokens: Path) -> None:
  """When all shows have unaired next episodes, raises unavailable."""
  shows = [
    {'show': {'title': f'Show {i}', 'ids': {'trakt': i}}, 'last_watched_at': '2099-01-01T00:00:00.000Z'}
    for i in range(5)
  ]
  watched = _mock_watched_ok(shows)
  progress_unaired = _mock_progress_ok(_PROGRESS_WITH_UNAIRED)

  with patch('integrations.trakt.fetch_with_retry', side_effect=[watched] + [progress_unaired] * 5):
    with pytest.raises(IntegrationDataUnavailableError, match='No next episode'):
      trakt.get_variables_next_up()
