#!/usr/bin/env python
"""Prove config writes work when config.toml is a single-file bind mount.

Docker's documented deployment mounts config.toml as an individual file
(`-v /host/config.toml:/app/config.toml`). That pins the inode, so the
temp-file + os.replace() path in config._atomic_write() fails with EBUSY and
falls back to an in-place write.

No unit test can reproduce that — it needs a real bind mount — so this runs
inside the CI Docker job. Without it the fallback is untested in the only
environment where it is actually taken.

Exercises the escaping path too: the value contains a quote and a backslash,
which before #592 produced a config.toml that tomllib could no longer parse.
"""

import sys
from pathlib import Path

import config

_VALUE = 'quote" backslash\\ done'


def main() -> int:
  config.load_config()

  # Which write path actually ran matters. os.replace() creates a new inode; an
  # in-place write keeps the old one. Reporting this is the difference between
  # "the smoke test passed" and "the fallback is exercised" — without it, a
  # successful atomic write here would look identical to a successful fallback
  # and the fallback would stay untested.
  before = Path(config._CONFIG_PATH).stat().st_ino

  config.write_config_section('smoke', {'token': _VALUE, 'items': ['a"b', 'c\\d']})

  after = Path(config._CONFIG_PATH).stat().st_ino
  path = 'in-place fallback' if before == after else 'atomic os.replace'
  print(f'write path taken: {path} (inode {before} -> {after})')

  # Re-read from disk rather than trusting the in-memory cache, so a corrupt
  # file surfaces here instead of silently passing.
  config._config.clear()
  config.load_config()

  got = config.get('smoke', 'token')
  if got != _VALUE:
    print(f'FAIL: token round-trip mismatch: {got!r} != {_VALUE!r}', file=sys.stderr)
    return 1

  print('OK: config write survived a single-file bind mount and re-parsed cleanly')
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
