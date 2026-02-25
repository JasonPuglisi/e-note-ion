import tomllib
from pathlib import Path
from zoneinfo import ZoneInfo

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


# --- get_optional_bool ---


def test_get_optional_bool_absent_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_optional_bool('scheduler', 'public') is False


def test_get_optional_bool_absent_custom_default(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_optional_bool('scheduler', 'public', default=True) is True


def test_get_optional_bool_true(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'public': True}})
  assert _mod.get_optional_bool('scheduler', 'public') is True


def test_get_optional_bool_false(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'public': False}})
  assert _mod.get_optional_bool('scheduler', 'public') is False


# --- get_model ---


def test_get_model_absent_returns_note(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_model() == 'note'


def test_get_model_flagship(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'model': 'flagship'}})
  assert _mod.get_model() == 'flagship'


def test_get_model_note_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'model': 'note'}})
  assert _mod.get_model() == 'note'


def test_get_model_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'model': 'banana'}})
  with pytest.raises(ValueError, match='banana'):
    _mod.get_model()


# --- get_public_mode ---


def test_get_public_mode_absent_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_public_mode() is False


def test_get_public_mode_true(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'public': True}})
  assert _mod.get_public_mode() is True


# --- get_content_enabled ---


def test_get_content_enabled_absent_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_content_enabled() == set()


def test_get_content_enabled_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'content_enabled': []}})
  assert _mod.get_content_enabled() == set()


def test_get_content_enabled_all(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'content_enabled': ['*']}})
  assert _mod.get_content_enabled() == {'*'}


def test_get_content_enabled_stems(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'content_enabled': ['bart', 'trakt']}})
  assert _mod.get_content_enabled() == {'bart', 'trakt'}


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


# --- get_timezone ---


def test_get_timezone_absent_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_timezone() is None


def test_get_timezone_valid_returns_zone_info(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'timezone': 'America/Los_Angeles'}})
  result = _mod.get_timezone()
  assert result == ZoneInfo('America/Los_Angeles')


def test_get_timezone_invalid_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'timezone': 'Not/ATimezone'}})
  with pytest.raises(ValueError, match='Not/ATimezone'):
    _mod.get_timezone()


# --- write_section_values ---


def test_write_section_values_updates_existing_key(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _write_config(tmp_path, '[myapp]\naccess_token = "old"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  _mod.write_section_values('myapp', {'access_token': 'new'})
  text = (tmp_path / 'config.toml').read_text()
  assert 'access_token = "new"' in text
  assert 'old' not in text


def test_write_section_values_replaces_commented_key(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _write_config(tmp_path, '[myapp]\n# access_token = "placeholder"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  _mod.write_section_values('myapp', {'access_token': 'tok123'})
  text = (tmp_path / 'config.toml').read_text()
  assert 'access_token = "tok123"' in text
  assert '# access_token' not in text


def test_write_section_values_appends_new_key(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _write_config(tmp_path, '[myapp]\nexisting = "val"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  _mod.write_section_values('myapp', {'new_key': 'added'})
  text = (tmp_path / 'config.toml').read_text()
  assert 'new_key = "added"' in text
  assert 'existing = "val"' in text


def test_write_section_values_preserves_other_sections(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _write_config(tmp_path, '[other]\nfoo = "bar"\n\n[myapp]\nkey = "old"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  _mod.write_section_values('myapp', {'key': 'new'})
  text = (tmp_path / 'config.toml').read_text()
  assert 'foo = "bar"' in text
  assert 'key = "new"' in text


def test_write_section_values_section_not_found_raises(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _write_config(tmp_path, '[other]\nfoo = "bar"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  with pytest.raises(ValueError, match='missing'):
    _mod.write_section_values('missing', {'key': 'val'})


def test_write_section_values_missing_file_raises(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  with pytest.raises(FileNotFoundError):
    _mod.write_section_values('myapp', {'key': 'val'})


def test_write_section_values_updates_memory_cache(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _write_config(tmp_path, '[myapp]\ntoken = "old"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  cache: dict = {}
  monkeypatch.setattr(_mod, '_config', cache)
  _mod.write_section_values('myapp', {'token': 'fresh'})
  assert cache.get('myapp', {}).get('token') == 'fresh'
