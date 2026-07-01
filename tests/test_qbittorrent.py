"""Unit tests for integrations/qbittorrent.py."""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests as _requests

import integrations.qbittorrent as qbt
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _torrent(*, size: int = 1_000_000_000) -> dict:
  """Build a minimal qBittorrent torrent dict."""
  return {'size': size, 'name': 'test.torrent', 'state': 'uploading'}


def _login_response_ok() -> MagicMock:
  # qBittorrent <=5.1: 200 + body 'Ok.'.
  resp = MagicMock()
  resp.status_code = 200
  resp.text = 'Ok.'
  resp.raise_for_status = MagicMock()
  return resp


def _login_response_ok_204() -> MagicMock:
  # qBittorrent 5.2+: 204 No Content, empty body (session cookie still set).
  resp = MagicMock()
  resp.status_code = 204
  resp.text = ''
  resp.raise_for_status = MagicMock()
  return resp


def _login_response_fail() -> MagicMock:
  # Rejected credentials: 200 + body 'Fails.' (no session cookie).
  resp = MagicMock()
  resp.status_code = 200
  resp.text = 'Fails.'
  resp.raise_for_status = MagicMock()
  return resp


def _torrents_response(torrents: list[dict]) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = torrents
  resp.raise_for_status = MagicMock()
  return resp


def _patched_config() -> dict:
  return {
    'qbittorrent': {
      'url': 'http://192.168.1.50:8080',
      'username': 'admin',
      'password': 'test',
    }
  }


# ---------------------------------------------------------------------------
# _fmt_size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'size_bytes,expected',
  [
    (0, '0 GB'),
    (500 * 1024**3, '500 GB'),
    (1024**4, '1 TB'),
    (int(1.2 * 1024**4), '1.2 TB'),
    (int(14.0 * 1024**4), '14 TB'),
    (int(2.5 * 1024**3), '2.5 GB'),
    (int(0.1 * 1024**3), '0.1 GB'),
  ],
)
def test_fmt_size(size_bytes: int, expected: str) -> None:
  assert qbt._fmt_size(size_bytes) == expected


# ---------------------------------------------------------------------------
# _login
# ---------------------------------------------------------------------------


def test_login_success() -> None:
  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_ok()
  with patch('integrations.qbittorrent.requests.Session', return_value=session_mock):
    session = qbt._login('http://localhost:8080', 'admin', 'test')
  assert session is session_mock


def test_login_success_204() -> None:
  # qBittorrent 5.2+ signals success with 204 + empty body (not 'Ok.').
  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_ok_204()
  with patch('integrations.qbittorrent.requests.Session', return_value=session_mock):
    session = qbt._login('http://localhost:8080', 'admin', 'test')
  assert session is session_mock


def test_login_bad_credentials() -> None:
  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_fail()
  with patch('integrations.qbittorrent.requests.Session', return_value=session_mock):
    with pytest.raises(IntegrationDataUnavailableError, match='login failed'):
      qbt._login('http://localhost:8080', 'admin', 'wrong')


# ---------------------------------------------------------------------------
# get_variables
# ---------------------------------------------------------------------------


def test_get_variables_normal(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _patched_config())
  qbt._cache = None

  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_ok()
  session_mock.cookies = MagicMock()

  torrents = [_torrent(size=500 * 1024**3), _torrent(size=700 * 1024**3)]
  with (
    patch('integrations.qbittorrent.requests.Session', return_value=session_mock),
    patch('integrations.qbittorrent.fetch_with_retry', return_value=_torrents_response(torrents)),
  ):
    result = qbt.get_variables()

  assert result['header'] == [['[B] TORRENTS']]
  assert result['count'] == [['2 SEEDING']]
  assert result['size'] == [['1.2 TB']]
  qbt._cache = None


def test_get_variables_no_seeders(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _patched_config())
  qbt._cache = None

  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_ok()
  session_mock.cookies = MagicMock()

  with (
    patch('integrations.qbittorrent.requests.Session', return_value=session_mock),
    patch('integrations.qbittorrent.fetch_with_retry', return_value=_torrents_response([])),
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='no seeding'):
      qbt.get_variables()

  qbt._cache = None


def test_get_variables_login_failure(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _patched_config())
  qbt._cache = None

  session_mock = MagicMock()
  session_mock.post.side_effect = _requests.ConnectionError('refused')

  with patch('integrations.qbittorrent.requests.Session', return_value=session_mock):
    with pytest.raises(IntegrationDataUnavailableError, match='login failed'):
      qbt.get_variables()

  qbt._cache = None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _patched_config())

  cached_value: dict = {
    'header': [['[B] TORRENTS']],
    'count': [['5 SEEDING']],
    'size': [['2 TB']],
  }
  qbt._cache = CacheEntry(cached_value)

  with patch('integrations.qbittorrent.requests.Session') as mock_session:
    result = qbt.get_variables()
    mock_session.assert_not_called()

  assert result == cached_value
  qbt._cache = None


def test_stale_cache_served_on_login_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _patched_config())

  stale_value: dict = {
    'header': [['[B] TORRENTS']],
    'count': [['3 SEEDING']],
    'size': [['1 TB']],
  }
  qbt._cache = CacheEntry(stale_value)
  qbt._cache.cached_at = time.monotonic() - qbt._CACHE_TTL - 1

  session_mock = MagicMock()
  session_mock.post.side_effect = _requests.ConnectionError('refused')

  with patch('integrations.qbittorrent.requests.Session', return_value=session_mock):
    result = qbt.get_variables()

  assert result == stale_value
  qbt._cache = None


# ---------------------------------------------------------------------------
# verify_tls
# ---------------------------------------------------------------------------


def test_verify_tls_false_passes_verify(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  cfg = _patched_config()
  cfg['qbittorrent']['verify_tls'] = False
  monkeypatch.setattr(_config_mod, '_config', cfg)
  qbt._cache = None

  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_ok()
  session_mock.cookies = MagicMock()

  torrents = [_torrent()]
  with (
    patch('integrations.qbittorrent.requests.Session', return_value=session_mock),
    patch('integrations.qbittorrent.fetch_with_retry', return_value=_torrents_response(torrents)) as mock_fetch,
  ):
    qbt.get_variables()

  assert session_mock.verify is False
  assert mock_fetch.call_args.kwargs['verify'] is False
  qbt._cache = None


def test_verify_tls_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', _patched_config())
  qbt._cache = None

  session_mock = MagicMock()
  session_mock.post.return_value = _login_response_ok()
  session_mock.cookies = MagicMock()

  torrents = [_torrent()]
  with (
    patch('integrations.qbittorrent.requests.Session', return_value=session_mock),
    patch('integrations.qbittorrent.fetch_with_retry', return_value=_torrents_response(torrents)) as mock_fetch,
  ):
    qbt.get_variables()

  assert session_mock.verify is True
  assert mock_fetch.call_args.kwargs['verify'] is True
  qbt._cache = None
