# config.py
#
# TOML configuration loader.
#
# Call load_config() once at startup (e.g. from main()). All other functions
# read from the module-level cache and may be called from any thread.
#
# Integration modules import config inside their functions so they can be
# imported in tests without a real config file present.

import re
import sys
import tomllib
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CONFIG_PATH = Path('config.toml')

_config: dict = {}


def load_config(path: Path | None = None) -> None:
  """Load config.toml, optionally from a custom path.

  When *path* is given, updates the module-level ``_CONFIG_PATH`` so that
  ``write_section_values`` and ``write_config_section`` persist to the
  same file.  Exits with a clear message if the file is missing.  Lets
  ``tomllib.TOMLDecodeError`` propagate on parse errors.
  """
  global _config, _CONFIG_PATH
  if path is not None:
    _CONFIG_PATH = path
  if not _CONFIG_PATH.exists():
    print(
      f'Error: config.toml not found at {_CONFIG_PATH.resolve()}. '
      'Copy config.example.toml, fill in your API keys, and try again.',
      file=sys.stderr,
    )
    raise SystemExit(1)
  with open(_CONFIG_PATH, 'rb') as f:
    _config = tomllib.load(f)


def get(section: str, key: str) -> str:
  """Return a required string config value.

  Raises ValueError with a descriptive message if the section or key is
  missing, or if the value is an empty string.
  """
  value = _config.get(section, {}).get(key)
  if not value:
    raise ValueError(f'Missing required config key [{section}].{key} in config.toml')
  return str(value)


def has_section(section: str) -> bool:
  """Return True if the given top-level section exists in the loaded config."""
  return section in _config


def get_optional(section: str, key: str, default: str = '') -> str:
  """Return an optional string config value, or default if absent."""
  value = _config.get(section, {}).get(key)
  if value is None:
    return default
  return str(value)


def write_section_values(section: str, values: dict[str, str | int]) -> None:
  """Write key-value pairs into [section] in config.toml in-place.

  Updates the in-memory config cache and persists to disk, preserving all
  comments and other sections. Active and commented-out versions of a key are
  both replaced. New keys are appended to the end of the section.

  Raises FileNotFoundError if config.toml does not exist.
  Raises ValueError if the section header is not found in the file.
  """
  if not _CONFIG_PATH.exists():
    raise FileNotFoundError(f'config.toml not found at {_CONFIG_PATH.resolve()}')

  lines = _CONFIG_PATH.read_text().splitlines(keepends=True)

  section_start: int | None = None
  section_end = len(lines)

  for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == f'[{section}]':
      section_start = i + 1
    elif section_start is not None and stripped.startswith('[') and not stripped.startswith('#'):
      section_end = i
      break

  if section_start is None:
    raise ValueError(f'No [{section}] section found in config.toml')

  section_lines = list(lines[section_start:section_end])

  for key, value in values.items():
    val_str = f'"{value}"' if isinstance(value, str) else str(value)
    new_line = f'{key} = {val_str}\n'
    found = False
    for j, sl in enumerate(section_lines):
      if re.match(rf'^{re.escape(key)}\s*=', sl):
        section_lines[j] = new_line
        found = True
        break
      if re.match(rf'^#\s*{re.escape(key)}\s*=', sl):
        section_lines[j] = new_line
        found = True
        break
    if not found:
      # Insert before any trailing blank lines so section separators
      # stay between sections rather than before the new key.
      insert_at = len(section_lines)
      while insert_at > 0 and section_lines[insert_at - 1].strip() == '':
        insert_at -= 1
      section_lines.insert(insert_at, new_line)

  lines[section_start:section_end] = section_lines
  _CONFIG_PATH.write_text(''.join(lines))
  _config.setdefault(section, {}).update(values)


def get_timezone() -> ZoneInfo | None:
  """Return the configured timezone, or None to use the system local timezone.

  Reads [scheduler].timezone from config.toml. When absent or empty, returns
  None, which causes datetime.astimezone(None) to fall back to the system
  local timezone (i.e. whatever TZ is set to in the environment).

  Raises ValueError with a clear message if the timezone name is invalid.
  """
  tz_name = get_optional('scheduler', 'timezone')
  if not tz_name:
    return None
  try:
    return ZoneInfo(tz_name)
  except ZoneInfoNotFoundError:
    raise ValueError(
      f'Unknown timezone {tz_name!r} in [scheduler].timezone — '
      'use an IANA name such as "America/Los_Angeles" or "Europe/London"'
    ) from None


def get_optional_bool(section: str, key: str, default: bool = False) -> bool:
  """Return an optional boolean config value, or default if absent.

  Uses the raw TOML value rather than casting through str, so TOML booleans
  (e.g. `public = true`) are returned as Python bools correctly.
  """
  value = _config.get(section, {}).get(key)
  if value is None:
    return default
  return bool(value)


