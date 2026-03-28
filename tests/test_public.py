from unittest.mock import patch

import public as _mod


def test_init_loads_from_config_true() -> None:
  with patch('config.get_public_mode', return_value=True):
    _mod.init()
  assert _mod.is_public() is True
  # Reset for other tests.
  _mod._public = False


def test_init_loads_from_config_false() -> None:
  with patch('config.get_public_mode', return_value=False):
    _mod.init()
  assert _mod.is_public() is False


def test_set_public_true() -> None:
  _mod._public = False
  with patch('config.write_config_section') as mock_write:
    _mod.set_public(True)
  assert _mod.is_public() is True
  mock_write.assert_called_once_with('scheduler', {'public': True})
  _mod._public = False


def test_set_public_false() -> None:
  _mod._public = True
  with patch('config.write_config_section') as mock_write:
    _mod.set_public(False)
  assert _mod.is_public() is False
  mock_write.assert_called_once_with('scheduler', {'public': False})


def test_set_public_noop_when_unchanged() -> None:
  _mod._public = True
  with patch('config.write_config_section') as mock_write:
    _mod.set_public(True)
  mock_write.assert_not_called()
  _mod._public = False


def test_is_public_default_false() -> None:
  _mod._public = False
  assert _mod.is_public() is False


def test_set_public_true_fires_changed_event() -> None:
  _mod._public = False
  _mod._changed.clear()
  with patch('config.write_config_section'):
    _mod.set_public(True)
  assert _mod._changed.is_set()
  _mod._public = False
  _mod._changed.clear()


def test_set_public_false_does_not_fire_changed_event() -> None:
  _mod._public = True
  _mod._changed.clear()
  with patch('config.write_config_section'):
    _mod.set_public(False)
  assert not _mod._changed.is_set()


def test_changed_event_not_fired_on_noop() -> None:
  _mod._public = True
  _mod._changed.clear()
  with patch('config.write_config_section') as mock_write:
    _mod.set_public(True)
  assert not _mod._changed.is_set()
  mock_write.assert_not_called()
  _mod._public = False
