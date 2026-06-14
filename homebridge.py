# homebridge.py
#
# Optional outbound push notifier for the HomeBridge / Apple Home integration.
# When [homebridge] is configured in config.toml, a quiet- or public-mode
# transition fires a small HTTP POST to a configured endpoint so HomeKit
# switches update instantly instead of waiting for the next poll of /state.
#
# Fully optional: a no-op when [homebridge] is absent. The POST runs in a daemon
# thread so toggling latency never depends on HomeBridge being reachable, and
# all failures are caught and logged — a HomeBridge outage must never break mode
# toggling. The shared secret is never logged.

import logging
import threading

import config as _config_mod

logger = logging.getLogger(__name__)

# Bounded so a slow/unreachable HomeBridge can't tie up the notify thread.
_TIMEOUT = 5
_RETRIES = 2


def notify_mode_change(characteristic: str, value: bool) -> None:
  """Push a mode transition to HomeBridge, if configured.

  Args:
    characteristic: 'quiet' or 'public'.
    value: the new state.

  No-op when [homebridge] is not configured (or has no url). Never raises:
  the request is dispatched to a daemon thread and any error is logged there.
  """
  if not _config_mod.has_section('homebridge'):
    return
  url = _config_mod.get_optional('homebridge', 'url')
  if not url:
    return
  secret = _config_mod.get_optional('homebridge', 'secret')
  _dispatch(url, secret, characteristic, value)


def _dispatch(url: str, secret: str, characteristic: str, value: bool) -> None:
  """Run _send on a short-lived daemon thread (decouples toggling latency)."""
  thread = threading.Thread(
    target=_send,
    args=(url, secret, characteristic, value),
    daemon=True,
    name='homebridge-notify',
  )
  thread.start()


def _send(url: str, secret: str, characteristic: str, value: bool) -> None:
  """POST the mode transition to HomeBridge. Catches and logs all errors."""
  import integrations.http as _http  # local import: keep this module light

  headers = {'Content-Type': 'application/json'}
  if secret:
    headers['X-Webhook-Secret'] = secret
  try:
    resp = _http.fetch_with_retry(
      'POST',
      url,
      json={'characteristic': characteristic, 'value': value},
      headers=headers,
      timeout=_TIMEOUT,
      retries=_RETRIES,
    )
    if resp.status_code >= 400:
      logger.warning('HomeBridge notify %s=%s returned HTTP %d', characteristic, value, resp.status_code)
    else:
      logger.debug('HomeBridge notified: %s=%s', characteristic, value)
  except Exception as e:  # noqa: BLE001 — never let a HomeBridge outage break toggling
    logger.warning('HomeBridge notify failed for %s=%s: %s', characteristic, value, e)
