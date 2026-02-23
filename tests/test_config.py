import tomllib
from pathlib import Path

import pytest

import config as _mod


def _write_config(tmp_path: Path, content: str) -> None:
  (tmp_path / 'config.toml').write_text(content)


# --- load_config ---


def test_load_config_missing_file_exits(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
) -> None:
  monkeypatch.chdir(tmp_path)
  with pytest.raises(SystemExit) as exc_info:
    _mod.load_config()
  assert exc_info.value.code == 1
  assert 'config.example.toml' in capsys.readouterr().err


def test_load_config_valid_file_populates_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[vestaboard]\napi_key = "test-key"\n')
  _mod.load_config()
  assert _mod._config.get('vestaboard', {}).get('api_key') == 'test-key'


def test_load_config_invalid_toml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.chdir(tmp_path)
  (tmp_path / 'config.toml').write_text('not valid toml ={[}')
  with pytest.raises(tomllib.TOMLDecodeError):
    _mod.load_config()


# --- get ---


def test_get_required_present(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'sec': {'key': 'value'}})
  assert _mod.get('sec', 'key') == 'value'


def test_get_required_missing_section_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  with pytest.raises(ValueError, match='missing_sec'):
    _mod.get('missing_sec', 'key')


def test_get_required_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'sec': {}})
  with pytest.raises(ValueError, match='missing_key'):
    _mod.get('sec', 'missing_key')


def test_get_required_empty_string_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'sec': {'key': ''}})
  with pytest.raises(ValueError):
    _mod.get('sec', 'key')


# --- get_optional ---


def test_get_optional_present(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'sec': {'key': 'val'}})
  assert _mod.get_optional('sec', 'key') == 'val'


def test_get_optional_absent_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_optional('sec', 'key') == ''


# --- get_schedule_override ---


def test_get_schedule_override_present(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    _mod,
    '_config',
    {'bart': {'schedules': {'departures': {'cron': '*/5 7-9 * * 1-5', 'hold': 120}}}},
  )
  result = _mod.get_schedule_override('bart.departures')
  assert result == {'cron': '*/5 7-9 * * 1-5', 'hold': 120}


def test_get_schedule_override_absent(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_schedule_override('bart.departures') == {}


def test_get_schedule_override_malformed_template_id(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_schedule_override('no_dot_here') == {}
