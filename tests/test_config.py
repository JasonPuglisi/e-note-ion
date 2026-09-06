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


def test_get_optional_bool_dotted_section(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'quiet': {'active': True}}})
  assert _mod.get_optional_bool('scheduler.quiet', 'active') is True


def test_get_optional_bool_dotted_section_false(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'quiet': {'active': False}}})
  assert _mod.get_optional_bool('scheduler.quiet', 'active') is False


def test_get_optional_bool_dotted_section_absent(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {}})
  assert _mod.get_optional_bool('scheduler.quiet', 'active') is False


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


def test_get_content_enabled_absent_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {})
  assert _mod.get_content_enabled() is None


def test_get_content_enabled_empty_list_returns_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
  """Explicit empty list is distinct from absent — returns set(), not None."""
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


def test_write_section_values_appends_before_trailing_blank_lines(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """New key must land before the inter-section blank line, not after it.

  Reproduces the bug where auto-generating a webhook secret produced:

    [webhook]
    bind = "0.0.0.0"

    secret = "..."
    [vestaboard]

  instead of:

    [webhook]
    bind = "0.0.0.0"
    secret = "..."

    [vestaboard]
  """
  _write_config(tmp_path, '[webhook]\nbind = "0.0.0.0"\n\n[vestaboard]\napi_key = "x"\n')
  monkeypatch.chdir(tmp_path)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  monkeypatch.setattr(_mod, '_config', {})
  _mod.write_section_values('webhook', {'secret': 'tok'})
  text = (tmp_path / 'config.toml').read_text()
  # The new key must appear before the blank line that separates sections.
  secret_pos = text.index('secret = "tok"')
  blank_pos = text.index('\n\n')
  assert secret_pos < blank_pos, 'new key was appended after the section separator blank line'


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


# --- write_config_section ---


def _setup_wcs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> Path:
  cfg = tmp_path / 'config.toml'
  cfg.write_text(content)
  monkeypatch.setattr(_mod, '_CONFIG_PATH', cfg)
  monkeypatch.setattr(_mod, '_config', {})
  return cfg


def test_write_config_section_creates_new_section(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  cfg = _setup_wcs(tmp_path, monkeypatch, '[webhook]\nsecret = "abc"\n')
  _mod.write_config_section('webhook.credentials.alice', {'secret_hash': 'hash1', 'webhooks': ['message']})
  text = cfg.read_text()
  assert '[webhook.credentials.alice]' in text
  assert 'secret_hash = "hash1"' in text
  assert 'webhooks = ["message"]' in text


def test_write_config_section_updates_existing_section(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  cfg = _setup_wcs(tmp_path, monkeypatch, '[webhook.credentials.alice]\nsecret_hash = "old"\nwebhooks = ["message"]\n')
  _mod.write_config_section('webhook.credentials.alice', {'secret_hash': 'new'})
  text = cfg.read_text()
  assert text.count('[webhook.credentials.alice]') == 1
  assert 'secret_hash = "new"' in text


def test_write_config_section_groups_siblings_together(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # Second credential must be inserted after the first, before unrelated sections.
  content = (
    '[webhook]\nsecret = "x"\n\n'
    '[webhook.credentials.alice]\nsecret_hash = "a"\nwebhooks = ["message"]\n\n'
    '[message.friends.alice]\ncolor = "R"\n'
  )
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.write_config_section('webhook.credentials.bob', {'secret_hash': 'b', 'webhooks': ['message']})
  text = cfg.read_text()
  alice_pos = text.index('[webhook.credentials.alice]')
  bob_pos = text.index('[webhook.credentials.bob]')
  friends_pos = text.index('[message.friends.alice]')
  assert alice_pos < bob_pos < friends_pos


def test_write_config_section_no_double_blank_lines(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  content = (
    '[webhook]\nsecret = "x"\n\n'
    '[webhook.credentials.alice]\nsecret_hash = "a"\nwebhooks = ["message"]\n\n'
    '[message.friends.alice]\ncolor = "R"\n'
  )
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.write_config_section('webhook.credentials.bob', {'secret_hash': 'b', 'webhooks': ['message']})
  assert '\n\n\n' not in cfg.read_text()


def test_write_config_section_ends_with_newline(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  cfg = _setup_wcs(tmp_path, monkeypatch, '[webhook]\nsecret = "x"\n')
  _mod.write_config_section('message.friends.alice', {'color': 'R'})
  assert cfg.read_text().endswith('\n')
  assert not cfg.read_text().endswith('\n\n')


def test_write_config_section_updates_memory_cache(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  _setup_wcs(tmp_path, monkeypatch, '[webhook]\nsecret = "x"\n')
  _mod.write_config_section('webhook.credentials.alice', {'secret_hash': 'h', 'webhooks': ['message']})
  assert _mod._config['webhook']['credentials']['alice']['secret_hash'] == 'h'  # noqa: SLF001


# --- delete_config_section ---


def test_delete_config_section_removes_section(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  content = (
    '[webhook]\nport = 8080\n\n'
    '[webhook.credentials.alice]\nsecret_hash = "h"\nwebhooks = ["message"]\n\n'
    '[message.friends.alice]\ncolor = "R"\n'
  )
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.delete_config_section('webhook.credentials.alice')
  text = cfg.read_text()
  assert '[webhook.credentials.alice]' not in text
  assert 'secret_hash' not in text
  assert '[webhook]' in text
  assert '[message.friends.alice]' in text


def test_delete_config_section_preserves_separator_for_next_section(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # The blank line before the deleted section must survive as separator for the next.
  content = (
    '[webhook]\nport = 8080\n\n[webhook.credentials.alice]\nsecret_hash = "h"\n\n[message.friends.alice]\ncolor = "R"\n'
  )
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.delete_config_section('webhook.credentials.alice')
  text = cfg.read_text()
  assert '\n\n[message.friends.alice]' in text


def test_delete_config_section_no_double_blank_lines(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  content = (
    '[webhook]\nport = 8080\n\n[webhook.credentials.alice]\nsecret_hash = "h"\n\n[message.friends.alice]\ncolor = "R"\n'
  )
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.delete_config_section('webhook.credentials.alice')
  assert '\n\n\n' not in cfg.read_text()


def test_delete_config_section_last_section_no_trailing_blank(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  content = '[webhook]\nport = 8080\n\n[webhook.credentials.alice]\nsecret_hash = "h"\n'
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.delete_config_section('webhook.credentials.alice')
  text = cfg.read_text()
  assert text.endswith('8080\n')
  assert not text.endswith('\n\n')


def test_delete_config_section_noop_when_absent(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  content = '[webhook]\nport = 8080\n'
  cfg = _setup_wcs(tmp_path, monkeypatch, content)
  _mod.delete_config_section('webhook.credentials.alice')
  assert cfg.read_text() == content


def test_delete_config_section_updates_memory_cache(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  content = '[webhook]\nport = "8080"\n\n[webhook.credentials.alice]\nsecret_hash = "h"\nwebhooks = ["message"]\n'
  _setup_wcs(tmp_path, monkeypatch, content)
  _mod._config = {  # noqa: SLF001
    'webhook': {'port': '8080', 'credentials': {'alice': {'secret_hash': 'h', 'webhooks': ['message']}}},
  }
  _mod.delete_config_section('webhook.credentials.alice')
  assert 'alice' not in _mod._config['webhook']['credentials']  # noqa: SLF001


def test_delete_config_section_missing_file_raises(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(_mod, '_CONFIG_PATH', tmp_path / 'config.toml')
  with pytest.raises(FileNotFoundError):
    _mod.delete_config_section('webhook.credentials.alice')


# --- 2.0 credential namespace (#431) ---


def test_flat_message_credentials_no_longer_authenticate(monkeypatch: pytest.MonkeyPatch) -> None:
  """The breaking change: message credentials live only in the nested namespace."""
  monkeypatch.setattr(
    _mod,
    '_config',
    {
      'webhook': {
        'credentials': {
          'alice': {'secret_hash': 'h', 'webhooks': ['message']},
          'message': {'admin': {'secret_hash': 'h2'}},
        }
      }
    },
  )
  assert sorted(_mod.get_credentials('message')) == ['admin'], 'flat alice must not be returned'


def test_flat_credentials_still_work_for_other_integrations(monkeypatch: pytest.MonkeyPatch) -> None:
  """Only message moved to a nested namespace; everything else stays flat."""
  monkeypatch.setattr(
    _mod,
    '_config',
    {'webhook': {'credentials': {'plex': {'secret_hash': 'h', 'webhooks': ['plex']}}}},
  )
  assert sorted(_mod.get_credentials('plex')) == ['plex']


# --- boolean config values are not coerced ---


@pytest.mark.parametrize(
  'value',
  [
    pytest.param({'active': False}, id='table (the pre-2.0 [scheduler.quiet] shape)'),
    pytest.param({'active': True}, id='table-truthy'),
    pytest.param('false', id='string-false'),
    pytest.param('true', id='string-true'),
    pytest.param(0, id='int'),
  ],
)
def test_non_bool_boolean_value_exits_with_a_clear_error(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  value: object,
) -> None:
  """bool() would coerce these, and coercion is how a user gets the opposite
  of what they asked for: bool({'active': False}) and bool('false') are both
  True. Refusing is the only safe reading."""
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'quiet': value}})
  with pytest.raises(SystemExit) as exc:
    _mod.get_optional_bool('scheduler', 'quiet')
  assert exc.value.code == 1
  err = capsys.readouterr().err
  assert 'must be true or false' in err
  assert 'quiet' in err


@pytest.mark.parametrize(('value', 'expected'), [(True, True), (False, False)])
def test_real_booleans_pass_through(monkeypatch: pytest.MonkeyPatch, value: bool, expected: bool) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {'quiet': value}})
  assert _mod.get_optional_bool('scheduler', 'quiet') is expected


def test_absent_boolean_uses_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(_mod, '_config', {'scheduler': {}})
  assert _mod.get_optional_bool('scheduler', 'quiet', default=True) is True


# --- TOML string escaping (#592) ---


@pytest.mark.parametrize(
  'raw',
  [
    'plain',
    'has "quotes"',
    'back\\slash',
    'both "q" and \\s',
    'new\nline',
    'carriage\rreturn',
    'tab\there',
    'null\x00byte',
    'del\x7fchar',
  ],
)
def test_toml_str_round_trips_through_a_real_write(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  raw: str,
) -> None:
  """Any string written must parse back identically.

  OAuth tokens (Trakt, Google) reach write_section_values unvalidated. Before
  #592 a value containing a quote or backslash produced a config.toml that
  tomllib could no longer read — taking every other credential with it.
  """
  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[myapp]\naccess_token = "old"\n')
  _mod.load_config()

  _mod.write_section_values('myapp', {'access_token': raw})

  parsed = tomllib.loads((tmp_path / 'config.toml').read_text())
  assert parsed['myapp']['access_token'] == raw


def test_toml_str_escapes_list_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[webhook]\n')
  _mod.load_config()

  _mod.write_config_section('webhook.credentials.alice', {'secret_hash': 'h', 'webhooks': ['mes"sage', 'pl\\ex']})

  parsed = tomllib.loads((tmp_path / 'config.toml').read_text())
  assert parsed['webhook']['credentials']['alice']['webhooks'] == ['mes"sage', 'pl\\ex']


# --- atomic write (#592) ---


def test_atomic_write_leaves_no_temp_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[myapp]\nkey = "old"\n')
  _mod.load_config()

  _mod.write_section_values('myapp', {'key': 'new'})

  assert list(tmp_path.glob('.config.toml.*')) == []
  assert tomllib.loads((tmp_path / 'config.toml').read_text())['myapp']['key'] == 'new'


def test_atomic_write_preserves_file_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """config.toml holds every credential — a write must not widen its mode."""
  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[myapp]\nkey = "old"\n')
  (tmp_path / 'config.toml').chmod(0o600)
  _mod.load_config()

  _mod.write_section_values('myapp', {'key': 'new'})

  assert (tmp_path / 'config.toml').stat().st_mode & 0o777 == 0o600


def test_atomic_write_falls_back_when_replace_is_refused(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Docker bind-mounts config.toml as a single file, which pins the inode and
  makes os.replace fail with EBUSY. The write must still land."""
  import errno as _errno
  import os as _os

  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[myapp]\nkey = "old"\n')
  _mod.load_config()

  original_inode = (tmp_path / 'config.toml').stat().st_ino

  def _refuse(src: object, dst: object) -> None:
    raise OSError(_errno.EBUSY, 'Device or resource busy')

  monkeypatch.setattr(_os, 'replace', _refuse)
  _mod.write_section_values('myapp', {'key': 'new'})

  assert tomllib.loads((tmp_path / 'config.toml').read_text())['myapp']['key'] == 'new'
  assert list(tmp_path.glob('.config.toml.*')) == []
  # In-place write keeps the inode, which is exactly why it survives a bind mount.
  assert (tmp_path / 'config.toml').stat().st_ino == original_inode


def test_atomic_write_reraises_unexpected_oserror(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """A genuine failure (disk full, say) must not be swallowed by the fallback."""
  import errno as _errno
  import os as _os

  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[myapp]\nkey = "old"\n')
  _mod.load_config()

  def _boom(src: object, dst: object) -> None:
    raise OSError(_errno.ENOSPC, 'No space left on device')

  monkeypatch.setattr(_os, 'replace', _boom)
  with pytest.raises(OSError):
    _mod.write_section_values('myapp', {'key': 'new'})

  assert list(tmp_path.glob('.config.toml.*')) == []


# --- write serialisation (#592) ---


def test_concurrent_writers_do_not_lose_updates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
  """The webhook thread and APScheduler job threads both write config.

  Without the lock these interleave: each reads the file, mutates its own copy,
  and the last writer wins, silently discarding the others. Refreshed OAuth
  tokens are the most likely casualty.
  """
  import threading

  monkeypatch.chdir(tmp_path)
  _write_config(tmp_path, '[myapp]\nseed = "0"\n')
  _mod.load_config()

  n = 12
  start = threading.Barrier(n)
  errors: list[BaseException] = []

  def _writer(i: int) -> None:
    try:
      start.wait(timeout=10)
      _mod.write_section_values('myapp', {f'key{i}': str(i)})
    except BaseException as e:  # noqa: BLE001 — surfaced via the errors list
      errors.append(e)

  threads = [threading.Thread(target=_writer, args=(i,)) for i in range(n)]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=30)

  assert not errors
  parsed = tomllib.loads((tmp_path / 'config.toml').read_text())
  assert sorted(k for k in parsed['myapp'] if k.startswith('key')) == sorted(f'key{i}' for i in range(n))


# --- OAuth state subsections (#486) ---


def test_auth_subsection_is_written_after_the_parents_keys(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """TOML section headers are positional, so placement is correctness.

  If [trakt.auth] were inserted above a plain [trakt] key, that key would
  silently become part of the subsection and stop being read.
  """
  monkeypatch.chdir(tmp_path)
  _write_config(
    tmp_path,
    '[scheduler]\nmodel = "note"\n\n[trakt]\nclient_id = "cid"\ncalendar_days = 14\n\n[weather]\ncity = "Oakland"\n',
  )
  _mod.load_config()

  _mod.write_config_section('trakt.auth', {'access_token': 'tok', 'expires_at': 999})

  parsed = tomllib.loads((tmp_path / 'config.toml').read_text())
  assert parsed['trakt']['calendar_days'] == 14, 'a sibling key must not be swallowed'
  assert parsed['trakt']['client_id'] == 'cid'
  assert parsed['trakt']['auth']['access_token'] == 'tok'
  assert parsed['weather']['city'] == 'Oakland', 'later sections must be untouched'


def test_dotted_sections_read_through_every_accessor(monkeypatch: pytest.MonkeyPatch) -> None:
  """get() and get_optional() did not walk dotted names; get_optional_bool did.

  [google.auth] nests as _config['google']['auth'], so an accessor that does not
  walk it reads the section as absent rather than failing.
  """
  monkeypatch.setattr(
    _mod,
    '_config',
    {'google': {'client_id': 'cid', 'auth': {'access_token': 'tok', 'enabled': True}}},
  )
  assert _mod.get('google.auth', 'access_token') == 'tok'
  assert _mod.get_optional('google.auth', 'access_token') == 'tok'
  assert _mod.get_optional_bool('google.auth', 'enabled') is True
  assert _mod.get('google', 'client_id') == 'cid', 'the parent section still resolves'
  assert _mod.get_optional('google.auth', 'absent', 'fallback') == 'fallback'


def test_missing_dotted_key_names_the_full_section() -> None:
  with pytest.raises(ValueError, match=r'\[google\.auth\]\.access_token'):
    _mod.get('google.auth', 'access_token')
