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

import argparse
import email.message
import email.parser
import heapq
import importlib
import importlib.metadata
import json
import logging
import secrets
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, PriorityQueue
from typing import Any
from urllib.parse import parse_qs, urlparse

from apscheduler.schedulers.background import BackgroundScheduler

import config as _config_mod
import health as _health_mod
import integrations.http as _http
import integrations.vestaboard as _vb
import public as _public_mod
import quiet as _quiet_mod
from exceptions import IntegrationDataUnavailableError

# When run via `python scheduler.py` or the `e-note-ion` entry point, Python
# loads this module as __main__. Integrations that do `import scheduler` (e.g.
# plex.py) would otherwise trigger a *second* import, producing a separate
# module object with its own copies of module-level globals such as
# _current_hold_supersede_tag. This line aliases __main__ → scheduler so that
# all imports share a single module object. No-op when scheduler is already
# imported normally (e.g. in tests or when imported by another module).
if __name__ == '__main__':
  sys.modules.setdefault('scheduler', sys.modules['__main__'])

logger = logging.getLogger('scheduler')


class _IndentedFormatter(logging.Formatter):
  # Prefix width for 'HH:MM:SS LEVELNAM ' (asctime=8, space=1, levelname-8=8, space=1)
  _PREFIX_WIDTH = 18

  def format(self, record: logging.LogRecord) -> str:
    msg = super().format(record)
    indent = ' ' * self._PREFIX_WIDTH
    return msg.replace('\n', '\n' + indent)


# Allowlist of valid integration names. Must be extended when a new integration
# is added to integrations/.
_KNOWN_INTEGRATIONS: frozenset[str] = frozenset(
  {
    'bart',
    'calendar',
    'discogs',
    'diving',
    'health',
    'message',
    'moon',
    'morning',
    'notion',
    'parcel',
    'plex',
    'qbittorrent',
    'scheduler',
    'trakt',
    'unraid',
    'uptimerobot',
    'weather',
    'ynab',
    'youtube',
  }
)

# Cache of loaded integration modules, keyed by name.
_integrations: dict[str, Any] = {}

# Resolved private flags for webhook-capable integrations, keyed by integration
# name and ORed across that integration's templates. Consulted at pop time as a
# fallback when a message does not carry data['private'] itself.
#
# Populated by _register_webhook_private_flags() for every content file on disk,
# and again by _load_file for the subset that is actually loaded. The former
# matters because webhook dispatch is independent of [scheduler].content_enabled:
# an integration can receive and display webhooks while its JSON was never
# loaded, and a load-time-only registry would leave those messages unmarked.
#
# This is a coarse net (per-integration, not per-template). Webhook integrations
# should stamp data['private'] on the messages they enqueue so the per-template
# flag travels with the message; see resolve_private().
_webhook_private: dict[str, bool] = {}


def _get_integration(name: str) -> Any:
  if name not in _KNOWN_INTEGRATIONS:
    raise ValueError(f'Unknown integration: {name!r}')
  if name not in _integrations:
    logger.debug('loading integration %r', name)
    try:
      _integrations[name] = importlib.import_module(f'integrations.{name}')
    except ImportError as e:
      raise RuntimeError(
        f'Integration {name!r} is missing dependencies. '
        f'Install them with: pip install -r integrations/{name}.requirements.txt'
      ) from e
  else:
    logger.debug('integration %r already cached', name)
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
  interrupt: bool = False  # if True, post-set_state re-fire block will re-arm _hold_interrupt to break the new hold

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
  interrupt: bool = False,
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
    interrupt=interrupt,
  )

  if supersede_tag:
    with _queue.mutex:
      before = len(_queue.queue)
      _queue.queue[:] = [m for m in _queue.queue if m.supersede_tag != supersede_tag]
      removed = before - len(_queue.queue)
      if removed:
        heapq.heapify(_queue.queue)
        logger.debug('supersede removed %d queued message(s) with tag %r', removed, supersede_tag)

  logger.debug('enqueued %s (priority=%d, seq=%d, hold=%ds, timeout=%ds)', name, priority, seq, hold, timeout)
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
      # A discard behind a higher-priority hold is expected; only a genuine
      # backlog deserves a warning. Warning on both made the log useless
      # during any Plex session. (#600)
      log = logger.debug if _discard_is_expected(m.priority) else logger.warning
      log('Discarding %s (waited %.1fs, timeout=%ds)', m.name, waited, m.timeout)

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

# Tracks state of the message currently being held by the worker.
# supersede_tag is '' and priority is None when the worker is idle.
_current_hold_lock = threading.Lock()
_current_hold_supersede_tag: str = ''
_current_hold_priority: int | None = None

# Tracks whether the board is currently showing private content. Defaults to
# True (safe assumption after restart — we don't know what was last displayed).
# Set True when a private message is sent, False on non-private send or clear.
_board_showing_private: bool = True


def current_hold_tag() -> str:
  """Return the supersede_tag of the message currently being held, or ''."""
  with _current_hold_lock:
    tag = _current_hold_supersede_tag
  return tag


def _current_hold_is_interruptible() -> bool:
  """Return True if the current hold's priority is below the interrupt threshold.

  Mirrors the cron-based interrupt gate: only holds with priority below
  _INTERRUPT_PRIORITY_THRESHOLD can be cut short. High-priority holds always
  run to completion. Returns True when no hold is active.
  """
  with _current_hold_lock:
    cur = _current_hold_priority
  return cur is None or cur < _INTERRUPT_PRIORITY_THRESHOLD


def _discard_is_expected(priority: int) -> bool:
  """Return True when a message expiring is explained by the current hold.

  A higher-or-equal priority hold that outlives a message's timeout is the
  system working: the board is already showing something more important, and
  the stale message is correctly dropped rather than displayed late.

  The case that motivated this is Plex. webhook.plex holds indefinitely while
  media plays, and contrib.trakt.watching fires every 3 minutes with a 120s
  timeout, so a two-hour film produced roughly forty identical warnings
  describing correct behaviour. (#600)
  """
  with _current_hold_lock:
    cur = _current_hold_priority
  return cur is not None and cur >= priority


