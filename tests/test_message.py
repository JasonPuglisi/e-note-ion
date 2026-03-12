from typing import Any, Generator
from unittest.mock import call, patch

import pytest

import integrations.message as message
from scheduler import WebhookMessage

_TEMPLATE_CONFIG = {
  'hold': 120,
  'timeout': 120,
  'priority': 8,
  'truncation': 'wrap_ellipsis',
}

_FRIEND_ALICE = {'color': 'R'}


@pytest.fixture(autouse=True)
def _mock_config() -> Generator[None, None, None]:
  with (
    patch('config.get_schedule_override', return_value={}),
    patch('config.get_optional', return_value='note'),
  ):
    yield


def _make_payload(
  msg: str = 'food is ready',
  action: str = 'message',
) -> dict[str, Any]:
  return {'action': action, 'message': msg}


# ---------------------------------------------------------------------------
# Message posting
# ---------------------------------------------------------------------------


def test_valid_message_returns_webhook_message() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.data['variables'] == {'message': [['FOOD IS READY']]}


def test_message_uppercased() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload('hello world'), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.data['variables']['message'] == [['HELLO WORLD']]


def test_multiline_message_splits_on_newlines() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload('line one\nline two'), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.data['variables']['message'] == [['LINE ONE', 'LINE TWO']]


def test_empty_message_returns_none() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload(''), credential_name='alice')
  assert result is None


def test_blank_message_returns_none() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload('   \n  '), credential_name='alice')
  assert result is None


def test_missing_message_key_returns_none() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook({}, credential_name='alice')
  assert result is None


# ---------------------------------------------------------------------------
# Credential gating
# ---------------------------------------------------------------------------


def test_no_credential_returns_none() -> None:
  result = message.handle_webhook(_make_payload(), credential_name=None)
  assert result is None


def test_admin_credential_returns_none() -> None:
  # Admin (empty string = main secret) cannot post messages.
  result = message.handle_webhook(_make_payload(), credential_name='')
  assert result is None


def test_unregistered_credential_returns_none() -> None:
  with patch('config.get_message_friend', return_value=None):
    result = message.handle_webhook(_make_payload(), credential_name='unknown')
  assert result is None


# ---------------------------------------------------------------------------
# Header construction
# ---------------------------------------------------------------------------


def test_header_uses_friend_color() -> None:
  with patch('config.get_message_friend', return_value={'color': 'R'}):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.data['templates'][0]['format'][0] == '[R] FROM ALICE'


def test_header_uses_heart_color() -> None:
  with patch('config.get_message_friend', return_value={'color': 'H'}):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.data['templates'][0]['format'][0] == '❤️ FROM ALICE'


def test_header_defaults_to_white_for_unknown_color() -> None:
  with patch('config.get_message_friend', return_value={'color': 'Z'}):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.data['templates'][0]['format'][0].startswith('[W] FROM')


def test_name_hard_capped_to_note_budget() -> None:
  # Note: 15 cols. Header prefix = 7. Max name = 8.
  # credential_name 'bartholo' (8 chars) → 'BARTHOLO'
  with patch('config.get_message_friend', return_value={'color': 'W'}):
    result = message.handle_webhook(_make_payload(), credential_name='bartholo')
  assert isinstance(result, WebhookMessage)
  assert result.data['templates'][0]['format'][0] == '[W] FROM BARTHOLO'


def test_name_hard_capped_to_flagship_budget() -> None:
  # Flagship: 22 cols. Header prefix = 7. Max name = 15.
  # credential_name 'bartholomew-jones' → 'BARTHOLOMEW-JONE' (16 chars → capped at 15)
  with (
    patch('config.get_optional', return_value='flagship'),
    patch('config.get_message_friend', return_value={'color': 'W'}),
  ):
    result = message.handle_webhook(_make_payload(), credential_name='bartholomew-jones')
  assert isinstance(result, WebhookMessage)
  assert result.data['templates'][0]['format'][0] == '[W] FROM BARTHOLOMEW-JON'


def test_header_uses_credential_name() -> None:
  # Name on board comes from credential_name, not a stored display_name field.
  with patch('config.get_message_friend', return_value={'color': 'G'}):
    result = message.handle_webhook(_make_payload(), credential_name='bob')
  assert isinstance(result, WebhookMessage)
  assert result.data['templates'][0]['format'][0] == '[G] FROM BOB'


# ---------------------------------------------------------------------------
# Supersede tag and queue fields
# ---------------------------------------------------------------------------


