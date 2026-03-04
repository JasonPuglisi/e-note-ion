from typing import Generator
from unittest.mock import patch

import pytest

import integrations.notion as notion
from scheduler import WebhookMessage

_TEMPLATE_CONFIG = {
  'hold': 120,
  'timeout': 120,
  'priority': 7,
  'truncation': 'word',
  'templates': [{'format': ['[W] FROM NOTION', '{message}']}],
}


@pytest.fixture(autouse=True)
def _mock_config() -> Generator[None, None, None]:
  with patch('config.get_schedule_override', return_value={}):
    yield


# --- Message handling ---


def test_valid_message_returns_webhook_message() -> None:
  result = notion.handle_webhook({'message': 'task completed'})
  assert isinstance(result, WebhookMessage)
  assert result.data['variables'] == {'message': [['TASK COMPLETED']]}


def test_message_uppercased() -> None:
  result = notion.handle_webhook({'message': 'hello world'})
  assert isinstance(result, WebhookMessage)
  assert result.data['variables']['message'] == [['HELLO WORLD']]


def test_multiline_message_splits_on_newlines() -> None:
  result = notion.handle_webhook({'message': 'line one\nline two\nline three'})
  assert isinstance(result, WebhookMessage)
  assert result.data['variables']['message'] == [['LINE ONE', 'LINE TWO', 'LINE THREE']]


def test_empty_message_returns_none() -> None:
  assert notion.handle_webhook({'message': ''}) is None


def test_blank_only_message_returns_none() -> None:
  assert notion.handle_webhook({'message': '   \n  '}) is None


def test_missing_message_returns_none() -> None:
  assert notion.handle_webhook({}) is None


def test_non_string_message_returns_none() -> None:
  assert notion.handle_webhook({'message': 42}) is None


# --- urgent field ---


def test_urgent_false_by_default() -> None:
  result = notion.handle_webhook({'message': 'hello'})
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is False


def test_urgent_true_sets_interrupt() -> None:
  result = notion.handle_webhook({'message': 'hello', 'urgent': True})
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is True


def test_urgent_false_explicit() -> None:
  result = notion.handle_webhook({'message': 'hello', 'urgent': False})
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is False


# --- tag / supersede_tag ---


def test_default_tag_is_notion() -> None:
  result = notion.handle_webhook({'message': 'hello'})
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'notion'


def test_custom_tag_is_namespaced() -> None:
  result = notion.handle_webhook({'message': 'hello', 'tag': 'reminders'})
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'notion.reminders'


def test_empty_tag_disables_superseding() -> None:
  result = notion.handle_webhook({'message': 'hello', 'tag': ''})
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == ''


def test_non_string_tag_falls_back_to_notion() -> None:
  result = notion.handle_webhook({'message': 'hello', 'tag': 123})
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'notion'


# --- Config defaults ---


def test_default_priority_and_hold() -> None:
  result = notion.handle_webhook({'message': 'hello'})
  assert isinstance(result, WebhookMessage)
  assert result.priority == 7
  assert result.hold == 120
  assert result.timeout == 120


def test_config_override_applied() -> None:
  with patch('config.get_schedule_override', return_value={'hold': 60, 'priority': 9}):
    result = notion.handle_webhook({'message': 'hello'})
  assert isinstance(result, WebhookMessage)
  assert result.hold == 60
  assert result.priority == 9


# --- Error handling ---


def test_exception_returns_none(caplog: pytest.LogCaptureFixture) -> None:
  with patch.object(notion, '_load_template_config', side_effect=RuntimeError('boom')):
    result = notion.handle_webhook({'message': 'hello'})
  assert result is None
  assert 'Notion webhook error' in caplog.text