def fire_hold_interrupt(supersede_tag: str = '') -> None:
  """Fire the hold interrupt if the current hold matches supersede_tag or is interruptible.

  Intended for use by integration timer callbacks that enqueue directly (bypassing
  the webhook handler) and need to cut the current hold short, e.g. plex stop debounce.
  """
  same_tag = bool(supersede_tag) and supersede_tag == current_hold_tag()
  if same_tag or _current_hold_is_interruptible():
    _hold_interrupt.set()


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
     any point, regardless of min_hold. The webhook server only sets this
     event when the current hold's priority is below
     _INTERRUPT_PRIORITY_THRESHOLD (mirrors condition 2 below).
  2. Priority-based interruption — after min_hold seconds, if the current
     message's priority is below _INTERRUPT_PRIORITY_THRESHOLD and the
     highest-priority queued item is at or above it, exits early.

  High-priority messages (priority >= _INTERRUPT_PRIORITY_THRESHOLD) always
  run their full hold and are never interrupted by either path.

  If refresh_fn and refresh_interval are provided, refresh_fn() is called
  every refresh_interval seconds during the hold. Errors from refresh_fn are
  logged and the hold continues; the display keeps showing the last good content.

  Quiet→wake transitions are detected within ≤1s (the poll interval). When
  detected, the virtual state is sent to the board immediately and the hold
  continues normally.
  """
  hold_start = time.monotonic()
  last_refresh = hold_start
  was_quiet = _quiet_mod.is_quiet()
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
      logger.debug('[hold] %s interrupted at %.1fs', message.name, time.monotonic() - hold_start)
      break

    if message.priority < _INTERRUPT_PRIORITY_THRESHOLD and elapsed >= min_hold:
      with _queue.mutex:
        if _queue.queue and _queue.queue[0].priority >= _INTERRUPT_PRIORITY_THRESHOLD:
          logger.debug(
            '[hold] %s preempted by higher-priority message at %.1fs',
            message.name,
            time.monotonic() - hold_start,
          )
          break

    # Detect quiet→wake transition and push the virtual state to the board
    # immediately, without breaking the hold. Subsequent refreshes (if any)
    # will write to the board directly via _do_refresh's is_quiet() check.
    now_quiet = _quiet_mod.is_quiet()
    if was_quiet and not now_quiet:
      virtual = _quiet_mod.pop_virtual_state()
      if virtual is not None:
        logger.info('[hold] quiet→wake during %s — sending virtual state to board', message.name)
        try:
          _vb.set_state_raw(virtual)
        except _vb.DuplicateContentError:
          pass
        except _vb.BoardLockedError:
          logger.warning('[hold] board locked on wake — virtual state discarded')
        except Exception as e:  # noqa: BLE001
          logger.error('[hold] error sending virtual state on wake: %s', e)
    was_quiet = now_quiet

    # Detect public mode activation while the board shows private content.
    # Break immediately so the worker can drain the queue or clear the board.
    if _public_mod.changed_event().is_set():
      _public_mod.changed_event().clear()
      if _board_showing_private:
        logger.info('[hold] public mode activated while private content displayed (%s) — breaking', message.name)
        break

    if refresh_fn and refresh_interval:
      now = time.monotonic()
      if now - last_refresh >= refresh_interval:
        last_refresh = now
        try:
          refresh_fn()
        except Exception as e:  # noqa: BLE001
          logger.warning('Refresh error for %s: %s', message.name, e)


def _clear_private_content() -> None:
  """Drain the queue for the next public message, or clear the board.

  Called when public mode is activated while the board shows private content.
  Skips private messages in the queue until a public one is found (put back for
  the main loop). If no public message is available, sends a blank grid to the
  board. Privacy trumps quiet mode: the blank grid is sent to the real board
  even when quiet is active.
  """
  global _board_showing_private
  found_public = False
  while True:
    try:
      m = _queue.get_nowait()
    except Empty:
      break
    m_private = m.data.get('private')
    if not m_private and m.name.startswith('webhook.'):
      m_private = _webhook_private.get(m.name.removeprefix('webhook.'), False)
    if m_private:
      logger.debug('Draining %s — private content hidden in public mode', m.name)
      continue
    _queue.put(m)
    found_public = True
    break
  if not found_public:
    logger.info('Public mode active with private content displayed — clearing board')
    try:
      blank = [[0] * _vb.model.cols for _ in range(_vb.model.rows)]
      _vb.set_state_raw(blank)
      _board_showing_private = False
      _health_mod.record_success(_health_mod.VESTABOARD_TARGET)
    except _vb.BoardLockedError:
      _health_mod.record_locked(_health_mod.VESTABOARD_TARGET)
    except Exception as e:  # noqa: BLE001
      _health_mod.record_error(_health_mod.VESTABOARD_TARGET, _http.redact(str(e)))
      logger.error('Error clearing board for public mode: %s', e)


def worker() -> None:
  # Single worker thread — ensures messages are sent to the Vestaboard
  # sequentially and never overlap. After sending a message, sleeps for
  # `hold` seconds before pulling the next one, giving the physical flaps
  # time to settle and the content time to be read.
  global _current_hold_supersede_tag, _current_hold_priority, _board_showing_private
  _idle_refresh_fn: Callable[[], None] | None = None
  _idle_refresh_interval: int | None = None
  _idle_last_refresh: float = 0.0
  while True:
    # Handle quiet→wake transition: send the last virtual state to the board
    # so it wakes to contextually relevant content. Detected within ≤1s of
    # the wake webhook firing (the pop_valid_message timeout).
    if not _quiet_mod.is_quiet():
      virtual = _quiet_mod.pop_virtual_state()
      if virtual is not None:
        logger.info('Quiet mode ended — sending virtual state to board')
        try:
          _vb.set_state_raw(virtual)
        except _vb.DuplicateContentError:
          pass  # content already showing
        except _vb.BoardLockedError:
          logger.warning('Board locked on wake — virtual state discarded')
        except Exception as e:  # noqa: BLE001
          logger.error('Error sending virtual state on wake: %s', e)

    # Public mode activated while idle — if the board shows private content,
    # drain the queue for the next public message or clear the board.
    if _public_mod.changed_event().is_set():
      _public_mod.changed_event().clear()
      if _board_showing_private:
        _idle_refresh_fn = None
        _idle_refresh_interval = None
        _clear_private_content()

    # Idle refresh: if the queue is empty and the previous integration message
    # is still on the board, keep refreshing at the same interval until a new
    # message is successfully sent. Errors are logged; the loop continues.
    if _idle_refresh_fn and _idle_refresh_interval:
      now = time.monotonic()
      if now - _idle_last_refresh >= _idle_refresh_interval:
        _idle_last_refresh = now
        with _queue.mutex:
          queue_pending = bool(_queue.queue)
        if not queue_pending:
          try:
            _idle_refresh_fn()
          except Exception as e:  # noqa: BLE001
            logger.warning('Idle refresh error: %s', e)

    message = pop_valid_message()
    if message is None:
      continue

    is_private = message.data.get('private')
    if not is_private and message.name.startswith('webhook.'):
      is_private = _webhook_private.get(message.name.removeprefix('webhook.'), False)
    if _public_mod.is_public() and is_private:
      logger.debug('Skipping %s — private content hidden in public mode', message.name)
      continue

    scheduled = datetime.fromtimestamp(time.time() - (time.monotonic() - message.scheduled_at))
    hold_desc = f'{message.hold}s (indefinite)' if message.indefinite else f'{message.hold}s'
    quiet_tag = ' [quiet]' if _quiet_mod.is_quiet() else ''
    logger.info(
      'Sending %s%s | scheduled: %s | priority: %d | hold: %s',
      message.name,
      quiet_tag,
      scheduled.strftime('%H:%M:%S'),
      message.priority,
      hold_desc,
    )
    # Set hold tracking before the set_state API call so that concurrent webhook
    # events (e.g. Plex pause arriving while now_playing is being sent to the
    # board) see the correct tag immediately rather than waiting ~1–2s for the
    # API round-trip to complete. Restored to '' in each exception path that
    # skips _do_hold so we never leave a stale tag when nothing is on the board.
    with _current_hold_lock:
      _current_hold_supersede_tag = message.supersede_tag
      _current_hold_priority = message.priority

    _health_name = message.data.get('integration', '')
    # Phase 1: fetch + render — attributed to the integration target.
    # Phase 2: POST to the board — attributed to the 'vestaboard' target.
    # Splitting the phases keeps Vestaboard-side outages from smearing
    # across every integration's health status and vice versa.
    try:
      variables = message.data['variables']
      if 'integration' in message.data:
        fn_name = message.data.get('integration_fn', 'get_variables')
        variables = getattr(_get_integration(message.data['integration']), fn_name)()
      templates = message.data['templates']
      truncation = message.data.get('truncation', 'hard')
      grid = _vb.render(templates, variables, truncation)
    except IntegrationDataUnavailableError as e:
      if _health_name:
        if e.expected:
          _health_mod.record_expected_empty(_health_name)
        else:
          _health_mod.record_error(_health_name, _http.redact(str(e)))
      with _current_hold_lock:
        _current_hold_supersede_tag = ''
        _current_hold_priority = None
      # An expected-empty result is the healthy state ("UptimeRobot: all
      # monitors up"), and health already records it as such. Logging it at
      # WARNING made WARNING meaningless: a real integration failure looked
      # identical to everything being fine. (#599)
      log = logger.info if e.expected else logger.warning
      log('Skipping %s: %s', message.name, e)
      continue
    except Exception as e:
      if _health_name:
        _health_mod.record_error(_health_name, _http.redact(str(e)))
      with _current_hold_lock:
        _current_hold_supersede_tag = ''
        _current_hold_priority = None
      logger.error('Error fetching data for %s: %s', message.name, e)
      continue
    if _health_name:
      _health_mod.record_success(_health_name)

    # Phase 2: send to the board. Quiet mode skips the POST entirely, so
    # no vestaboard health event is recorded — recording a fake success
    # there would mask real outages whenever quiet mode is active.
    if _quiet_mod.is_quiet():
      _quiet_mod.set_virtual_state(grid)
    else:
      try:
        _vb.set_state_raw(grid)
      except _vb.DuplicateContentError:
        # Duplicate = board already shows this content. Count as vestaboard
        # success (the user's goal is met) and fall through to _do_hold()
        # so lower-priority queued messages cannot preempt it.
        _health_mod.record_success(_health_mod.VESTABOARD_TARGET)
        logger.warning('Duplicate content for %s — already on board, still holding.', message.name)
      except _vb.BoardLockedError as e:
        _health_mod.record_locked(_health_mod.VESTABOARD_TARGET)
        with _current_hold_lock:
          _current_hold_supersede_tag = ''
          _current_hold_priority = None
        logger.warning('Board locked: %s. Retrying in %ds.', e, _LOCK_RETRY_DELAY)
        time.sleep(_LOCK_RETRY_DELAY)
        # Re-enqueue if the message hasn't exceeded its timeout.
        if time.monotonic() - message.scheduled_at <= message.timeout:
          _queue.put(message)
        continue
      except Exception as e:
        _health_mod.record_error(_health_mod.VESTABOARD_TARGET, _http.redact(str(e)))
        with _current_hold_lock:
          _current_hold_supersede_tag = ''
          _current_hold_priority = None
        logger.error('Error sending to board: %s', e)
        continue
      else:
        _health_mod.record_success(_health_mod.VESTABOARD_TARGET)

    # New message successfully sent (or DuplicateContentError fell through) —
    # track whether the board is now showing private content.
    _board_showing_private = bool(is_private)

    # Clear idle refresh state before setting up the new hold.
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
        _hn: str = _health_name,
      ) -> None:
        # Phase 1: fetch + render — attributed to the integration target.
        try:
          new_vars = getattr(_i, _f)()
          _grid = _vb.render(_t, new_vars, _tr)
        except IntegrationDataUnavailableError as _e:
          if _hn:
            if _e.expected:
              _health_mod.record_expected_empty(_hn)
            else:
              _health_mod.record_error(_hn, _http.redact(str(_e)))
          raise
        except Exception as _e:
          if _hn:
            _health_mod.record_error(_hn, _http.redact(str(_e)))
          raise
        if _hn:
          _health_mod.record_success(_hn)

        # Phase 2: send to the board — attributed to the vestaboard target.
        if _quiet_mod.is_quiet():
          _quiet_mod.set_virtual_state(_grid)
          return
        try:
          _vb.set_state_raw(_grid)
        except _vb.DuplicateContentError:
          _health_mod.record_success(_health_mod.VESTABOARD_TARGET)
        except _vb.BoardLockedError:
          _health_mod.record_locked(_health_mod.VESTABOARD_TARGET)
        except Exception as _e:
          _health_mod.record_error(_health_mod.VESTABOARD_TARGET, _http.redact(str(_e)))
          raise
        else:
          _health_mod.record_success(_health_mod.VESTABOARD_TARGET)

      _refresh_fn = _do_refresh

    # Clear any interrupt that fired before this hold began (e.g. the webhook
    # interrupt that preempted the previous hold and triggered enqueueing of
    # this message) so it cannot exit the new hold instantly. But if a newer
    # message arrived during the set_state API call above that wants to cut
    # through — either because it shares our supersede_tag, or because it
    # carries its own interrupt=True intent and we (the new hold) are below
    # the priority interrupt threshold — re-fire the interrupt so _do_hold
    # exits immediately and the worker processes the newer event.
    _hold_interrupt.clear()
    with _queue.mutex:
      for m in _queue.queue:
        same_tag = bool(message.supersede_tag) and m.supersede_tag == message.supersede_tag
        interrupt_intent = m.interrupt and message.priority < _INTERRUPT_PRIORITY_THRESHOLD
        if same_tag or interrupt_intent:
          reason = 'same-tag' if same_tag else 'interrupt intent'
          logger.debug('[hold] %s re-firing interrupt (%s): message queued during set_state', message.name, reason)
          _hold_interrupt.set()
          break
    _do_hold(message, _get_min_hold(), refresh_fn=_refresh_fn, refresh_interval=refresh_interval)
    with _current_hold_lock:
      _current_hold_supersede_tag = ''
      _current_hold_priority = None
    logger.debug('[hold] %s hold ended, tag cleared', message.name)

    # Public mode activated while private content is on the board — drain the
    # queue for the next public message or clear the board.
    if _board_showing_private and _public_mod.is_public():
      _idle_refresh_fn = None
      _idle_refresh_interval = None
      _clear_private_content()
      continue

    # Hold expired — if this was a refresh-capable integration message, transfer
    # the refresh fn to idle state so the display keeps updating while the queue
    # is empty. Set last_refresh to 0 so the first idle refresh fires immediately.
    if _refresh_fn and refresh_interval:
      _idle_refresh_fn = _refresh_fn
      _idle_refresh_interval = refresh_interval
      _idle_last_refresh = 0.0


# --- Webhook Server ---

# Logger names, not package names — qh3 logs under 'quic'. Verified against the
# installed package; guessing 'qh3' would silence nothing. See #598.
#
# Noise only: these are restored at DEBUG, where the operator has asked for
# third-party detail.
_NOISY_THIRD_PARTY_LOGGERS = ('quic',)

# caldav's "Ical data was modified" warning is handled separately because it is
# not noise — it prints a unified diff of real calendar event data (summaries,
# descriptions, times) to justify normalising trailing whitespace.
#
# It is silenced at *every* level, DEBUG included. Asking for verbose logs from
# this project is not the same as consenting to a dependency dumping personal
# calendar contents into a file that gets collected and shipped onward, and the
# original fix conflated the two: production runs at DEBUG, so the escape hatch
# put the diff straight back.
_ICAL_DIFF_MARKER = 'Ical data was modified'


class _DropIcalDiff(logging.Filter):
  """Drop caldav's event-data diff, whatever the configured level."""

  def filter(self, record: logging.LogRecord) -> bool:
    return _ICAL_DIFF_MARKER not in record.getMessage()


