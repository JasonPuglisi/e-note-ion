# e-note-ion.py
#
# Scheduler for sending timed messages to a Vestaboard split-flap display.
# Supports both the Note (3×15) and Flagship (6×22); defaults to Note.
#
# Content is defined in JSON files under content/contrib/ (bundled, opt-in
# via [scheduler].content_enabled in config.toml) and content/user/ (personal,
# always loaded). Each file describes one or more named templates, each with
# its own cron schedule, priority, and timing constraints. At runtime,
# scheduled messages are pushed into a priority queue and consumed by a single
# worker thread that sends them to the display one at a time, ensuring the
# physical flaps are never driven concurrently.
#
# Display model, public mode, and content selection are configured in
# config.toml under [scheduler].

import email.message
import email.parser
import heapq
import importlib
import importlib.metadata
import json
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from queue import Empty, PriorityQueue
from typing import Any
from urllib.parse import parse_qs, urlparse

from apscheduler.schedulers.background import BackgroundScheduler

import config as _config_mod
import integrations.vestaboard as vestaboard
from exceptions import IntegrationDataUnavailableError

# Allowlist of valid integration names. Must be extended when a new integration
# is added to integrations/.
_KNOWN_INTEGRATIONS: frozenset[str] = frozenset({'bart', 'plex', 'trakt'})

# Cache of loaded integration modules, keyed by name.
_integrations: dict[str, Any] = {}


def _get_integration(name: str) -> Any:
  if name not in _KNOWN_INTEGRATIONS:
    raise ValueError(f'Unknown integration: {name!r}')
  if name not in _integrations:
    try:
      _integrations[name] = importlib.import_module(f'integrations.{name}')
    except ImportError as e:
      raise RuntimeError(
        f'Integration {name!r} is missing dependencies. '
        f'Install them with: pip install -r integrations/{name}.requirements.txt'
      ) from e
  return _integrations[name]


# --- Message ---

_counter = 0
_counter_lock = threading.Lock()


@dataclass
class QueuedMessage:
  # Represents a pending display message waiting in the priority queue.
  # `seq` is a monotonically increasing counter used to break priority ties
  # in favour of whichever message was scheduled earlier.
  priority: int
  seq: int
  name: str
  scheduled_at: float
  data: dict[str, Any]
  hold: int  # seconds message must stay on display
  timeout: int  # seconds message can wait in queue before being discarded
  indefinite: bool = False  # if True, hold runs until explicitly interrupted
  supersede_tag: str = ''  # if non-empty, enqueue() removes earlier same-tagged messages first

  def __lt__(self, other: 'QueuedMessage') -> bool:
    # PriorityQueue is a min-heap, so we invert priority comparison so that
    # higher numeric priority values are popped first.
    if self.priority != other.priority:
      return self.priority > other.priority  # higher priority = first
    return self.seq < other.seq  # earlier scheduled = first


@dataclass
class WebhookMessage:
  # Returned by an integration's handle_webhook() to enqueue a display message
  # triggered by an external HTTP POST. Set interrupt=True to cut the current
  # hold short so this message is shown immediately. Set indefinite=True to
  # hold until an explicit interrupt (e.g. a stop event) rather than timing
  # out at hold seconds. Set interrupt_only=True (e.g. for stop events) to
  # fire _hold_interrupt without enqueueing a new message.
  data: dict[str, Any]
  priority: int
  hold: int
  timeout: int
  name: str = ''
  interrupt: bool = False
  indefinite: bool = False
  interrupt_only: bool = False
  supersede_tag: str = ''  # if non-empty, enqueue() removes earlier same-tagged messages first


# --- Priority Queue ---

# Single shared queue consumed by the worker thread. Messages are pushed here
# by APScheduler's background threads when their cron triggers fire.
_queue: PriorityQueue[QueuedMessage] = PriorityQueue()


