# e-note-ion.py
#
# Scheduler for sending timed messages to a Vestaboard split-flap display.
# Supports both the Note (3×15) and Flagship (6×22); defaults to Note.
#
# Content is defined in JSON files under content/contrib/ (bundled, opt-in
# via --content-enabled) and content/user/ (personal, always loaded). Each
# file describes one or more named templates, each with its own cron schedule,
# priority, and timing constraints. At runtime, scheduled messages are pushed
# into a priority queue and consumed by a single worker thread that sends them
# to the display one at a time, ensuring the physical flaps are never driven
# concurrently.
#
# Run with --flagship to target a Flagship board, --public to restrict output
# to templates marked as public (useful when the display is in a shared
# space), and --content-enabled to opt into bundled contrib content.

import argparse
import importlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, PriorityQueue
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

import config as _config_mod
import integrations.vestaboard as vestaboard

# Allowlist of valid integration names. Must be extended when a new integration
# is added to integrations/.
_KNOWN_INTEGRATIONS: frozenset[str] = frozenset({'bart'})

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

  def __lt__(self, other: 'QueuedMessage') -> bool:
    # PriorityQueue is a min-heap, so we invert priority comparison so that
    # higher numeric priority values are popped first.
    if self.priority != other.priority:
      return self.priority > other.priority  # higher priority = first
    return self.seq < other.seq  # earlier scheduled = first


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
) -> None:
  global _counter
  with _counter_lock:
    seq = _counter
    _counter += 1

  _queue.put(
    QueuedMessage(
      priority=priority,
      seq=seq,
      name=name,
      scheduled_at=time.monotonic(),
      data=data,
      hold=hold,
      timeout=timeout,
    )
  )


def pop_valid_message() -> QueuedMessage | None:
  """Return the next non-expired message, or None if the queue is empty."""
  while True:
    try:
      message = _queue.get(timeout=1)
    except Empty:
      return None

    waited = time.monotonic() - message.scheduled_at
    if waited <= message.timeout:
      return message
    # Message sat in the queue too long (e.g. blocked behind a higher-priority
    # message with a long hold time) — discard so stale content never shows.
    print(f'Discarding {message.name} (waited {waited:.1f}s, timeout={message.timeout}s)')


# --- Display Worker ---

_LOCK_RETRY_DELAY = 60  # seconds to wait before retrying a 423-locked send


def worker() -> None:
  # Single worker thread — ensures messages are sent to the Vestaboard
  # sequentially and never overlap. After sending a message, sleeps for
  # `hold` seconds before pulling the next one, giving the physical flaps
  # time to settle and the content time to be read.
  while True:
    message = pop_valid_message()
    if message is None:
      continue

    scheduled = datetime.fromtimestamp(time.time() - (time.monotonic() - message.scheduled_at))
    print(
      f'[{datetime.now().strftime("%H:%M:%S")}] Sending {message.name}'
      f' | scheduled: {scheduled.strftime("%H:%M:%S")}'
      f' | priority: {message.priority}'
      f' | hold: {message.hold}s'
    )
    try:
      variables = message.data['variables']
      if 'integration' in message.data:
        variables = _get_integration(message.data['integration']).get_variables()
      vestaboard.set_state(
        message.data['templates'],
        variables,
        message.data.get('truncation', 'hard'),
      )
    except vestaboard.DuplicateContentError:
      print('Duplicate content, skipping.')
      continue
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

    time.sleep(message.hold)


# --- Scheduler ---


def parse_cron(cron: str) -> dict[str, str]:
  minute, hour, day, month, day_of_week = cron.split()
  return {'minute': minute, 'hour': hour, 'day': day, 'month': month, 'day_of_week': day_of_week}


_VALID_TRUNCATION: frozenset[str] = frozenset({'hard', 'word', 'ellipsis'})


