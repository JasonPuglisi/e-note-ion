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