_MAX_WEBHOOK_BODY = 64 * 1024  # 64 KB — generous limit for any webhook payload

# Seconds a single connection may sit idle mid-request before the handler gives
# up. Without this a stalled socket holds its thread forever; before #590, when
# the server was single-threaded, it held the *entire* listener forever.
_WEBHOOK_SOCKET_TIMEOUT = 20

# Concurrent argon2id verifications allowed across all handler threads.
#
# Threading the server without this trades a CPU denial-of-service for a worse
# memory one: PasswordHasher defaults to 64 MiB per verification, so N handler
# threads verifying at once reserve N * 64 MiB. Two keeps the ceiling at 128 MiB
# on hardware that is often a Raspberry Pi, while still letting a legitimate
# webhook through while another is being checked.
_AUTH_CONCURRENCY = 2

# How long a request waits for an argon2 slot before being shed with 503. Bounds
# the queue an attacker can build up; a legitimate caller never waits this long
# because a verification takes tens of milliseconds.
_AUTH_WAIT_SECONDS = 5

_auth_semaphore = threading.BoundedSemaphore(_AUTH_CONCURRENCY)


class _AuthCapacityError(Exception):
  """Raised when no argon2 slot frees up within _AUTH_WAIT_SECONDS."""


def _authenticate_webhook(provided: str, integration: str) -> str | None:
  """Authenticate a webhook request against named credentials.

  Returns:
    '<name>' — authenticated as the named credential
    None     — authentication failed

  Credentials are scoped to the integration: a credential's 'webhooks' list must
  include the integration name for it to be considered. Credential secrets are
  verified using argon2id hashing.
  """
  # Reject a missing secret before touching argon2 at all. Unauthenticated
  # probes overwhelmingly send no secret, and this makes them free to refuse.
  if not provided:
    return None

  credentials = _config_mod.get_credentials(integration)
  if not credentials:
    return None

  # One verification per configured credential is unavoidable — there is no way
  # to know which one a secret belongs to without trying. Bounding concurrency
  # is what keeps that from being a lever.
  if not _auth_semaphore.acquire(timeout=_AUTH_WAIT_SECONDS):
    logger.warning('Webhook: auth capacity exhausted, shedding request for %r', integration)
    raise _AuthCapacityError

  try:
    from argon2 import PasswordHasher  # noqa: PLC0415
    from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError  # noqa: PLC0415

    ph = PasswordHasher()
    for name, cred in credentials.items():
      secret_hash = cred.get('secret_hash', '')
      if not secret_hash:
        continue
      try:
        ph.verify(secret_hash, provided)
        return name
      except VerifyMismatchError, VerificationError, InvalidHashError:
        continue
  except ImportError:
    logger.error(
      'argon2-cffi is required for named webhook credentials but is not installed; '
      'install it with: pip install argon2-cffi'
    )
  finally:
    _auth_semaphore.release()

  return None


