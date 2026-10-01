from unittest.mock import patch

import pytest

import integrations.scheduler as _mod


def test_handle_webhook_quiet() -> None:
  with patch('quiet.set_quiet') as mock:
    result = _mod.handle_webhook({'action': 'quiet'})
  mock.assert_called_once_with(True)
  assert result is None


def test_handle_webhook_wake() -> None:
  with patch('quiet.set_quiet') as mock:
    result = _mod.handle_webhook({'action': 'wake'})
  mock.assert_called_once_with(False)
  assert result is None


def test_handle_webhook_wake_with_delay_schedules() -> None:
  with patch('quiet.schedule_wake') as mock_schedule, patch('quiet.set_quiet') as mock_set:
    result = _mod.handle_webhook({'action': 'wake', 'delay': 300})
  mock_schedule.assert_called_once_with(300.0)
  mock_set.assert_not_called()
  assert result is None


def test_handle_webhook_wake_delay_numeric_string() -> None:
  with patch('quiet.schedule_wake') as mock_schedule:
    _mod.handle_webhook({'action': 'wake', 'delay': ' 300 '})
  mock_schedule.assert_called_once_with(300.0)


def test_handle_webhook_wake_delay_zero_wakes_now() -> None:
  with patch('quiet.schedule_wake') as mock_schedule, patch('quiet.set_quiet') as mock_set:
    _mod.handle_webhook({'action': 'wake', 'delay': 0})
  mock_set.assert_called_once_with(False)
  mock_schedule.assert_not_called()


def test_handle_webhook_wake_delay_at_max() -> None:
  with patch('quiet.schedule_wake') as mock_schedule:
    _mod.handle_webhook({'action': 'wake', 'delay': 3600})
  mock_schedule.assert_called_once_with(3600.0)


@pytest.mark.parametrize(
  'delay',
  [True, False, -1, 3601, 'abc', '', float('nan'), float('inf'), 'inf', None, [300], {'s': 300}],
)
def test_handle_webhook_wake_invalid_delay(delay: object) -> None:
  with (
    patch('quiet.schedule_wake') as mock_schedule,
    patch('quiet.set_quiet') as mock_set,
    pytest.raises(ValueError, match='Invalid wake delay'),
  ):
    _mod.handle_webhook({'action': 'wake', 'delay': delay})
  mock_schedule.assert_not_called()
  mock_set.assert_not_called()


@pytest.mark.parametrize('action', ['quiet', 'public', 'private'])
def test_handle_webhook_delay_rejected_for_other_actions(action: str) -> None:
  with (
    patch('quiet.set_quiet') as mock_quiet,
    patch('public.set_public') as mock_public,
    pytest.raises(ValueError, match='only supported for the wake action'),
  ):
    _mod.handle_webhook({'action': action, 'delay': 300})
  mock_quiet.assert_not_called()
  mock_public.assert_not_called()


def test_handle_webhook_public() -> None:
  with patch('public.set_public') as mock:
    result = _mod.handle_webhook({'action': 'public'})
  mock.assert_called_once_with(True)
  assert result is None


def test_handle_webhook_private() -> None:
  with patch('public.set_public') as mock:
    result = _mod.handle_webhook({'action': 'private'})
  mock.assert_called_once_with(False)
  assert result is None


def test_handle_webhook_invalid_action() -> None:
  with pytest.raises(ValueError, match='Invalid scheduler action'):
    _mod.handle_webhook({'action': 'sleep'})


def test_handle_webhook_missing_action() -> None:
  with pytest.raises(ValueError, match='Invalid scheduler action'):
    _mod.handle_webhook({})


def test_handle_webhook_accepts_credential_name() -> None:
  """credential_name kwarg is accepted (even though unused)."""
  with patch('quiet.set_quiet'):
    result = _mod.handle_webhook({'action': 'quiet'}, credential_name='scheduler')
  assert result is None