def _validate_template(name: str, template: dict[str, Any]) -> None:
  """Validate a single template dict, raising ValueError with a clear message.

  Checks: schedule fields (cron str, hold/timeout non-negative int),
  priority range, truncation value, and that at least one of templates or
  integration is present.
  """
  schedule = template.get('schedule')
  if not isinstance(schedule, dict):
    raise ValueError(f'{name}: missing or invalid "schedule" field')
  cron = schedule.get('cron')
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

  has_templates = 'templates' in template
  has_integration = 'integration' in template
  if not has_templates and not has_integration:
    raise ValueError(f'{name}: must have "templates" and/or "integration"')


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
    new_jobs.append(
      (
        f'{stem}.{template_name}',
        priority,
        data,
        template['schedule'],
      )
    )

  # Atomically swap out the old jobs for this file.
  for job in scheduler.get_jobs():
    if job.id.startswith(f'{stem}.'):
      job.remove()

  max_name = max((len(job_id[len(stem) + 1 :]) for job_id, *_ in new_jobs), default=0)
  max_cron = max((len(schedule['cron']) for _, _, _, schedule in new_jobs), default=0)
  max_priority = max((len(str(priority)) for _, priority, _, _ in new_jobs), default=0)
  max_hold = max((len(str(schedule['hold'])) for _, _, _, schedule in new_jobs), default=0)
  max_timeout = max((len(str(schedule['timeout'])) for _, _, _, schedule in new_jobs), default=0)
  if new_jobs:
    print(f'Loaded {content_file.parent.name}/{content_file.name}:')
  for job_id, priority, data, schedule in new_jobs:
    template_name = job_id[len(stem) + 1 :]
    # Merge any schedule overrides from config.toml (e.g. [bart.schedules.departures]).
    override = _config_mod.get_schedule_override(f'{content_file.stem}.{template_name}')
    effective = dict(schedule)
    for field in ('cron', 'hold', 'timeout'):
      if field not in override:
        continue
      val = override[field]
      if field == 'cron' and isinstance(val, str) and val.strip():
        effective[field] = val
      elif field in ('hold', 'timeout') and isinstance(val, int) and val >= 0:
        effective[field] = val
    if 'priority' in override:
      val = override['priority']
      if isinstance(val, int) and 0 <= val <= 10:
        priority = val
      else:
        print(f'Warning: ignoring invalid priority override for {job_id}: {val!r}')
    scheduler.add_job(
      enqueue,
      trigger='cron',
      args=[priority, data, effective['hold'], effective['timeout'], job_id],
      id=job_id,
      **parse_cron(effective['cron']),  # type: ignore[arg-type]
    )
    print(
      f'  · {template_name.ljust(max_name)}'
      f'  cron="{effective["cron"].ljust(max_cron)}"'
      f'  priority={str(priority).ljust(max_priority)}'
      f'  hold={str(effective["hold"]).ljust(max_hold)}s'
      f'  timeout={str(effective["timeout"]).ljust(max_timeout)}s'
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


def main() -> None:
  _config_mod.load_config()
  parser = argparse.ArgumentParser()
  parser.add_argument('--public', action='store_true', help='Only show public messages')
  parser.add_argument(
    '--flagship',
    action='store_true',
    help='Target a Vestaboard Flagship (6×22) instead of a Note (3×15)',
  )
  parser.add_argument(
    '--content-enabled',
    default='',
    help=(
      'Comma-separated list of contrib content file stems to enable '
      '(e.g. aria,bart), or * to enable all bundled contrib content. '
      'Contrib content is disabled by default.'
    ),
  )
  args = parser.parse_args()

  if args.flagship:
    vestaboard.model = vestaboard.VestaboardModel.FLAGSHIP

  content_enabled = set(filter(None, args.content_enabled.split(',')))

  board_desc = 'Flagship (6×22)' if args.flagship else 'Note (3×15)'
  extras: list[str] = []
  if content_enabled:
    if '*' in content_enabled:
      extras.append('all contrib content')
    else:
      extras.append(f'contrib: {", ".join(sorted(content_enabled))}')
  if args.public:
    extras.append('public mode')
  if not extras:
    extras.append('user content only')
  print(f'Starting e-note-ion — {board_desc}, {", ".join(extras)}')

  print('Current message:')
  try:
    print(vestaboard.get_state())
  except vestaboard.EmptyBoardError:
    print('(no current message)')
  scheduler = BackgroundScheduler(misfire_grace_time=300)
  load_content(scheduler, public_mode=args.public, content_enabled=content_enabled)
  scheduler.start()
  print(f'Scheduler started — {len(scheduler.get_jobs())} job(s) registered')

  threading.Thread(target=worker, daemon=True).start()

  try:
    while True:
      time.sleep(1)
  except KeyboardInterrupt:
    scheduler.shutdown()


if __name__ == '__main__':
  main()