def _build_state_payload(refresh: bool = False) -> dict[str, Any]:
  """Assemble the GET /state response: current mode toggles + board content.

  Always includes `modes` (quiet/public) — the primary payload for the HomeKit
  switches, so the endpoint returns 200 with modes even when no board content is
  known. Board content is best-effort: the quiet-mode virtual state when quiet is
  active, an authoritative fetch when refresh=True, otherwise the in-memory cache
  of the last grid the worker sent. `source` is 'virtual', 'board', or 'empty';
  `grid`/`rendered`/`timestamp` are null when nothing is known.
  """
  modes = {'quiet': _quiet_mod.is_quiet(), 'public': _public_mod.is_public()}
  grid: list[list[int]] | None = None
  grid_at = 0.0
  source = 'empty'
  refresh_error = False

  if modes['quiet']:
    grid = _quiet_mod.get_virtual_state()
    if grid is not None:
      source = 'virtual'
      grid_at = time.time()
  elif refresh:
    try:
      grid = _vb.get_state().layout
      source = 'board'
      grid_at = time.time()
    except _vb.EmptyBoardError:
      source = 'empty'
    except Exception as e:  # noqa: BLE001 — refresh is best-effort; fall back to cache
      logger.warning('State: refresh fetch failed: %s', e)
      refresh_error = True
      grid, grid_at = _vb.get_cached_grid()
      if grid is not None:
        source = 'board'
  else:
    grid, grid_at = _vb.get_cached_grid()
    if grid is not None:
      source = 'board'

  payload: dict[str, Any] = {
    'modes': modes,
    'source': source,
    'grid': grid,
    'rendered': _vb.render_grid_text(grid) if grid is not None else None,
    'timestamp': (datetime.fromtimestamp(grid_at, tz=_config_mod.get_timezone()).isoformat() if grid_at else None),
  }
  if refresh_error:
    payload['refresh_error'] = True
  return payload