def get_model() -> str:
  """Return the configured display model: 'note' (default) or 'flagship'.

  Reads [scheduler].model from config.toml. Raises ValueError if the value
  is present but not a recognised model name.
  """
  value = get_optional('scheduler', 'model', 'note')
  if value not in ('note', 'flagship'):
    raise ValueError(
      f"Unknown model {value!r} in [scheduler].model — use 'note' (3\u00d715, default) or 'flagship' (6\u00d722)"
    )
  return value


def get_public_mode() -> bool:
  """Return True if public mode is enabled in config.toml.

  Reads [scheduler].public. Defaults to False when absent.
  """
  return get_optional_bool('scheduler', 'public', default=False)


def get_content_enabled() -> set[str] | None:
  """Return the configured content filter, or None if the key is absent.

  Reads [scheduler].content_enabled (a TOML array of strings).
  Returns None when the key is absent (user content loads unconditionally,
  no contrib content loads). When the key is present, returns a set of stems
  (possibly empty) that filters both user and contrib directories: {"*"} to
  enable all, or specific stems such as {"bart", "my_quotes"}.
  """
  scheduler_cfg = _config.get('scheduler', {})
  if 'content_enabled' not in scheduler_cfg:
    return None
  value = scheduler_cfg['content_enabled']
  return set(value) if value else set()


def get_credentials(integration_name: str) -> dict[str, dict[str, Any]]:
  """Return named credentials scoped to the given integration, keyed by credential name.

  Reads webhook.credentials from config. Supports both the standard flat credential
  sections (e.g. [webhook.credentials.plex]) and the nested message namespace
  ([webhook.credentials.message.admin] and [webhook.credentials.message.friend.<name>]).
  """
  creds = _config.get('webhook', {}).get('credentials', {})
  if not isinstance(creds, dict):
    return {}
  result: dict[str, dict[str, Any]] = {}
  for name, data in creds.items():
    if isinstance(data, dict) and integration_name in data.get('webhooks', []):
      result[name] = data
  # Message credentials live in a nested namespace: webhook.credentials.message.*
  if integration_name == 'message':
    msg_creds = creds.get('message', {})
    if isinstance(msg_creds, dict):
      admin = msg_creds.get('admin')
      if isinstance(admin, dict):
        result['admin'] = admin
      friends = msg_creds.get('friend', {})
      if isinstance(friends, dict):
        for friend_name, friend_data in friends.items():
          if isinstance(friend_data, dict):
            result[friend_name] = friend_data
  return result


def get_message_friend(name: str) -> dict[str, Any] | None:
  """Return [message.friends.<name>] config dict, or None if not configured."""
  friend = _config.get('message', {}).get('friends', {}).get(name)
  return dict(friend) if isinstance(friend, dict) else None


def write_config_section(section: str, values: dict[str, str | int | list[str]]) -> None:
  """Create-or-update a config section in config.toml.

  Handles dotted section names (e.g. 'webhook.credentials.alice').
  Supports str, int, and list[str] values (rendered as TOML inline arrays).
  Creates the section if absent; inserts it after the last existing section
  that shares the same parent prefix (e.g. 'webhook.credentials.bob' goes
  after 'webhook.credentials.alice'), or appends at end if none exists.
  Always produces exactly one blank line between sections and a trailing newline.
  Updates the in-memory config cache.
  """
  if not _CONFIG_PATH.exists():
    raise FileNotFoundError(f'config.toml not found at {_CONFIG_PATH.resolve()}')

  lines = _CONFIG_PATH.read_text().splitlines(keepends=True)
  header = f'[{section}]'

  section_start: int | None = None
  section_end = len(lines)

  for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == header:
      section_start = i + 1
    elif section_start is not None and stripped.startswith('[') and not stripped.startswith('#'):
      section_end = i
      break

  def _render(v: str | int | list[str]) -> str:
    if isinstance(v, list):
      return '[' + ', '.join(f'"{item}"' for item in v) + ']'
    if isinstance(v, int):
      return str(v)
    return f'"{v}"'

  if section_start is not None:
    section_lines = list(lines[section_start:section_end])
    for key, value in values.items():
      new_line = f'{key} = {_render(value)}\n'
      found = False
      for j, sl in enumerate(section_lines):
        if re.match(rf'^{re.escape(key)}\s*=', sl):
          section_lines[j] = new_line
          found = True
          break
        if re.match(rf'^#\s*{re.escape(key)}\s*=', sl):
          section_lines[j] = new_line
          found = True
          break
      if not found:
        insert_at = len(section_lines)
        while insert_at > 0 and section_lines[insert_at - 1].strip() == '':
          insert_at -= 1
        section_lines.insert(insert_at, new_line)
    lines[section_start:section_end] = section_lines
  else:
    # Find insertion point: after the last section that shares our parent prefix
    # (e.g. 'webhook.credentials.bob' groups with 'webhook.credentials.alice').
    parent_prefix = '.'.join(section.split('.')[:-1])
    sibling_end: int | None = None
    i = 0
    while i < len(lines):
      stripped = lines[i].strip()
      if stripped.startswith('[') and not stripped.startswith('#'):
        inner = stripped[1:].rstrip(']')
        if inner == parent_prefix or inner.startswith(f'{parent_prefix}.'):
          j = i + 1
          while j < len(lines):
            if lines[j].strip().startswith('[') and not lines[j].strip().startswith('#'):
              break
            j += 1
          sibling_end = j
      i += 1

    new_lines = [f'{header}\n']
    for key, value in values.items():
      new_lines.append(f'{key} = {_render(value)}\n')

    if sibling_end is not None and sibling_end < len(lines):
      # Insert between two sections: append a trailing blank line so the
      # section that follows keeps its preceding separator.
      new_lines.append('\n')
      lines[sibling_end:sibling_end] = new_lines
    else:
      # Append at end: strip any trailing blank lines, add exactly one separator.
      while lines and lines[-1].strip() == '':
        lines.pop()
      if lines and not lines[-1].endswith('\n'):
        lines[-1] += '\n'
      lines.append('\n')
      lines.extend(new_lines)

  # Ensure file ends with a single newline.
  while lines and lines[-1] == '\n' and len(lines) >= 2 and lines[-2] == '\n':
    lines.pop()
  if lines and not lines[-1].endswith('\n'):
    lines[-1] += '\n'

  _CONFIG_PATH.write_text(''.join(lines))

  # Update in-memory cache for dotted section names.
  parts = section.split('.')
  d = _config
  for part in parts[:-1]:
    d = d.setdefault(part, {})
  last_part = parts[-1]
  existing = d.get(last_part)
  if isinstance(existing, dict):
    existing.update(values)
  else:
    d[last_part] = dict(values)


