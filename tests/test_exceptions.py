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


# --- expected flag ---


def test_expected_defaults_false() -> None:
  err = IntegrationDataUnavailableError('API failed')
  assert err.expected is False


def test_expected_true() -> None:
  err = IntegrationDataUnavailableError('nothing playing', expected=True)
  assert err.expected is True
  assert str(err) == 'nothing playing'


def test_expected_false_explicit() -> None:
  err = IntegrationDataUnavailableError('HTTP 401', expected=False)
  assert err.expected is False
