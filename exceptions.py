# exceptions.py
#
# Shared exception types used across scheduler.py and integrations.
#
# Kept in a standalone module so that integrations can import directly
# without going through `scheduler`, which avoids the dual-module identity
# problem that arises when scheduler.py runs as __main__.


class IntegrationDataUnavailableError(Exception):
  """Raised by an integration when it has no current data to display.

  The worker skips the message silently rather than logging an error.

  Set ``expected=True`` for legitimate empty states (nothing playing, no
  events today, all monitors up).  Leave the default ``False`` for actual
  failures (HTTP errors, expired credentials, missing config).  The health
  check system uses this flag to distinguish normal gaps from real problems.
  """

  def __init__(self, message: str, *, expected: bool = False) -> None:
    super().__init__(message)
    self.expected = expected