def enqueue(
  priority: int,
  data: dict[str, Any],
  hold: int,
  timeout: int,
  name: str = '',
  indefinite: bool = False,
  supersede_tag: str = '',
) -> None:
  global _counter
  with _counter_lock:
    seq = _counter
    _counter += 1

  msg = QueuedMessage(
    priority=priority,
    seq=seq,
    name=name,
    scheduled_at=time.monotonic(),
    data=data,
    hold=hold,
    timeout=timeout,
    indefinite=indefinite,
    supersede_tag=supersede_tag,
  )

  if supersede_tag:
    with _queue.mutex:
      before = len(_queue.queue)
      _queue.queue[:] = [m for m in _queue.queue if m.supersede_tag != supersede_tag]
      if len(_queue.queue) < before:
        heapq.heapify(_queue.queue)

  _queue.put(msg)


def pop_valid_message() -> QueuedMessage | None:
  """Return the highest-priority non-expired message, or None if the queue is empty.

  After the first message arrives, waits _COALESCE_WINDOW seconds so that any
  co-scheduled jobs (fired by APScheduler within milliseconds of each other) have
  time to enqueue before we commit to a winner. All candidates are collected, expired
  ones discarded, and the highest-priority valid message is returned; the rest are
  re-enqueued for the next cycle.
  """
  try:
    first = _queue.get(timeout=1)
  except Empty:
    return None

  time.sleep(_COALESCE_WINDOW)

  candidates = [first]
  while True:
    try:
      candidates.append(_queue.get_nowait())
    except Empty:
      break

  now = time.monotonic()
  valid: list[QueuedMessage] = []
  for m in candidates:
    waited = now - m.scheduled_at
    if waited <= m.timeout:
      valid.append(m)
    else:
      print(f'Discarding {m.name} (waited {waited:.1f}s, timeout={m.timeout}s)')

  if not valid:
    return None

  best = min(valid)
  for m in valid:
    if m is not best:
      _queue.put(m)
  return best


# --- Display Worker ---

_LOCK_RETRY_DELAY = 60  # seconds to wait before retrying a 423-locked send
_COALESCE_WINDOW = 0.1  # seconds to wait after first message arrives so co-scheduled jobs can enqueue
_HOLD_POLL_INTERVAL = 1.0  # seconds between priority-peek checks during hold
_INTERRUPT_PRIORITY_THRESHOLD = 8  # queued items at or above this can interrupt a hold early
_REFRESH_MIN_INTERVAL = 30  # minimum allowed refresh_interval (seconds); prevents API hammering

# Set by the webhook server when a high-priority incoming message should cut
# the current hold short. Cleared by the worker after each hold completes.
_hold_interrupt = threading.Event()


def _get_min_hold() -> int:
  """Return the global minimum hold in seconds from config (default 60)."""
  raw = _config_mod.get_optional('scheduler', 'min_hold', '60')
  try:
    return max(0, int(raw))
  except ValueError:
    return 60


