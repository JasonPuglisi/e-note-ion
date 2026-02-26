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
def reset_trakt_auth_state() -> None:
  """Reset module-level auth state between tests."""
  trakt._auth_started = False


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
    f'expires_at = {int(time.time()) + 10000}\n'
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
        'expires_at': int(time.time()) + 10000,
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
    trakt.preflight()
  mock_auth.assert_not_called()


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
    f'expires_at = {int(time.time()) + 100}\n'  # within 1-hour threshold
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


def test_auth_thread_logs_error_on_expired(config_without_tokens: Path, capsys: pytest.CaptureFixture[str]) -> None:
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

  out = capsys.readouterr().out
  assert 'expired' in out.lower()


def test_auth_thread_logs_error_on_denied(config_without_tokens: Path, capsys: pytest.CaptureFixture[str]) -> None:
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

  out = capsys.readouterr().out
  assert 'denied' in out.lower()


# --- _format_episode_ref ---


def test_format_episode_ref_no_padding() -> None:
  assert trakt._format_episode_ref(9, 8) == 'S9E8'  # noqa: SLF001


def test_format_episode_ref_double_digit() -> None:
  assert trakt._format_episode_ref(12, 24) == 'S12E24'  # noqa: SLF001


def test_format_episode_ref_single_digit_each() -> None:
  assert trakt._format_episode_ref(1, 1) == 'S1E1'  # noqa: SLF001


# --- _strip_leading_article ---


def test_strip_leading_article_the() -> None:
  assert trakt._strip_leading_article('THE FINAL SHOWDOWN') == 'FINAL SHOWDOWN'  # noqa: SLF001


def test_strip_leading_article_a() -> None:
  assert trakt._strip_leading_article('A QUIET MAN') == 'QUIET MAN'  # noqa: SLF001


def test_strip_leading_article_an() -> None:
  assert trakt._strip_leading_article('AN UNEXPECTED JOURNEY') == 'UNEXPECTED JOURNEY'  # noqa: SLF001


def test_strip_leading_article_no_article() -> None:
  assert trakt._strip_leading_article('PILOT') == 'PILOT'  # noqa: SLF001


def test_strip_leading_article_word_starting_with_the() -> None:
  # "THEORY" should not be stripped — must match full word boundary
  assert trakt._strip_leading_article('THEORY OF EVERYTHING') == 'THEORY OF EVERYTHING'  # noqa: SLF001


def test_strip_leading_article_word_starting_with_a() -> None:
  # "AFTERMATH" should not be stripped
  assert trakt._strip_leading_article('AFTERMATH') == 'AFTERMATH'  # noqa: SLF001


def test_strip_leading_article_empty() -> None:
  assert trakt._strip_leading_article('') == ''  # noqa: SLF001


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

  with patch('requests.get', return_value=mock_response):
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

  with patch('requests.get', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_calendar()


def test_get_variables_calendar_all_past_raises_unavailable(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = _CALENDAR_RESPONSE_ALL_PAST

  with patch('requests.get', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_calendar()


def test_get_variables_calendar_skips_past_entries(
  config_with_tokens: Path,
) -> None:
  """Past entries are skipped; the next future entry is returned."""
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = _CALENDAR_RESPONSE_ALL_PAST + _CALENDAR_RESPONSE

  with patch('requests.get', return_value=mock_response):
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

  with patch('requests.get', return_value=mock_response):
    with pytest.raises(requests.HTTPError, match='403'):
      trakt.get_variables_calendar()


def test_get_variables_calendar_http_error_does_not_leak_client_id(
  config_with_tokens: Path,
) -> None:
  mock_response = MagicMock()
  mock_response.status_code = 401
  mock_response.reason = 'Unauthorized'
  mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)

  with patch('requests.get', return_value=mock_response):
    with pytest.raises(requests.HTTPError) as exc_info:
      trakt.get_variables_calendar()

  assert 'test-id' not in str(exc_info.value)
  assert 'test-access' not in str(exc_info.value)


# --- get_variables_watching ---


@pytest.fixture(autouse=True)
def reset_watching_state() -> None:
  """Clear module-level watching state between tests."""
  trakt._last_watching_vars = None


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

  with patch('requests.get', return_value=mock_response):
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

  with patch('requests.get', return_value=mock_response):
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

  with patch('requests.get', return_value=mock_response):
    with pytest.raises(IntegrationDataUnavailableError, match='Nothing currently playing'):
      trakt.get_variables_watching()


def test_get_variables_watching_204_after_playing_returns_violet_stopped_state(
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

  with patch('requests.get', side_effect=[play_response, stop_response]):
    trakt.get_variables_watching()
    result = trakt.get_variables_watching()

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

  with patch('requests.get', side_effect=[play_response, stop_response, stop_response]):
    trakt.get_variables_watching()
    trakt.get_variables_watching()  # returns violet stopped state, clears cache
    with pytest.raises(IntegrationDataUnavailableError):
      trakt.get_variables_watching()  # no prior state — raises


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

  with patch('requests.get', return_value=play_response):
    trakt.get_variables_watching()

  assert trakt._last_watching_vars is not None
  trakt.clear_watching_state()
  assert trakt._last_watching_vars is None

  # After clear, 204 should raise rather than return stopped state.
  with patch('requests.get', return_value=stop_response):
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
  with patch('requests.get', side_effect=[play_response, play_response]):
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

  with patch('requests.get', return_value=mock_response):
    with pytest.raises(requests.HTTPError) as exc_info:
      trakt.get_variables_watching()

  assert 'test-id' not in str(exc_info.value)
  assert 'test-access' not in str(exc_info.value)


def test_get_variables_calendar_long_show_name_truncated(
  config_with_tokens: Path,
) -> None:
  """A show name longer than model.cols must be word-truncated, not left to wrap."""
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

  with patch('requests.get', return_value=mock_response):
    result = trakt.get_variables_calendar()

  show_name = result['show_name'][0][0]
  upper = long_title.upper()
  assert vb.display_len(show_name) <= vb.model.cols
  assert upper.startswith(show_name)
  assert show_name == upper or upper[len(show_name)] == ' '


def test_get_variables_watching_long_show_name_truncated(
  config_with_tokens: Path,
) -> None:
  """A show name longer than model.cols must be word-truncated, not left to wrap."""
  long_title = 'Star Trek The Next Generation'
  mock_response = MagicMock()
  mock_response.status_code = 200
  mock_response.json.return_value = {
    'type': 'episode',
    'show': {'title': long_title},
    'episode': {'season': 1, 'number': 1, 'title': 'Encounter At Farpoint'},
  }

  with patch('requests.get', return_value=mock_response):
    result = trakt.get_variables_watching()

  show_name = result['show_name'][0][0]
  upper = long_title.upper()
  assert vb.display_len(show_name) <= vb.model.cols
  assert upper.startswith(show_name)
  assert show_name == upper or upper[len(show_name)] == ' '
