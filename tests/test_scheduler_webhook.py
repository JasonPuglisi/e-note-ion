from unittest.mock import patch

import pytest

import integrations.scheduler as _mod


def test_handle_webhook_quiet() -> None:
  with patch('quiet.activate') as mock:
    result = _mod.handle_webhook({'action': 'quiet'})
  mock.assert_called_once()
  assert result is None


def test_handle_webhook_wake() -> None:
  with patch('quiet.deactivate') as mock:
    result = _mod.handle_webhook({'action': 'wake'})
  mock.assert_called_once()
  assert result is None


def test_handle_webhook_invalid_action() -> None:
  with pytest.raises(ValueError, match='Invalid scheduler action'):
    _mod.handle_webhook({'action': 'sleep'})


def test_handle_webhook_missing_action() -> None:
  with pytest.raises(ValueError, match='Invalid scheduler action'):
    _mod.handle_webhook({})


def test_handle_webhook_accepts_credential_name() -> None:
  """credential_name kwarg is accepted (even though unused)."""
  with patch('quiet.activate'):
    result = _mod.handle_webhook({'action': 'quiet'}, credential_name='scheduler')
  assert result is None