def _do_hold(
  message: 'QueuedMessage',
  min_hold: int,
  refresh_fn: Callable[[], None] | None = None,
  refresh_interval: int | None = None,
) -> None:
  """Sleep for message.hold seconds, subject to two early-exit conditions:

  1. Webhook interrupt (_hold_interrupt event set) — exits immediately at
     any point, regardless of min_hold.
  2. Priority-based interruption — after min_hold seconds, if the current
     message's priority is below _INTERRUPT_PRIORITY_THRESHOLD and the
     highest-priority queued item is at or above it, exits early.

  High-priority messages (priority >= _INTERRUPT_PRIORITY_THRESHOLD) always
  run their full hold and are never interrupted.

  If refresh_fn and refresh_interval are provided, refresh_fn() is called
  every refresh_interval seconds during the hold. Errors from refresh_fn are
  logged and the hold continues; the display keeps showing the last good content.
  """
  hold_start = time.monotonic()
  last_refresh = hold_start
  while True:
    elapsed = time.monotonic() - hold_start
    remaining = message.hold - elapsed
    if remaining <= 0 and not message.indefinite:
      break

    next_wake = _HOLD_POLL_INTERVAL if message.indefinite else min(_HOLD_POLL_INTERVAL, remaining)
    if refresh_fn and refresh_interval:
      time_until_refresh = refresh_interval - (time.monotonic() - last_refresh)
      next_wake = min(next_wake, max(0.0, time_until_refresh))

    interrupted = _hold_interrupt.wait(timeout=next_wake)
    _hold_interrupt.clear()
    if interrupted:
      break

    if message.priority < _INTERRUPT_PRIORITY_THRESHOLD and elapsed >= min_hold:
      with _queue.mutex:
        if _queue.queue and _queue.queue[0].priority >= _INTERRUPT_PRIORITY_THRESHOLD:
          break

    if refresh_fn and refresh_interval:
      now = time.monotonic()
      if now - last_refresh >= refresh_interval:
        last_refresh = now
        try:
          refresh_fn()
        except Exception as e:  # noqa: BLE001
          print(f'Refresh error for {message.name}: {e}')


def worker() -> None:
  # Single worker thread — ensures messages are sent to the Vestaboard
  # sequentially and never overlap. After sending a message, sleeps for
  # `hold` seconds before pulling the next one, giving the physical flaps
  # time to settle and the content time to be read.
  _idle_refresh_fn: Callable[[], None] | None = None
  _idle_refresh_interval: int | None = None
  _idle_last_refresh: float = 0.0
  while True:
    # Idle refresh: if the queue is empty and the previous integration message
    # is still on the board, keep refreshing at the same interval until a new
    # message is successfully sent. Errors are logged; the loop continues.
    if _idle_refresh_fn and _idle_refresh_interval:
      now = time.monotonic()
      if now - _idle_last_refresh >= _idle_refresh_interval:
        _idle_last_refresh = now
        try:
          _idle_refresh_fn()
        except Exception as e:  # noqa: BLE001
          print(f'Idle refresh error: {e}')

    message = pop_valid_message()
    if message is None:
      continue

    scheduled = datetime.fromtimestamp(time.time() - (time.monotonic() - message.scheduled_at))
    hold_desc = f'{message.hold}s (indefinite)' if message.indefinite else f'{message.hold}s'
    print(
      f'[{datetime.now().strftime("%H:%M:%S")}] Sending {message.name}'
      f' | scheduled: {scheduled.strftime("%H:%M:%S")}'
      f' | priority: {message.priority}'
      f' | hold: {hold_desc}'
    )
    try:
      variables = message.data['variables']
      if 'integration' in message.data:
        fn_name = message.data.get('integration_fn', 'get_variables')
        variables = getattr(_get_integration(message.data['integration']), fn_name)()
      vestaboard.set_state(
        message.data['templates'],
        variables,
        message.data.get('truncation', 'hard'),
      )
    except IntegrationDataUnavailableError:
      continue  # expected empty state — skip silently
    except vestaboard.DuplicateContentError:
      print(f'Duplicate content for {message.name} — already on board, still holding.')
      # Fall through to _do_hold(): content is already showing; we must still
      # hold it so lower-priority queued messages cannot preempt it.
    except vestaboard.BoardLockedError as e:
      print(f'Board locked: {e}. Retrying in {_LOCK_RETRY_DELAY}s.')
      time.sleep(_LOCK_RETRY_DELAY)
      # Re-enqueue if the message hasn't exceeded its timeout.
      if time.monotonic() - message.scheduled_at <= message.timeout:
        _queue.put(message)
      continue
    except Exception as e:
      print(f'Error sending to board: {e}')
      continue

    # New message successfully sent (or DuplicateContentError fell through) —
    # clear idle refresh state before setting up the new hold.
    _idle_refresh_fn = None
    _idle_refresh_interval = None

    _refresh_fn: Callable[[], None] | None = None
    refresh_interval = message.data.get('refresh_interval')
    if refresh_interval and 'integration' in message.data:
      _integration = _get_integration(message.data['integration'])
      _fn_name = message.data.get('integration_fn', 'get_variables')
      _templates = message.data['templates']
      _truncation = message.data.get('truncation', 'hard')

      def _do_refresh(
        _i: Any = _integration,
        _f: Any = _fn_name,
        _t: Any = _templates,
        _tr: Any = _truncation,
      ) -> None:
        new_vars = getattr(_i, _f)()
        try:
          vestaboard.set_state(_t, new_vars, _tr)
        except vestaboard.DuplicateContentError, IntegrationDataUnavailableError:
          pass  # content unchanged or no data — keep showing current

      _refresh_fn = _do_refresh

    _do_hold(message, _get_min_hold(), refresh_fn=_refresh_fn, refresh_interval=refresh_interval)

    # Hold expired — if this was a refresh-capable integration message, transfer
    # the refresh fn to idle state so the display keeps updating while the queue
    # is empty. Set last_refresh to 0 so the first idle refresh fires immediately.
    if _refresh_fn and refresh_interval:
      _idle_refresh_fn = _refresh_fn
      _idle_refresh_interval = refresh_interval
      _idle_last_refresh = 0.0


