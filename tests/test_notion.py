from typing import Any, Generator
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


def _make_payload(
  message: str = 'task completed',
  urgent: bool = False,
  tag_select: dict[str, Any] | None = None,
  tag_missing: bool = False,
) -> dict[str, Any]:
  """Return a minimal Notion automation webhook payload."""
  properties: dict[str, Any] = {
    'message': {
      'type': 'title',
      'title': [{'type': 'text', 'plain_text': message}],
    },
    'urgent': {'type': 'checkbox', 'checkbox': urgent},
  }
  if not tag_missing:
    properties['tag'] = {'type': 'select', 'select': tag_select}
  return {
    'source': {'type': 'automation'},
    'data': {'object': 'page', 'properties': properties},
  }


@pytest.fixture(autouse=True)
def _mock_config() -> Generator[None, None, None]:
  with patch('config.get_schedule_override', return_value={}):
    yield


# --- Message handling ---


def test_valid_message_returns_webhook_message() -> None:
  result = notion.handle_webhook(_make_payload('task completed'))
  assert isinstance(result, WebhookMessage)
  assert result.data['variables'] == {'message': [['TASK COMPLETED']]}


def test_message_uppercased() -> None:
  result = notion.handle_webhook(_make_payload('hello world'))
  assert isinstance(result, WebhookMessage)
  assert result.data['variables']['message'] == [['HELLO WORLD']]


def test_multiline_message_splits_on_newlines() -> None:
  result = notion.handle_webhook(_make_payload('line one\nline two\nline three'))
  assert isinstance(result, WebhookMessage)
  assert result.data['variables']['message'] == [['LINE ONE', 'LINE TWO', 'LINE THREE']]


def test_empty_message_returns_none() -> None:
  payload = _make_payload()
  payload['data']['properties']['message']['title'] = [{'plain_text': ''}]
  assert notion.handle_webhook(payload) is None


def test_blank_only_message_returns_none() -> None:
  assert notion.handle_webhook(_make_payload('   \n  ')) is None


def test_missing_message_returns_none() -> None:
  payload = _make_payload()
  payload['data']['properties']['message']['title'] = []
  assert notion.handle_webhook(payload) is None


def test_missing_data_key_returns_none() -> None:
  assert notion.handle_webhook({'source': {'type': 'automation'}}) is None


def test_missing_properties_key_returns_none() -> None:
  assert notion.handle_webhook({'data': {'object': 'page'}}) is None


# --- urgent field ---


def test_urgent_false_by_default() -> None:
  result = notion.handle_webhook(_make_payload(urgent=False))
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is False


def test_urgent_true_sets_interrupt() -> None:
  result = notion.handle_webhook(_make_payload(urgent=True))
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is True


def test_urgent_false_explicit() -> None:
  result = notion.handle_webhook(_make_payload(urgent=False))
  assert isinstance(result, WebhookMessage)
  assert result.interrupt is False


# --- tag / supersede_tag ---


def test_default_tag_is_notion() -> None:
  result = notion.handle_webhook(_make_payload(tag_select=None))
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'notion'


def test_tag_missing_from_properties_uses_default() -> None:
  result = notion.handle_webhook(_make_payload(tag_missing=True))
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'notion'


def test_custom_tag_is_namespaced() -> None:
  result = notion.handle_webhook(_make_payload(tag_select={'name': 'reminders'}))
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == 'notion.reminders'


def test_empty_tag_disables_superseding() -> None:
  result = notion.handle_webhook(_make_payload(tag_select={'name': ''}))
  assert isinstance(result, WebhookMessage)
  assert result.supersede_tag == ''


# --- Config defaults ---


def test_default_priority_and_hold() -> None:
  result = notion.handle_webhook(_make_payload())
  assert isinstance(result, WebhookMessage)
  assert result.priority == 7
  assert result.hold == 120
  assert result.timeout == 120


def test_config_override_applied() -> None:
  with patch('config.get_schedule_override', return_value={'hold': 60, 'priority': 9}):
    result = notion.handle_webhook(_make_payload())
  assert isinstance(result, WebhookMessage)
  assert result.hold == 60
  assert result.priority == 9


# --- Error handling ---


def test_exception_returns_none(caplog: pytest.LogCaptureFixture) -> None:
  with patch.object(notion, '_load_template_config', side_effect=RuntimeError('boom')):
    result = notion.handle_webhook(_make_payload())
  assert result is None
  assert 'Notion webhook error' in caplog.text
