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
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, PriorityQueue
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

import integrations.vestaboard as vestaboard

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
      f'[{datetime.now().strftime("%H:%M:%S")}] Sending'
      f' | scheduled: {scheduled.strftime("%H:%M:%S")}'
      f' | priority: {message.priority}'
      f' | hold: {message.hold}s'
    )
    try:
      vestaboard.set_state(message.data['templates'], message.data['variables'])
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
    if public_mode and not template['public']:
      continue
    priority = template['priority']
    if not isinstance(priority, int) or not (0 <= priority <= 10):
      raise ValueError(f'{stem}.{template_name}: priority must be an integer between 0 and 10, got {priority!r}')
    new_jobs.append(
      (
        f'{stem}.{template_name}',
        priority,
        {
          'templates': template['templates'],
          'variables': content['variables'],
        },
        template['schedule'],
      )
    )

  # Atomically swap out the old jobs for this file.
  for job in scheduler.get_jobs():
    if job.id.startswith(f'{stem}.'):
      job.remove()

  for job_id, priority, data, schedule in new_jobs:
    scheduler.add_job(
      enqueue,
      trigger='cron',
      args=[priority, data, schedule['hold'], schedule['timeout'], job_id],
      id=job_id,
      **parse_cron(schedule['cron']),  # type: ignore[arg-type]
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


def watch_content(
  scheduler: BackgroundScheduler,
  public_mode: bool,
  content_enabled: set[str],
  interval: int = 5,
) -> None:
  # Daemon thread that polls content directories every `interval` seconds and
  # reloads jobs whenever files are added, removed, or modified.
  def eligible_files() -> set[Path]:
    files: set[Path] = set()
    user_path = Path('content') / 'user'
    if user_path.is_dir():
      files |= set(user_path.glob('*.json'))
    contrib_path = Path('content') / 'contrib'
    if contrib_path.is_dir() and content_enabled:
      for p in contrib_path.glob('*.json'):
        if '*' in content_enabled or p.stem in content_enabled:
          files.add(p)
    return files

  mtimes: dict[Path, float] = {p: p.stat().st_mtime for p in eligible_files()}

  while True:
    time.sleep(interval)
    try:
      current = eligible_files()
      known = set(mtimes)

      for path in current - known:
        try:
          _load_file(scheduler, path, public_mode)
          mtimes[path] = path.stat().st_mtime
          print(f'Content loaded: {path.parent.name}/{path.name}')
        except Exception as e:
          print(f'Error loading {path.parent.name}/{path.name}: {e}')

      for path in known - current:
        stem = f'{path.parent.name}.{path.stem}'
        for job in scheduler.get_jobs():
          if job.id.startswith(f'{stem}.'):
            job.remove()
        del mtimes[path]
        print(f'Content removed: {path.parent.name}/{path.name}')

      for path in current & known:
        try:
          mtime = path.stat().st_mtime
        except FileNotFoundError:
          continue
        if mtime != mtimes[path]:
          try:
            _load_file(scheduler, path, public_mode)
            mtimes[path] = mtime
            print(f'Content reloaded: {path.parent.name}/{path.name}')
          except Exception as e:
            print(f'Error reloading {path.parent.name}/{path.name}: {e}')
    except Exception as e:
      print(f'Watcher error: {e}')


def main() -> None:
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

  print('Current message:')
  print(vestaboard.get_state())
  scheduler = BackgroundScheduler(misfire_grace_time=300)
  load_content(scheduler, public_mode=args.public, content_enabled=content_enabled)
  scheduler.start()

  threading.Thread(target=worker, daemon=True).start()
  threading.Thread(
    target=watch_content,
    args=(scheduler, args.public, content_enabled),
    daemon=True,
  ).start()

  try:
    while True:
      time.sleep(1)
  except KeyboardInterrupt:
    scheduler.shutdown()


if __name__ == '__main__':
  main()