# --- Webhook Server ---

_MAX_WEBHOOK_BODY = 64 * 1024  # 64 KB — generous limit for any webhook payload


def _make_webhook_handler(secret: str) -> type:
  """Return a BaseHTTPRequestHandler subclass bound to the given shared secret."""

  class _WebhookHandler(BaseHTTPRequestHandler):
    _secret: str = secret

    def do_POST(self) -> None:  # noqa: N802
      # Validate path: must be /webhook/<integration>
      # Parse separately from query string so ?secret= is handled cleanly.
      parsed = urlparse(self.path)
      parts = parsed.path.strip('/').split('/')
      if len(parts) != 2 or parts[0] != 'webhook':
        self._respond(404, 'Not found')
        return

      integration_name = parts[1]

      # Accept secret from X-Webhook-Secret header (preferred) or ?secret=
      # query parameter (fallback for senders that cannot set custom headers,
      # e.g. Plex Media Server). Constant-time comparison prevents timing attacks.
      header_secret = self.headers.get('X-Webhook-Secret', '')
      query_secret = parse_qs(parsed.query).get('secret', [''])[0]
      provided = header_secret or query_secret
      if not secrets.compare_digest(provided, self._secret):
        print(f'Webhook: rejected request for {integration_name!r} — invalid or missing secret')
        self._respond(401, 'Unauthorized')
        return

      # Validate against allowlist before any importlib call.
      if integration_name not in _KNOWN_INTEGRATIONS:
        self._respond(404, f'Unknown integration: {integration_name!r}')
        return

      # Parse body.
      try:
        content_length = min(int(self.headers.get('Content-Length') or 0), _MAX_WEBHOOK_BODY)
      except ValueError:
        content_length = 0
      body = self.rfile.read(content_length)
      content_type = self.headers.get('Content-Type', '')
      if 'multipart/form-data' in content_type:
        # Plex sends webhooks as multipart/form-data with JSON in a 'payload'
        # field. Prepend the Content-Type header to form a parseable MIME
        # message, then extract the named part.
        raw = b'Content-Type: ' + content_type.encode() + b'\r\n\r\n' + body
        msg = email.parser.BytesParser().parsebytes(raw)
        json_bytes: bytes | None = None
        if msg.is_multipart():
          for part in msg.get_payload():  # type: ignore[union-attr]
            if not isinstance(part, email.message.Message):
              continue
            if part.get_param('name', header='content-disposition') == 'payload':
              json_bytes = part.get_payload(decode=True)  # type: ignore[assignment]
              break
        if not json_bytes:
          self._respond(400, 'Missing payload field in multipart body')
          return
        try:
          payload: dict[str, Any] = json.loads(json_bytes)
        except json.JSONDecodeError:
          self._respond(400, 'Invalid JSON in payload field')
          return
      else:
        try:
          payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
          self._respond(400, 'Invalid JSON')
          return

      # Load integration and check for webhook support.
      try:
        mod = _get_integration(integration_name)
      except (ValueError, RuntimeError) as e:
        self._respond(404, str(e))
        return
      if not hasattr(mod, 'handle_webhook'):
        self._respond(404, f'Integration {integration_name!r} does not support webhooks')
        return

      # Dispatch to the integration handler.
      try:
        result: WebhookMessage | None = mod.handle_webhook(payload)
      except Exception as e:  # noqa: BLE001
        print(f'Webhook error in {integration_name!r}: {e}')
        self._respond(500, 'Internal error')
        return

      if result is None:
        self._respond(200, 'Discarded')
        return

      if result.interrupt_only:
        _hold_interrupt.set()
        self._respond(200, 'Interrupted')
        return

      enqueue(
        priority=result.priority,
        data=result.data,
        hold=result.hold,
        timeout=result.timeout,
        name=result.name or f'webhook.{integration_name}',
        indefinite=result.indefinite,
        supersede_tag=result.supersede_tag,
      )
      if result.interrupt:
        _hold_interrupt.set()

      self._respond(200, 'Enqueued')

    def _respond(self, code: int, message: str) -> None:
      body = message.encode()
      self.send_response(code)
      self.send_header('Content-Type', 'text/plain')
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
      pass  # suppress default per-request access log lines

  return _WebhookHandler


