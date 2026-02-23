# exceptions.py
#
# Shared exception types used across scheduler.py and integrations.
#
# Kept in a standalone module so that integrations can import directly
# without going through `scheduler`, which avoids the dual-module identity
# problem that arises when scheduler.py runs as __main__.


class IntegrationDataUnavailableError(Exception):
  """Raised by an integration when it has no current data to display.

  The worker skips the message silently rather than logging an error. Use
  this for expected empty states (e.g. nothing currently playing, empty
  calendar window, auth pending).
  """