def _make_webhook_handler() -> type:
  """Return a BaseHTTPRequestHandler subclass for the webhook listener."""

  class _WebhookHandler(BaseHTTPRequestHandler):
    # Honoured by socketserver.StreamRequestHandler.setup(); without it a client
    # that opens a connection and stops sending holds its thread indefinitely.
    timeout = _WEBHOOK_SOCKET_TIMEOUT

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
      # e.g. Plex Media Server).
      header_secret = self.headers.get('X-Webhook-Secret', '')
      query_secret = parse_qs(parsed.query).get('secret', [''])[0]
      provided = header_secret or query_secret
      try:
        credential_name = _authenticate_webhook(provided, integration_name)
      except _AuthCapacityError:
        self._respond(503, 'Server busy, retry shortly')
        return
      if credential_name is None:
        logger.warning('Webhook: rejected request for %r — invalid or missing secret', integration_name)
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
      # All integrations must accept credential_name as a keyword argument.
      try:
        result: WebhookMessage | None = mod.handle_webhook(payload, credential_name=credential_name)
      except Exception as e:  # noqa: BLE001
        _health_mod.record_error(integration_name, _http.redact(str(e)))
        logger.error('Webhook error in %r: %s', integration_name, e)
        self._respond(500, 'Internal error')
        return

      if result is None:
        _health_mod.record_success(integration_name)
        self._respond(200, 'Discarded')
        return

      _health_mod.record_success(integration_name)

      if result.interrupt_only:
        if _current_hold_is_interruptible():
          logger.debug('[webhook] %s interrupt_only — firing hold interrupt', integration_name)
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
        interrupt=result.interrupt,
      )
      if result.interrupt:
        # Same-tag supersede always interrupts regardless of priority threshold —
        # a source's own state transitions (e.g. Plex play→pause→stop) must always
        # cut through the prior hold from that same source.
        same_tag = bool(result.supersede_tag) and result.supersede_tag == current_hold_tag()
        if same_tag or _current_hold_is_interruptible():
          reason = 'same-tag' if same_tag else 'interruptible hold'
          logger.debug('[webhook] %s interrupt (%s) — firing hold interrupt', integration_name, reason)
          _hold_interrupt.set()

      self._respond(200, 'Enqueued')

    def _handle_health(self) -> tuple[int, dict[str, Any]] | None:
      """Shared GET/HEAD /health logic: path check, auth, status.

      Returns (status_code, summary) on success, or None after sending
      an error response for path/auth failures.
      """
      parsed = urlparse(self.path)
      if parsed.path.strip('/') != 'health':
        self._respond(404, 'Not found')
        return None

      header_secret = self.headers.get('X-Webhook-Secret', '')
      query_secret = parse_qs(parsed.query).get('secret', [''])[0]
      provided = header_secret or query_secret
      try:
        credential_name = _authenticate_webhook(provided, 'health')
      except _AuthCapacityError:
        self._respond(503, 'Server busy, retry shortly')
        return None
      if credential_name is None:
        logger.warning('Health: rejected request — invalid or missing secret')
        self._respond(401, 'Unauthorized')
        return None

      summary = _health_mod.get_summary()
      status_code = 200 if summary['status'] == 'healthy' else 503
      return status_code, summary

    def _handle_state(self, parsed: Any) -> None:
      """GET /state — current mode toggles + board content (auth: 'state' cred)."""
      header_secret = self.headers.get('X-Webhook-Secret', '')
      query = parse_qs(parsed.query)
      query_secret = query.get('secret', [''])[0]
      provided = header_secret or query_secret
      try:
        credential_name = _authenticate_webhook(provided, 'state')
      except _AuthCapacityError:
        self._respond(503, 'Server busy, retry shortly')
        return
      if credential_name is None:
        logger.warning('State: rejected request — invalid or missing secret')
        self._respond(401, 'Unauthorized')
        return
      refresh = query.get('refresh', [''])[0].lower() in ('1', 'true', 'yes')
      self._respond_json(200, _build_state_payload(refresh=refresh))

    def do_GET(self) -> None:  # noqa: N802
      parsed = urlparse(self.path)
      if parsed.path.strip('/') == 'state':
        self._handle_state(parsed)
        return
      result = self._handle_health()
      if result is not None:
        self._respond_json(result[0], result[1])

    def do_HEAD(self) -> None:  # noqa: N802
      result = self._handle_health()
      if result is not None:
        body = json.dumps(result[1], indent=2).encode()
        self.send_response(result[0])
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()

    def _respond(self, code: int, message: str) -> None:
      body = message.encode()
      self.send_response(code)
      self.send_header('Content-Type', 'text/plain')
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def _respond_json(self, code: int, data: dict[str, Any]) -> None:
      body = json.dumps(data, indent=2).encode()
      self.send_response(code)
      self.send_header('Content-Type', 'application/json')
      self.send_header('Content-Length', str(len(body)))
      self.end_headers()
      self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
      pass  # suppress default per-request access log lines

  return _WebhookHandler


# Integrations that get a named credential auto-generated on first startup
# if none exists yet. For message, the credential lives at
# [webhook.credentials.message.admin] and is keyed as 'admin' in get_credentials.
_WEBHOOK_AUTOGEN: dict[str, str] = {
  'diving': 'diving',
  'health': 'health',
  'message': 'admin',
  'notion': 'notion',
  'plex': 'plex',
  'scheduler': 'scheduler',
  'state': 'state',
}


def _autogen_webhook_credential(integration: str, cred_name: str) -> None:
  """Auto-generate and persist a named credential for the given integration.

  Hashes a random 32-byte URL-safe secret with argon2id and writes it to
  config.toml. Message credentials are written under the nested namespace
  [webhook.credentials.message.<cred_name>]; all others go to the flat
  [webhook.credentials.<cred_name>]. The plaintext secret is logged once
  so the user can copy it into their webhook sender.
  """
  try:
    from argon2 import PasswordHasher  # noqa: PLC0415
  except ImportError:
    logger.warning(
      'argon2-cffi is required to auto-generate webhook credentials; install it with: pip install argon2-cffi'
    )
    return

  plaintext = secrets.token_urlsafe(32)
  ph = PasswordHasher()
  secret_hash = ph.hash(plaintext)
  section = (
    f'webhook.credentials.message.{cred_name}' if integration == 'message' else f'webhook.credentials.{cred_name}'
  )
  _config_mod.write_config_section(
    section,
    {'secret_hash': secret_hash, 'webhooks': [integration]},
  )
  logger.info(
    'Webhook credential auto-generated for %r and saved to config.toml '
    'as [%s]. '
    'Copy this into your webhook sender (as X-Webhook-Secret header or ?secret= query param): %s',
    integration,
    section,
    plaintext,
  )


def _start_webhook_server() -> None:
  """Start the HTTP webhook listener in a background daemon thread.

  Reads [webhook] config for port (default 8080) and bind address (default
  127.0.0.1). Authentication is handled entirely via named credentials defined
  in [webhook.credentials.*] sections of config.toml. Auto-generates a
  credential for each integration in _WEBHOOK_AUTOGEN on first startup if none
  exists yet. Raises OSError if the port is already in use.
  """
  try:
    port = int(_config_mod.get_optional('webhook', 'port', '8080'))
  except ValueError:
    port_raw = _config_mod.get_optional('webhook', 'port', '8080')
    logger.warning('invalid webhook port %r, defaulting to 8080', port_raw)
    port = 8080

  bind = _config_mod.get_optional('webhook', 'bind', '127.0.0.1')

  # Migrate old-style flat message credentials to the nested namespace (removed in 2.0).
  migrated = _config_mod.migrate_message_credentials()
  if migrated:
    logger.info(
      'Auto-migrated %d message credential(s) to webhook.credentials.message.*. '
      'No action required — your passphrases continue to work unchanged.',
      migrated,
    )

  # Auto-generate credentials for integrations that have none yet.
  for integration, cred_name in sorted(_WEBHOOK_AUTOGEN.items()):
    existing = _config_mod.get_credentials(integration)
    if integration == 'message':
      # For message, ensure the admin credential exists even when friends are present.
      needs_autogen = cred_name not in existing
    else:
      needs_autogen = not existing
    if needs_autogen:
      _autogen_webhook_credential(integration, cred_name)

  handler = _make_webhook_handler()
  # Threaded so one slow or stalled connection cannot block every other
  # webhook, /health, and /state request. daemon_threads is set by
  # ThreadingHTTPServer, so a wedged handler cannot hold up interpreter exit.
  server = ThreadingHTTPServer((bind, port), handler)
  threading.Thread(target=server.serve_forever, daemon=True).start()
  logger.info('Webhook listener started on %s:%d', bind, port)