def test_supersede_tag_is_per_friend() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'message.alice'


def test_default_priority_and_hold() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.priority == 8
  assert result.hold == 120
  assert result.timeout == 120


def test_config_override_applied() -> None:
  with (
    patch('config.get_schedule_override', return_value={'hold': 60, 'priority': 7}),
    patch('config.get_message_friend', return_value=_FRIEND_ALICE),
  ):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.hold == 60
  assert result.priority == 7


def test_interrupt_always_false() -> None:
  with patch('config.get_message_friend', return_value=_FRIEND_ALICE):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is False


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _make_register_payload(
  name: str = 'alice',
  color: str = 'R',
  passphrase: str = 'apple-river-bench',
) -> dict[str, Any]:
  return {
    'action': 'register',
    'name': name,
    'color': color,
    'passphrase': passphrase,
  }


def test_register_requires_admin() -> None:
  # Friend credential (non-empty) must be rejected.
  result = message.handle_webhook(_make_register_payload(), credential_name='alice')
  assert result is None


def test_register_with_none_credential_rejected() -> None:
  result = message.handle_webhook(_make_register_payload(), credential_name=None)
  assert result is None


def test_register_stores_credential_and_friend() -> None:
  with patch('config.write_config_section') as mock_write:
    result = message.handle_webhook(_make_register_payload(), credential_name='')
  assert result is None
  assert mock_write.call_count == 2
  # First call: credential section
  cred_call = mock_write.call_args_list[0]
  assert cred_call == call(
    'webhook.credentials.alice',
    {
      'secret_hash': cred_call.args[1]['secret_hash'],  # argon2 hash varies
      'webhooks': ['message'],
    },
  )
  assert cred_call.args[1]['secret_hash'].startswith('$argon2id$')
  # Second call: friend display section (no display_name — name is the identity)
  assert mock_write.call_args_list[1] == call(
    'message.friends.alice',
    {'color': 'R'},
  )


def test_register_overwrite_is_idempotent() -> None:
  with patch('config.write_config_section') as mock_write:
    message.handle_webhook(_make_register_payload(), credential_name='')
    message.handle_webhook(_make_register_payload(), credential_name='')
  assert mock_write.call_count == 4  # 2 calls per registration


def test_register_spaces_in_name_converted_to_hyphens() -> None:
  with patch('config.write_config_section') as mock_write:
    message.handle_webhook(_make_register_payload(name='Bob Smith'), credential_name='')
  assert mock_write.call_args_list[0].args[0] == 'webhook.credentials.bob-smith'


def test_register_invalid_name_returns_none(caplog: pytest.LogCaptureFixture) -> None:
  result = message.handle_webhook(_make_register_payload(name='!invalid'), credential_name='')
  assert result is None
  assert 'invalid' in caplog.text.lower()


def test_register_invalid_color_returns_none(caplog: pytest.LogCaptureFixture) -> None:
  result = message.handle_webhook(_make_register_payload(color='Z'), credential_name='')
  assert result is None
  assert 'invalid' in caplog.text.lower()


def test_register_full_color_name_accepted() -> None:
  with patch('config.write_config_section') as mock_write:
    message.handle_webhook(_make_register_payload(color='Green'), credential_name='')
  friend_call = mock_write.call_args_list[1]
  assert friend_call.args[1]['color'] == 'G'


def test_register_full_color_name_case_insensitive() -> None:
  with patch('config.write_config_section') as mock_write:
    message.handle_webhook(_make_register_payload(color='VIOLET'), credential_name='')
  friend_call = mock_write.call_args_list[1]
  assert friend_call.args[1]['color'] == 'V'


def test_register_heart_full_name_accepted() -> None:
  with patch('config.write_config_section') as mock_write:
    message.handle_webhook(_make_register_payload(color='Heart'), credential_name='')
  friend_call = mock_write.call_args_list[1]
  assert friend_call.args[1]['color'] == 'H'


def test_register_short_passphrase_returns_none(caplog: pytest.LogCaptureFixture) -> None:
  result = message.handle_webhook(_make_register_payload(passphrase='abc'), credential_name='')
  assert result is None
  assert 'short' in caplog.text.lower()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_exception_returns_none(caplog: pytest.LogCaptureFixture) -> None:
  with patch('config.get_message_friend', side_effect=RuntimeError('boom')):
    result = message.handle_webhook(_make_payload(), credential_name='alice')
  assert result is None
  assert 'Message webhook error' in caplog.text
