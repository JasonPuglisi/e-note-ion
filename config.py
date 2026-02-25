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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_CONFIG_PATH = Path('config.toml')
_EXAMPLE_PATH = Path('config.example.toml')

_config: dict = {}


def load_config() -> None:
  """Load config.toml from the current working directory.

  Exits with a clear message if the file is missing. Lets
  tomllib.TOMLDecodeError propagate on parse errors.
  """
  global _config
  if not _CONFIG_PATH.exists():
    print(
      f'Error: config.toml not found. Copy {_EXAMPLE_PATH} to config.toml and fill in your values.',
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
      section_lines.append(new_line)

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


def get_content_enabled() -> set[str]:
  """Return the set of enabled contrib content stems.

  Reads [scheduler].content_enabled (a TOML array of strings).
  Returns {"*"} to enable all, a set of stems for specific files,
  or an empty set when the key is absent or the list is empty.
  """
  value = _config.get('scheduler', {}).get('content_enabled')
  if not value:
    return set()
  return set(value)


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