def delete_config_section(section: str) -> None:
  """Remove a [section] block from config.toml in-place.

  Removes the section header, all its key-value pairs, and any preceding
  blank-line separator. No-op if the section is not present.

  Raises FileNotFoundError if config.toml does not exist.
  """
  if not _CONFIG_PATH.exists():
    raise FileNotFoundError(f'config.toml not found at {_CONFIG_PATH.resolve()}')

  lines = _CONFIG_PATH.read_text().splitlines(keepends=True)
  header = f'[{section}]'

  section_start: int | None = None
  section_end = len(lines)

  for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped == header:
      section_start = i
    elif section_start is not None and stripped.startswith('[') and not stripped.startswith('#'):
      section_end = i
      break

  if section_start is None:
    return  # nothing to delete

  # Delete the section header through to (but not including) the next section.
  # section_end already covers any trailing blank lines between this section
  # and the next header, so the blank line *before* section_start is preserved
  # as the separator for whatever follows.
  del lines[section_start:section_end]

  # Collapse any double blank lines that could arise (e.g. two separators
  # merging when the deleted section had no preceding blank of its own).
  result: list[str] = []
  prev_blank = False
  for line in lines:
    is_blank = line.strip() == ''
    if is_blank and prev_blank:
      continue
    result.append(line)
    prev_blank = is_blank
  lines = result

  # Strip trailing blank lines left behind when the deleted section was last;
  # ensure the file still ends with exactly one newline.
  while lines and lines[-1].strip() == '':
    lines.pop()
  if lines and not lines[-1].endswith('\n'):
    lines[-1] += '\n'

  _CONFIG_PATH.write_text(''.join(lines))

  # Update in-memory cache.
  parts = section.split('.')
  d = _config
  for part in parts[:-1]:
    if not isinstance(d.get(part), dict):
      return
    d = d[part]
  d.pop(parts[-1], None)


def migrate_message_credentials() -> int:
  """Migrate old-style flat message credentials to the nested namespace.

  Detects [webhook.credentials.<name>] sections with webhooks = ["message"] and
  rewrites them as [webhook.credentials.message.admin] (for the old 'message-admin'
  credential) or [webhook.credentials.message.friend.<name>] (for all friends).
  Removes the old flat sections after writing the new ones.

  Returns the number of credentials migrated. This migration shim is removed in 2.0.

  Raises FileNotFoundError if config.toml does not exist.
  """
  creds = _config.get('webhook', {}).get('credentials', {})
  if not isinstance(creds, dict):
    return 0

  to_migrate: list[tuple[str, dict]] = [
    (name, data) for name, data in creds.items() if isinstance(data, dict) and data.get('webhooks') == ['message']
  ]

  for name, data in to_migrate:
    new_section = (
      'webhook.credentials.message.admin' if name == 'message-admin' else f'webhook.credentials.message.friend.{name}'
    )
    write_config_section(new_section, data)
    delete_config_section(f'webhook.credentials.{name}')

  return len(to_migrate)


def get_schedule_override(template_id: str) -> dict:
  """Return schedule overrides for a named template, or {} if not configured.

  template_id is '<file_stem>.<template_name>' (e.g. 'bart.departures').
  Reads from [<file_stem>.schedules.<template_name>] in config.toml.
  """
  parts = template_id.split('.', 1)
  if len(parts) != 2:
    return {}
  section, template_name = parts
  overrides = _config.get(section, {}).get('schedules', {}).get(template_name, {})
  return dict(overrides) if isinstance(overrides, dict) else {}