def _start_webhook_server() -> None:
  """Start the HTTP webhook listener in a background daemon thread.

  Reads [webhook] config for port (default 8080) and bind address (default
  127.0.0.1). Auto-generates a shared secret if none is configured, persists
  it to config.toml, and logs it once so the user can copy it into their
  webhook sender. Raises OSError if the port is already in use.
  """
  try:
    port = int(_config_mod.get_optional('webhook', 'port', '8080'))
  except ValueError:
    port_raw = _config_mod.get_optional('webhook', 'port', '8080')
    print(f'Warning: invalid webhook port {port_raw!r}, defaulting to 8080')
    port = 8080

  bind = _config_mod.get_optional('webhook', 'bind', '127.0.0.1')

  secret = _config_mod.get_optional('webhook', 'secret')
  if not secret:
    secret = secrets.token_urlsafe(32)
    _config_mod.write_section_values('webhook', {'secret': secret})
    print(
      f'Webhook secret generated and saved to config.toml:\n'
      f'  {secret}\n'
      f'Copy this into your webhook sender (Plex, Shortcuts, etc.).'
    )

  handler = _make_webhook_handler(secret)
  server = HTTPServer((bind, port), handler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  print(f'Webhook listener started on {bind}:{port}')


# --- Scheduler ---


def parse_cron(cron: str) -> dict[str, str]:
  minute, hour, day, month, day_of_week = cron.split()
  return {'minute': minute, 'hour': hour, 'day': day, 'month': month, 'day_of_week': day_of_week}


_VALID_TRUNCATION: frozenset[str] = frozenset({'hard', 'word', 'ellipsis'})


def _validate_template(name: str, template: dict[str, Any]) -> None:
  """Validate a single template dict, raising ValueError with a clear message.

  Checks: schedule fields (cron str, hold/timeout non-negative int),
  priority range, truncation value, and that at least one of templates or
  integration is present. When "webhook": true is set, cron is optional —
  hold and timeout in the schedule dict still serve as webhook defaults.
  """
  is_webhook = bool(template.get('webhook', False))
  schedule = template.get('schedule')
  if not isinstance(schedule, dict):
    raise ValueError(f'{name}: missing or invalid "schedule" field')
  cron = schedule.get('cron')
  if not is_webhook:
    if not isinstance(cron, str) or not cron.strip():
      raise ValueError(f'{name}: schedule.cron must be a non-empty string')
  for field in ('hold', 'timeout'):
    val = schedule.get(field)
    if not isinstance(val, int) or val < 0:
      raise ValueError(f'{name}: schedule.{field} must be a non-negative integer, got {val!r}')

  priority = template.get('priority')
  if not isinstance(priority, int) or not (0 <= priority <= 10):
    raise ValueError(f'{name}: priority must be an integer between 0 and 10, got {priority!r}')

  truncation = template.get('truncation', 'hard')
  if truncation not in _VALID_TRUNCATION:
    valid = ', '.join(sorted(_VALID_TRUNCATION))
    raise ValueError(f'{name}: truncation must be one of {valid}, got {truncation!r}')

  refresh_interval = schedule.get('refresh_interval')
  if refresh_interval is not None:
    if not isinstance(refresh_interval, int) or refresh_interval < _REFRESH_MIN_INTERVAL:
      raise ValueError(
        f'{name}: schedule.refresh_interval must be an integer >= {_REFRESH_MIN_INTERVAL}, got {refresh_interval!r}'
      )

  has_templates = 'templates' in template
  has_integration = 'integration' in template
  if not has_templates and not has_integration:
    raise ValueError(f'{name}: must have "templates" and/or "integration"')

  integration_fn = template.get('integration_fn')
  if integration_fn is not None and not isinstance(integration_fn, str):
    raise ValueError(f'{name}: integration_fn must be a string, got {integration_fn!r}')


def _load_file(
  scheduler: BackgroundScheduler,
  content_file: Path,
  public_mode: bool,
) -> None:
  # Parse and validate the file before touching the scheduler so that a bad
  # file leaves existing jobs untouched.
  with open(content_file) as f:
    content = json.load(f)

  # Prefix the stem with the parent directory name (user or contrib) so that
  # files with the same name in different directories don't collide.
  stem = f'{content_file.parent.name}.{content_file.stem}'
  new_jobs = []
  webhook_only_jobs: list[tuple[str, int, dict[str, Any], dict[str, Any]]] = []
  for template_name, template in content['templates'].items():
    if public_mode and not template.get('public', True):
      continue
    _validate_template(f'{stem}.{template_name}', template)
    priority = template['priority']
    truncation = template.get('truncation', 'hard')
    data: dict[str, Any] = {
      'templates': template.get('templates', []),
      'variables': content.get('variables', {}),
      'truncation': truncation,
    }
    if 'integration' in template:
      data['integration'] = template['integration']
    if 'integration_fn' in template:
      data['integration_fn'] = template['integration_fn']
    schedule = template['schedule']
    is_webhook = bool(template.get('webhook', False))
    has_cron = isinstance(schedule.get('cron'), str) and bool(schedule['cron'].strip())
    if is_webhook and not has_cron:
      webhook_only_jobs.append((f'{stem}.{template_name}', priority, data, schedule))
    else:
      new_jobs.append((f'{stem}.{template_name}', priority, data, schedule))

  # Atomically swap out the old jobs for this file.
  for job in scheduler.get_jobs():
    if job.id.startswith(f'{stem}.'):
      job.remove()

  # First pass: apply overrides and collect effective values so column widths
  # are computed from the values actually shown (not the pre-override JSON).
  effective_jobs: list[tuple[str, int, dict[str, Any], dict[str, Any]]] = []
  for job_id, priority, data, schedule in new_jobs:
    template_name = job_id[len(stem) + 1 :]
    # Merge any schedule overrides from config.toml (e.g. [bart.schedules.departures]).
    override = _config_mod.get_schedule_override(f'{content_file.stem}.{template_name}')
    effective = dict(schedule)
    for field in ('cron', 'hold', 'timeout', 'refresh_interval'):
      if field not in override:
        continue
      val = override[field]
      if field == 'cron' and isinstance(val, str) and val.strip():
        effective[field] = val
      elif field in ('hold', 'timeout') and isinstance(val, int) and val >= 0:
        effective[field] = val
      elif field == 'refresh_interval':
        if isinstance(val, int) and val >= _REFRESH_MIN_INTERVAL:
          effective[field] = val
        else:
          print(f'Warning: ignoring invalid refresh_interval override for {job_id}: {val!r}')
    if 'priority' in override:
      val = override['priority']
      if isinstance(val, int) and 0 <= val <= 10:
        priority = val
      else:
        print(f'Warning: ignoring invalid priority override for {job_id}: {val!r}')
    effective_jobs.append((job_id, priority, data, effective))

  max_name = max((len(job_id[len(stem) + 1 :]) for job_id, *_ in effective_jobs), default=0)
  max_cron = max((len(effective['cron']) for _, _, _, effective in effective_jobs), default=0)
  max_priority = max((len(str(priority)) for _, priority, _, _ in effective_jobs), default=0)
  # +1 for the 's' suffix so the whole "180s" token is padded together
  max_hold = max((len(str(effective['hold'])) + 1 for _, _, _, effective in effective_jobs), default=0)
  max_timeout = max((len(str(effective['timeout'])) + 1 for _, _, _, effective in effective_jobs), default=0)

  if effective_jobs or webhook_only_jobs:
    print(f'Loaded {content_file.parent.name}/{content_file.name}:')
  for job_id, priority, data, effective in effective_jobs:
    template_name = job_id[len(stem) + 1 :]
    # Propagate effective refresh_interval (may have been set or overridden) into data.
    ri = effective.get('refresh_interval')
    if ri is not None:
      data['refresh_interval'] = ri
    elif 'refresh_interval' in data:
      del data['refresh_interval']
    scheduler.add_job(
      enqueue,
      trigger='cron',
      args=[priority, data, effective['hold'], effective['timeout'], job_id],
      id=job_id,
      **parse_cron(effective['cron']),  # type: ignore[arg-type]
    )
    print(
      f'  · {template_name.ljust(max_name)}'
      f'  {f"cron={chr(34)}{effective['cron']}{chr(34)}".ljust(max_cron + 7)}'
      f'  {f"priority={priority}".ljust(max_priority + 9)}'
      f'  {f"hold={effective['hold']}s".ljust(max_hold + 5)}'
      f'  {f"timeout={effective['timeout']}s".ljust(max_timeout + 8)}'
    )

  if webhook_only_jobs:
    max_wh_name = max((len(job_id[len(stem) + 1 :]) for job_id, *_ in webhook_only_jobs), default=0)
    max_wh_hold = max((len(str(schedule['hold'])) + 1 for _, _, _, schedule in webhook_only_jobs), default=0)
    max_wh_timeout = max((len(str(schedule['timeout'])) + 1 for _, _, _, schedule in webhook_only_jobs), default=0)
    max_wh_priority = max((len(str(priority)) for _, priority, _, _ in webhook_only_jobs), default=0)
    for job_id, priority, _, schedule in webhook_only_jobs:
      template_name = job_id[len(stem) + 1 :]
      print(
        f'  · {template_name.ljust(max_wh_name)}'
        f'  {"webhook=true".ljust(12)}'
        f'  {f"priority={priority}".ljust(max_wh_priority + 9)}'
        f'  {f"hold={schedule['hold']}s".ljust(max_wh_hold + 5)}'
        f'  {f"timeout={schedule['timeout']}s".ljust(max_wh_timeout + 8)}'
      )


def load_content(
  scheduler: BackgroundScheduler,
  public_mode: bool = False,
  content_enabled: set[str] | None = None,
) -> None:
  # Reads JSON files from content/user/ (always) and content/contrib/
  # (only stems listed in content_enabled, or all if '*' is present).
  if content_enabled is None:
    content_enabled = set()

  user_path = Path('content') / 'user'
  if user_path.is_dir():
    for f in sorted(user_path.glob('*.json')):
      _load_file(scheduler, f, public_mode)

  contrib_path = Path('content') / 'contrib'
  if contrib_path.is_dir() and content_enabled:
    for f in sorted(contrib_path.glob('*.json')):
      if '*' in content_enabled or f.stem in content_enabled:
        _load_file(scheduler, f, public_mode)


def _validate_startup() -> None:
  """Check for bad Docker mount states before loading config or content.

  Exits with a clear, actionable message on fatal errors (config.toml is a
  directory, missing, or empty). Warns non-fatally if the user content
  directory is empty.
  """
  config_path = Path('config.toml')
  if config_path.is_dir():
    print(
      f'Error: {config_path.resolve()} is a directory. '
      'Docker created it automatically because the host path did not exist at container start. '
      'Delete it on the host, create a proper config.toml file there, and restart the container.',
      file=sys.stderr,
    )
    raise SystemExit(1)
  if not config_path.exists():
    print(
      f'Error: config.toml not found at {config_path.resolve()}. '
      'Copy config.example.toml, fill in your API keys, '
      'and make sure the host path is mounted correctly before starting the container.',
      file=sys.stderr,
    )
    raise SystemExit(1)
  if config_path.stat().st_size == 0:
    print(
      'Error: config.toml is empty. Copy config.example.toml and fill in your API keys.',
      file=sys.stderr,
    )
    raise SystemExit(1)

  user_path = Path('content') / 'user'
  if user_path.is_dir() and not any(user_path.iterdir()):
    print(
      'Warning: user content directory is empty. '
      'If you intended to mount personal content, make sure the host path exists and '
      'contains JSON files. If Docker created this directory automatically, delete it on '
      'the host, create it with your content files, and restart the container.'
    )


def main() -> None:
  _validate_startup()
  _config_mod.load_config()

  model = _config_mod.get_model()
  if model == 'flagship':
    vestaboard.model = vestaboard.VestaboardModel.FLAGSHIP

  public_mode = _config_mod.get_public_mode()
  content_enabled = _config_mod.get_content_enabled()

  board_desc = 'Flagship (6×22)' if model == 'flagship' else 'Note (3×15)'
  extras: list[str] = []
  if content_enabled:
    if '*' in content_enabled:
      extras.append('all contrib content')
    else:
      extras.append(f'contrib: {", ".join(sorted(content_enabled))}')
  if public_mode:
    extras.append('public mode')
  if not extras:
    extras.append('user content only')
  version = importlib.metadata.version('e-note-ion')
  print(f'Starting e-note-ion v{version} — {board_desc}, {", ".join(extras)}')

  print('Current message:')
  try:
    print(vestaboard.get_state())
  except vestaboard.EmptyBoardError:
    print('(no current message)')
  scheduler = BackgroundScheduler(
    misfire_grace_time=300,
    timezone=_config_mod.get_timezone(),
  )
  load_content(scheduler, public_mode=public_mode, content_enabled=content_enabled)
  scheduler.start()
  print(f'Scheduler started — {len(scheduler.get_jobs())} job(s) registered')

  loaded_integrations: set[str] = set()
  for job in scheduler.get_jobs():
    data = job.args[1]
    if 'integration' in data:
      loaded_integrations.add(data['integration'])
  for name in loaded_integrations:
    try:
      mod = _get_integration(name)
      if hasattr(mod, 'preflight'):
        mod.preflight()
    except Exception as e:  # noqa: BLE001
      print(f'Warning: preflight for {name!r} failed: {e}')

  threading.Thread(target=worker, daemon=True).start()

  if _config_mod.has_section('webhook'):
    _start_webhook_server()

  try:
    while True:
      time.sleep(1)
  except KeyboardInterrupt:
    scheduler.shutdown()


if __name__ == '__main__':
  main()