# --- Scheduler ---


# How far ahead to sample fire times, and the hard iteration cap that bounds
# the work for very frequent crons. Eight days covers weekly schedules and any
# overnight blackout; the cap keeps a per-minute cron to a couple of days of
# samples, which is still enough to see one night.
_CRON_SAMPLE_DAYS = 8
_CRON_SAMPLE_LIMIT = 3000


def cron_interval_seconds(cron: str) -> float | None:
  """Return the longest gap between consecutive firings of *cron*, in seconds.

  Used for overdue detection (#502). Computed by asking APScheduler's own
  CronTrigger for successive fire times rather than parsing the expression
  ourselves — the trigger is already the authority on what the cron means,
  including the configured timezone.

  The *longest* gap, not the average: "0 8,16 * * *" alternates 8h and 16h, and
  taking the short one would flag the integration as overdue every night. Being
  conservative here trades slower detection for no false alarms.

  Returns None if the cron cannot be interpreted or never fires again.
  """
  from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415

  try:
    trigger = CronTrigger(**parse_cron(cron), timezone=_config_mod.get_timezone())
  except ValueError, TypeError:
    return None

  # Sample far enough ahead to see daily and weekly blackout windows, not just
  # the next few firings. "*/3 7-23 * * *" looks like a 3-minute cadence over a
  # short sample, but it does not fire between 23:00 and 07:00 — measuring the
  # short gap would mark it overdue every single night. The horizon has to
  # outlast the longest blackout the expression can express.
  # Use the trigger's own timezone rather than the config value: get_timezone()
  # returns None when unset, and a naive `now` cannot be compared against the
  # tz-aware datetimes APScheduler hands back.
  now = datetime.now(tz=trigger.timezone)
  horizon = now + timedelta(days=_CRON_SAMPLE_DAYS)

  fire_times: list[datetime] = []
  previous: datetime | None = None
  for _ in range(_CRON_SAMPLE_LIMIT):
    nxt = trigger.get_next_fire_time(previous, previous or now)
    if nxt is None:
      break
    fire_times.append(nxt)
    previous = nxt
    if nxt >= horizon and len(fire_times) >= 2:
      break
  if len(fire_times) < 2:
    return None

  gaps = [(b - a).total_seconds() for a, b in zip(fire_times, fire_times[1:], strict=False)]
  gaps = [g for g in gaps if g > 0]
  return max(gaps) if gaps else None


def parse_cron(cron: str) -> dict[str, str]:
  minute, hour, day, month, day_of_week = cron.split()
  return {'minute': minute, 'hour': hour, 'day': day, 'month': month, 'day_of_week': day_of_week}


_VALID_TRUNCATION: frozenset[str] = frozenset({'hard', 'word', 'ellipsis', 'wrap_ellipsis'})


def _coerce_bool(val: object, label: str) -> bool | None:
  """Coerce a config override value to bool, warning on string input.

  Returns True/False for recognised values, None for unrecognised (caller
  ignores). Native TOML booleans pass through directly. String 'true'/'false'
  (case-insensitive) are accepted with a warning nudging toward correct TOML.
  Any other type is rejected with a warning.
  """
  if isinstance(val, bool):
    return val
  if isinstance(val, str):
    lower = val.strip().lower()
    if lower in ('true', 'false'):
      logger.warning(
        '%s should be a TOML boolean (true/false without quotes), not a string; treating as %s',
        label,
        lower,
      )
      return lower == 'true'
  logger.warning('ignoring invalid %s: %r', label, val)
  return None


def resolve_private(template: dict[str, Any], override: dict[str, Any], label: str) -> bool:
  """Resolve the effective private flag for a template.

  The config override (e.g. [plex.schedules.now_playing] private = false)
  takes precedence over the JSON's "private" field; an unrecognised override
  value is ignored and the JSON value stands.

  Shared by _load_file and by webhook-only integrations, which must stamp
  data['private'] themselves — their templates are never registered through
  _load_file unless the file is listed in [scheduler].content_enabled.
  """
  private = bool(template.get('private', False))
  if 'private' in override:
    coerced = _coerce_bool(override['private'], f'private override for {label}')
    if coerced is not None:
      private = coerced
  return private


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


def _make_gated_enqueue(template_id: str, stem: str) -> Callable[..., None]:
  """Wrap enqueue() with a calendar_schedule gate check.

  The returned callable has the same signature as enqueue(). When the gate
  is closed (calendar-driven override or [scheduler.calendar_schedule].gated_templates
  default), the cron fire is silently dropped after a debug log.
  """

  def _gated(
    priority: int,
    data: dict[str, Any],
    hold: int,
    timeout: int,
    name: str = '',
  ) -> None:
    import integrations.calendar_schedule as _cs

    if not _cs.is_open(template_id, stem):
      logger.debug('cron %s suppressed by calendar_schedule', name)
      return
    enqueue(priority, data, hold, timeout, name)

  return _gated


