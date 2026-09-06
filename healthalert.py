# healthalert.py
#
# Optional outbound notifier for health status transitions. When [health] is
# configured with an alert_url in config.toml, a sustained change in overall
# health (e.g. healthy → error, or error → healthy) fires a small HTTP POST so
# Slack, Discord, Pushover, ntfy, or a Home Assistant automation can react
# without polling GET /health.
#
# Deliberately mirrors homebridge.py: fully optional, dispatched on a daemon
# thread so a slow endpoint never stalls the health timer, every failure caught
# and logged, and the shared secret never logged. An alerting outage must never
# affect the scheduler.

import logging
import threading
from typing import Any

import config as _config_mod

logger = logging.getLogger(__name__)

# Bounded so an unreachable alert endpoint cannot tie up the notify thread.
_TIMEOUT = 5
_RETRIES = 2


def is_configured() -> bool:
  """Return True if an alert endpoint is configured."""
  return bool(_config_mod.has_section('health') and _config_mod.get_optional('health', 'alert_url'))


def notify_status_change(previous: str, current: str, summary: dict[str, Any]) -> None:
  """Push a health status transition to the configured endpoint, if any.

  Args:
    previous: the status being left (e.g. 'healthy').
    current:  the status being entered (e.g. 'error').
    summary:  the same payload GET /health returns, so a receiver can act on
              detail without a follow-up request.

  No-op when unconfigured. Never raises: the request runs on a daemon thread
  and any error is logged there.
  """
  url = _config_mod.get_optional('health', 'alert_url')
  if not url:
    return
  secret = _config_mod.get_optional('health', 'alert_secret')
  thread = threading.Thread(
    target=_send,
    args=(url, secret, previous, current, summary),
    daemon=True,
    name='health-alert',
  )
  thread.start()


def _send(url: str, secret: str, previous: str, current: str, summary: dict[str, Any]) -> None:
  """POST the transition. Catches and logs all errors."""
  import integrations.http as _http  # local import: keep this module light

  headers = {'Content-Type': 'application/json'}
  if secret:
    headers['X-Webhook-Secret'] = secret

  # Trimmed to the targets that are actually unwell, so a chat notification is
  # readable and a payload does not grow with the number of integrations.
  unhealthy = {
    name: detail
    for name, detail in (summary.get('integrations') or {}).items()
    if detail.get('status') not in ('healthy', 'unknown')
  }
  payload = {
    'previous': previous,
    'current': current,
    'uptime_seconds': summary.get('uptime_seconds'),
    'unhealthy': unhealthy,
  }
  try:
    resp = _http.fetch_with_retry('POST', url, json=payload, headers=headers, timeout=_TIMEOUT, retries=_RETRIES)
    if resp.status_code >= 400:
      logger.warning('Health alert %s→%s returned HTTP %d', previous, current, resp.status_code)
    else:
      logger.debug('Health alert sent: %s→%s', previous, current)
  except Exception as e:  # noqa: BLE001 — an alerting outage must not affect the scheduler
    logger.warning('Health alert failed for %s→%s: %s', previous, current, _http.redact(str(e)))
