import pytest

from exceptions import IntegrationDataUnavailableError

# --- IntegrationDataUnavailableError ---


def test_is_exception_subclass() -> None:
  assert issubclass(IntegrationDataUnavailableError, Exception)


def test_message_preserved() -> None:
  err = IntegrationDataUnavailableError('nothing playing')
  assert str(err) == 'nothing playing'


def test_can_be_raised_and_caught() -> None:
  with pytest.raises(IntegrationDataUnavailableError, match='auth pending'):
    raise IntegrationDataUnavailableError('auth pending')


def test_caught_as_base_exception() -> None:
  with pytest.raises(Exception):
    raise IntegrationDataUnavailableError('caught as base')