def _load_file(
  scheduler: BackgroundScheduler,
  content_file: Path,
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
  disabled_jobs: list[str] = []
  for template_name, template in content['templates'].items():
    _validate_template(f'{stem}.{template_name}', template)
    # Check disabled overrides before building the job.
    override = _config_mod.get_schedule_override(f'{content_file.stem}.{template_name}')
    if 'disabled' in override:
      coerced = _coerce_bool(override['disabled'], f'disabled override for {stem}.{template_name}')
      if coerced is True:
        disabled_jobs.append(template_name)
        continue
    # Resolve effective private flag: config override takes precedence over JSON.
    private = resolve_private(template, override, f'{stem}.{template_name}')
    priority = template['priority']
    truncation = template.get('truncation', 'hard')
    data: dict[str, Any] = {
      'templates': template.get('templates', []),
      'variables': content.get('variables', {}),
      'truncation': truncation,
    }
    if private:
      data['private'] = True
    if 'integration' in template:
      integration_name = template['integration']
      try:
        _get_integration(integration_name)
      except (ValueError, RuntimeError) as e:
        logger.warning('skipping template %s.%r — %s', stem, template_name, e)
        continue
      data['integration'] = integration_name
      _health_mod.register(integration_name)
    if 'integration_fn' in template:
      data['integration_fn'] = template['integration_fn']
    schedule = template['schedule']
    is_webhook = bool(template.get('webhook', False))
    if is_webhook and 'integration' in template:
      _webhook_private[template['integration']] = _webhook_private.get(template['integration'], False) or private
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
          logger.warning('ignoring invalid refresh_interval override for %s: %r', job_id, val)
    if 'priority' in override:
      val = override['priority']
      if isinstance(val, int) and 0 <= val <= 10:
        priority = val
      else:
        logger.warning('ignoring invalid priority override for %s: %r', job_id, val)
    effective_jobs.append((job_id, priority, data, effective))

  max_name = max((len(job_id[len(stem) + 1 :]) for job_id, *_ in effective_jobs), default=0)
  max_cron = max((len(effective['cron']) for _, _, _, effective in effective_jobs), default=0)
  max_priority = max((len(str(priority)) for _, priority, _, _ in effective_jobs), default=0)
  # +1 for the 's' suffix so the whole "180s" token is padded together
  max_hold = max((len(str(effective['hold'])) + 1 for _, _, _, effective in effective_jobs), default=0)
  max_timeout = max((len(str(effective['timeout'])) + 1 for _, _, _, effective in effective_jobs), default=0)

  if effective_jobs or webhook_only_jobs or disabled_jobs:
    logger.info('Loaded %s/%s:', content_file.parent.name, content_file.name)
  for job_id, priority, data, effective in effective_jobs:
    template_name = job_id[len(stem) + 1 :]
    # Propagate effective refresh_interval (may have been set or overridden) into data.
    ri = effective.get('refresh_interval')
    if ri is not None:
      data['refresh_interval'] = ri
    elif 'refresh_interval' in data:
      del data['refresh_interval']
    # The gated-enqueue closure consults calendar_schedule before forwarding to
    # enqueue(). Webhook-triggered and refresh-triggered enqueues bypass this
    # entirely — only cron firings go through the gate.
    template_id = f'{content_file.stem}.{template_name}'
    # Overdue detection needs the *effective* cron, after config.toml schedule
    # overrides — a user who moved a job to a different cadence should not be
    # measured against the JSON default. (#502)
    if 'integration' in data:
      interval = cron_interval_seconds(effective['cron'])
      if interval is not None:
        _health_mod.set_expected_interval(data['integration'], interval)
    gated = _make_gated_enqueue(template_id, content_file.stem)
    scheduler.add_job(
      gated,
      trigger='cron',
      args=[priority, data, effective['hold'], effective['timeout'], job_id],
      id=job_id,
      **parse_cron(effective['cron']),  # type: ignore[arg-type]
    )
    logger.info(
      '  · %s  %s  %s  %s  %s',
      template_name.ljust(max_name),
      f'cron="{effective["cron"]}"'.ljust(max_cron + 7),
      f'priority={priority}'.ljust(max_priority + 9),
      f'hold={effective["hold"]}s'.ljust(max_hold + 5),
      f'timeout={effective["timeout"]}s'.ljust(max_timeout + 8),
    )

  if webhook_only_jobs:
    max_wh_name = max((len(job_id[len(stem) + 1 :]) for job_id, *_ in webhook_only_jobs), default=0)
    max_wh_hold = max((len(str(schedule['hold'])) + 1 for _, _, _, schedule in webhook_only_jobs), default=0)
    max_wh_timeout = max((len(str(schedule['timeout'])) + 1 for _, _, _, schedule in webhook_only_jobs), default=0)
    max_wh_priority = max((len(str(priority)) for _, priority, _, _ in webhook_only_jobs), default=0)
    for job_id, priority, _, schedule in webhook_only_jobs:
      template_name = job_id[len(stem) + 1 :]
      logger.info(
        '  · %s  %s  %s  %s  %s',
        template_name.ljust(max_wh_name),
        'webhook=true'.ljust(12),
        f'priority={priority}'.ljust(max_wh_priority + 9),
        f'hold={schedule["hold"]}s'.ljust(max_wh_hold + 5),
        f'timeout={schedule["timeout"]}s'.ljust(max_wh_timeout + 8),
      )

  for template_name in disabled_jobs:
    logger.info('  · %s  disabled', template_name)


def _register_webhook_private_flags() -> None:
  """Populate _webhook_private from every content file on disk.

  Runs regardless of [scheduler].content_enabled because webhook dispatch is
  independent of content loading: _get_integration() imports the module and the
  integration reads its own JSON, so an integration that was never loaded can
  still receive webhooks and display them. Registering only loaded files left
  those messages unmarked, so private content reached the board in public mode.

  Best-effort: unreadable or malformed files are logged at debug and skipped —
  the load pass that follows reports them at warning level.
  """
  for directory in ('user', 'contrib'):
    path = Path('content') / directory
    if not path.is_dir():
      continue
    for f in sorted(path.glob('*.json')):
      try:
        with open(f) as fh:
          content = json.load(fh)
        templates = content['templates']
      except Exception as e:  # noqa: BLE001 — the load pass reports this properly
        logger.debug('skipping %s during webhook private-flag scan: %s', f, e)
        continue
      if not isinstance(templates, dict):
        continue
      for template_name, template in templates.items():
        if not isinstance(template, dict):
          continue
        integration = template.get('integration')
        if not template.get('webhook', False) or not isinstance(integration, str):
          continue
        override = _config_mod.get_schedule_override(f'{f.stem}.{template_name}')
        private = resolve_private(template, override, f'{f.stem}.{template_name}')
        _webhook_private[integration] = _webhook_private.get(integration, False) or private


def load_content(
  scheduler: BackgroundScheduler,
  content_enabled: set[str] | None = None,
) -> None:
  # Reads JSON files from content/user/ and content/contrib/.
  #
  # When content_enabled is None (key absent from config), user files always
  # load and no contrib files load — preserving the pre-filter default.
  #
  # When content_enabled is a set (key explicitly configured), the filter
  # applies to both directories: '*' enables all files; specific stems select
  # individual files from either directory.

  def _enabled(stem: str) -> bool:
    if content_enabled is None:
      return True
    return '*' in content_enabled or stem in content_enabled

  # Register private flags for every webhook template on disk before the load
  # filter is applied — webhooks fire whether or not their file was enabled.
  _register_webhook_private_flags()

  user_stems: set[str] = set()
  contrib_stems: set[str] = set()

  user_path = Path('content') / 'user'
  if user_path.is_dir():
    for f in sorted(user_path.glob('*.json')):
      if _enabled(f.stem):
        user_stems.add(f.stem)
        try:
          _load_file(scheduler, f)
        except Exception as e:  # noqa: BLE001
          logger.warning('failed to load %s: %s', f, e)

  contrib_path = Path('content') / 'contrib'
  if contrib_path.is_dir() and content_enabled:
    for f in sorted(contrib_path.glob('*.json')):
      if _enabled(f.stem):
        contrib_stems.add(f.stem)
        try:
          _load_file(scheduler, f)
        except Exception as e:  # noqa: BLE001
          logger.warning('failed to load %s: %s', f, e)

  for stem in sorted(user_stems & contrib_stems):
    logger.warning(
      '%s.json exists in both content/user/ and content/contrib/ — '
      'both loaded; rename the user file if this is unintentional',
      stem,
    )

  if content_enabled and '*' not in content_enabled:
    found = user_stems | contrib_stems
    for stem in sorted(content_enabled - found):
      logger.warning('content file not found for enabled stem %r — check [scheduler] enabled in config.toml', stem)

  _validate_calendar_schedule(scheduler)


def _validate_calendar_schedule(scheduler: BackgroundScheduler) -> None:
  """Warn about gated_templates entries that don't match any loaded template.

  Each entry must be either a file stem (e.g. 'bart') or a fully-qualified
  template id ('bart.departures'). Bare template names without a stem are
  rejected — silent ambiguity in keyword-driven config is a footgun.
  """
  cs_cfg = _config_mod._config.get('scheduler', {}).get('calendar_schedule', {})
  if not isinstance(cs_cfg, dict) or not cs_cfg:
    return
  raw = cs_cfg.get('gated_templates', [])
  if not isinstance(raw, list):
    logger.warning('calendar_schedule.gated_templates must be a list, got %r', raw)
    return

  loaded_stems: set[str] = set()
  loaded_template_ids: set[str] = set()
  for job in scheduler.get_jobs():
    # job.id is '<dir>.<stem>.<template_name>' (e.g. 'contrib.bart.departures').
    parts = job.id.split('.', 2)
    if len(parts) == 3:
      _, stem, template_name = parts
      loaded_stems.add(stem)
      loaded_template_ids.add(f'{stem}.{template_name}')

  for entry in raw:
    if not isinstance(entry, str):
      logger.warning('calendar_schedule.gated_templates entry must be a string, got %r', entry)
      continue
    if entry in loaded_stems or entry in loaded_template_ids:
      continue
    logger.warning(
      'calendar_schedule.gated_templates entry %r does not match any loaded '
      'file stem or template id — entries must be "<stem>" or "<stem>.<template>"',
      entry,
    )


def _validate_startup(config_path: Path) -> None:
  """Sanity-check the config file before loading.

  Exits with a clear, actionable message on fatal errors (config path is a
  directory, missing, or empty). Warns non-fatally if the user content
  directory is empty.
  """
  if config_path.is_dir():
    print(
      f'Error: {config_path.resolve()} is a directory, not a file. '
      'Create a config.toml file (copy config.example.toml) and try again.',
      file=sys.stderr,
    )
    raise SystemExit(1)
  if not config_path.exists():
    print(
      f'Error: config.toml not found at {config_path.resolve()}. '
      'Copy config.example.toml, fill in your API keys, and try again.',
      file=sys.stderr,
    )
    raise SystemExit(1)
  if config_path.stat().st_size == 0:
    print(
      f'Error: {config_path.resolve()} is empty. Copy config.example.toml and fill in your API keys.',
      file=sys.stderr,
    )
    raise SystemExit(1)

  user_path = Path('content') / 'user'
  if user_path.is_dir() and not any(user_path.iterdir()):
    logger.warning('user content directory is empty — add JSON content files or remove the directory')


def main() -> None:
  parser = argparse.ArgumentParser(
    prog='e-note-ion',
    description='Content scheduler for Vestaboard split-flap displays.',
  )
  parser.add_argument(
    '-V',
    '--version',
    action='version',
    version=f'%(prog)s {importlib.metadata.version("e-note-ion")}',
  )
  parser.add_argument(
    '-c',
    '--config',
    type=Path,
    default=Path('config.toml'),
    metavar='PATH',
    help='path to config.toml (default: ./config.toml)',
  )
  args = parser.parse_args()

  _handler = logging.StreamHandler()
  _handler.setFormatter(
    _IndentedFormatter(
      fmt='%(asctime)s %(levelname)-8s %(message)s',
      datefmt='%H:%M:%S',
    )
  )
  logging.basicConfig(level=logging.INFO, handlers=[_handler])
  _validate_startup(args.config)
  _config_mod.load_config(args.config)
  _config_mod.migrate_quiet_config()
  _quiet_mod.init()
  _public_mod.init()
  _health_mod.init()

  log_level_str = _config_mod.get_optional('scheduler', 'log_level', 'INFO').upper()
  level = getattr(logging, log_level_str, None)
  if not isinstance(level, int):
    logger.warning('invalid log_level %r in config.toml — defaulting to INFO', log_level_str)
    level = logging.INFO
  logging.root.setLevel(level)

  # log_level is set on the root logger, so without this every dependency
  # inherits it. Two are noisy at WARNING for reasons that are never actionable
  # here, and one of them logs personal data:
  #
  #   caldav — "Ical data was modified to avoid compatibility issues" arrives
  #     with a multi-line unified diff of the actual event data it normalised
  #     (summaries, descriptions, timestamps), to report trailing-whitespace
  #     changes. That lands in the Docker log in plaintext.
  #   quic (qh3, pulled in via caldav) — "Native peer close: ... keepalive
  #     timeout" for an idle pooled connection being closed, which is normal.
  #
  # Skipped entirely at DEBUG: an operator who asked for DEBUG wants the
  # third-party detail too.
  if level > logging.DEBUG:
    for noisy in _NOISY_THIRD_PARTY_LOGGERS:
      logging.getLogger(noisy).setLevel(logging.ERROR)

  # Unconditional: personal data must not be gated on log level. Attached to the
  # handler rather than the 'caldav' logger — a logger's own filters do not run
  # on records propagated up from its children, so a handler filter is the only
  # placement that holds regardless of which logger caldav emits from.
  for handler in logging.root.handlers:
    handler.addFilter(_DropIcalDiff())

  model = _config_mod.get_model()
  if model == 'flagship':
    _vb.model = _vb.VestaboardModel.FLAGSHIP

  content_enabled = _config_mod.get_content_enabled()

  board_desc = 'Flagship (6×22)' if model == 'flagship' else 'Note (3×15)'
  extras: list[str] = []
  if content_enabled is None:
    extras.append('user content only')
  elif '*' in content_enabled:
    extras.append('all content')
  elif content_enabled:
    extras.append(f'content: {", ".join(sorted(content_enabled))}')
  else:
    extras.append('no content loaded')
  if _public_mod.is_public():
    extras.append('public mode')
  if _quiet_mod.is_quiet():
    extras.append('quiet mode')
  version = importlib.metadata.version('e-note-ion')
  logger.info('Starting e-note-ion v%s — %s, %s', version, board_desc, ', '.join(extras))

  logger.info('Current message:')
  try:
    logger.info('%s', _vb.get_state())
  except _vb.EmptyBoardError:
    logger.info('(no current message)')
  scheduler = BackgroundScheduler(
    misfire_grace_time=300,
    timezone=_config_mod.get_timezone(),
  )
  load_content(scheduler, content_enabled=content_enabled)
  scheduler.start()
  logger.info('Scheduler started — %d job(s) registered', len(scheduler.get_jobs()))

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
      logger.warning('preflight for %r failed: %s', name, e)

  threading.Thread(target=worker, daemon=True).start()
  _health_mod.start_periodic_log()
  _health_mod.start_status_watch()

  if _config_mod.has_section('webhook'):
    _start_webhook_server()

  try:
    while True:
      time.sleep(1)
  except KeyboardInterrupt:
    _health_mod.stop_periodic_log()
    _health_mod.stop_status_watch()
    scheduler.shutdown()


if __name__ == '__main__':
  main()
