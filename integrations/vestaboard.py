# integrations/vestaboard.py
#
# Vestaboard API client — supports both the Note (3×15) and Flagship (6×22).
#   get_state() — reads the current board layout.
#   set_state() — renders a content template and writes it to the board.
#
# Set the module-level `model` variable before making any calls to select the
# board type. It defaults to VestaboardModel.NOTE.
#
# The Read/Write API lives at https://rw.vestaboard.com. Both GET and POST
# use the X-Vestaboard-Read-Write-Key header for authentication. A POST body
# is a raw JSON array-of-arrays of integer character codes with no wrapper key.

import json
import os
import random
import re
import sys
from enum import Enum
from typing import Literal

import requests

# --- API configuration ---

try:
  _API_KEY = os.environ['VESTABOARD_KEY']
except KeyError:
  print('Vestaboard API key missing in environment variable `VESTABOARD_KEY`')
  sys.exit(1)

_HOST = 'https://rw.vestaboard.com'
_HEADERS = {
  'X-Vestaboard-Read-Write-Key': _API_KEY,
  'Content-Type': 'application/json',
}

# --- Board model ---


class VestaboardModel(Enum):
  NOTE = (3, 15)  # Vestaboard Note:     3 rows × 15 columns
  FLAGSHIP = (6, 22)  # Vestaboard Flagship: 6 rows × 22 columns

  def __init__(self, rows: int, cols: int) -> None:
    self.rows = rows
    self.cols = cols


# Active board model. Set this before calling get_state() or set_state().
model: VestaboardModel = VestaboardModel.NOTE


# --- Character codes ---
# https://docs.vestaboard.com/docs/characterCodes
#
# Index = character code, value = canonical display character.
# Empty string marks reserved or unused code positions.
_CHAR_MAP: tuple[str, ...] = (
  ' ',  # 0   blank
  'A',
  'B',
  'C',
  'D',
  'E',
  'F',
  'G',
  'H',
  'I',
  'J',
  'K',
  'L',
  'M',  # 1-13
  'N',
  'O',
  'P',
  'Q',
  'R',
  'S',
  'T',
  'U',
  'V',
  'W',
  'X',
  'Y',
  'Z',  # 14-26
  '1',
  '2',
  '3',
  '4',
  '5',
  '6',
  '7',
  '8',
  '9',
  '0',  # 27-36
  '!',
  '@',
  '#',
  '$',
  '(',
  ')',  # 37-42
  '',
  '-',
  '',
  '+',
  '&',
  '=',
  ';',
  ':',  # 43-50
  '',
  "'",
  '"',
  '%',
  ',',
  '.',  # 51-56
  '',
  '',
  '/',
  '?',
  '',  # 57-61
  '❤',  # 62  ❤ (Note) / ° (Flagship)
)

# ANSI terminal representations for color chip codes 63-71.
# Code 71 (filled) is board-color-dependent and handled separately.
_COLOR_DISPLAY: tuple[str, ...] = (
  '\033[38;2;194;57;35m▉',  # 63 red
  '\033[38;2;236;116;36m▉',  # 64 orange
  '\033[38;2;254;179;54m▉',  # 65 yellow
  '\033[38;2;58;140;66m▉',  # 66 green
  '\033[38;2;48;118;202m▉',  # 67 blue
  '\033[38;2;93;47;124m▉',  # 68 violet
  '\033[38;2;255;255;255m▉',  # 69 white
  '\033[38;2;0;0;0m▉',  # 70 black
)

# Reverse lookup for encoding: character → code.
# The heart has two Unicode representations; both map to 62.
# On the Flagship, code 62 is a degree sign, so '°' also maps to 62.
_CHAR_CODES: dict[str, int] = {ch: code for code, ch in enumerate(_CHAR_MAP) if ch}
_CHAR_CODES['❤️'] = 62  # U+2764 + U+FE0F variation selector
_CHAR_CODES['°'] = 62  # degree sign on the Flagship (same code as ❤ on Note)

# Truncation strategy: how to shorten a line that exceeds model.cols.
#   hard     — cut at the column limit, mid-word if necessary (default)
#   word     — cut at the last full word that fits
#   ellipsis — cut at the last full word and append '...'
TruncationStrategy = Literal['hard', 'word', 'ellipsis']

# Short color tags usable in format strings and integration output.
# Each tag is exactly 3 characters and encodes to a Vestaboard color square.
_COLOR_TAGS: dict[str, int] = {
  '[R]': 63,  # red
  '[O]': 64,  # orange
  '[Y]': 65,  # yellow
  '[G]': 66,  # green
  '[B]': 67,  # blue
  '[V]': 68,  # violet
  '[W]': 69,  # white
  '[K]': 70,  # black
}


# --- Board color ---


class VestaboardColor(Enum):
  BLACK = 0  # standard black board: code 71 (filled) renders white
  WHITE = 1  # white board: code 71 (filled) renders black


# --- Rendering ---


def _display_char(code: int, color: VestaboardColor = VestaboardColor.BLACK) -> str:
  if code < len(_CHAR_MAP):
    ch = _CHAR_MAP[code]
    if ch == '❤':
      # Note: red heart. Flagship: degree sign (same code, different glyph).
      return '\033[38;2;194;57;35m❤' if model is VestaboardModel.NOTE else '°'
    return ch
  color_idx = code - 63
  if 0 <= color_idx < len(_COLOR_DISPLAY):
    return _COLOR_DISPLAY[color_idx]
  if code == 71:
    return '\033[38;2;255;255;255m▉' if color is VestaboardColor.BLACK else '\033[38;2;0;0;0m▉'
  return '?'


def render_grid(
  grid: list[list[int]],
  color: VestaboardColor = VestaboardColor.BLACK,
) -> str:
  """Render a character code grid as a bordered string for console output."""
  bar = '─' * (model.cols + 2)
  lines = [f'┌{bar}┐']
  for row in grid:
    cells = ''.join(f'{_display_char(x, color)}\033[0m' for x in row)
    lines.append(f'│ {cells} │')
  lines.append(f'└{bar}┘')
  return '\n'.join(lines)


# --- State ---


class VestaboardState:
  """Snapshot of the current board layout returned by get_state()."""

  def __init__(
    self,
    state: dict,
    color: VestaboardColor = VestaboardColor.BLACK,
  ) -> None:
    current = state['currentMessage']
    self.id: str = current['id']
    self.appeared: str = current['appeared']
    self.layout: list[list[int]] = json.loads(current['layout'])
    self.color = color

  def __str__(self) -> str:
    return render_grid(self.layout, self.color)


# --- API calls ---


def get_state(color: VestaboardColor = VestaboardColor.BLACK) -> VestaboardState:
  """Fetch and return the current board state."""
  r = requests.get(_HOST, headers=_HEADERS, timeout=10)
  r.raise_for_status()
  return VestaboardState(r.json(), color)


# --- Encoding ---


def _encode_char(ch: str) -> int:
  """Map a single character to its Vestaboard code (0 = blank if unknown)."""
  return _CHAR_CODES.get(ch.upper(), 0)


def _encode_line(text: str) -> list[int]:
  """Encode a text string into a row of model.cols integer character codes.

  Handles the two-character ❤️ emoji sequence and three-character color tags
  (e.g. [G], [R]). Output is truncated to model.cols characters and
  zero-padded on the right.
  """
  cols = model.cols
  codes: list[int] = []
  i = 0
  while i < len(text) and len(codes) < cols:
    if text[i : i + 2] == '❤️':
      codes.append(62)
      i += 2
    elif (tag := text[i : i + 3]) in _COLOR_TAGS:
      codes.append(_COLOR_TAGS[tag])
      i += 3
    else:
      codes.append(_encode_char(text[i]))
      i += 1
  codes += [0] * (cols - len(codes))
  return codes


def _expand_format(
  fmt: list[str],
  variables: dict[str, list],
) -> list[str]:
  """Expand a format list into concrete lines by substituting {variable}
  placeholders.

  A format entry that is exactly '{variable}' is replaced by all lines from
  the chosen option (which may expand a single entry into multiple output
  lines). An inline '{variable}' within other text is replaced by the first
  line of the chosen option.

  Options are chosen at random.
  """
  chosen: dict[str, list[str]] = {name: random.choice(options) for name, options in variables.items()}  # nosec B311

  def _sub(match: re.Match[str]) -> str:
    opt = chosen.get(match.group(1), [''])
    return opt[0] if opt else ''

  lines: list[str] = []
  for entry in fmt:
    m = re.fullmatch(r'\{(\w+)\}', entry.strip())
    if m:
      # Whole-line variable: expand to all lines of the chosen option.
      lines.extend(chosen.get(m.group(1), ['']))
    else:
      # Inline substitution: use first line of the chosen option.
      lines.append(re.sub(r'\{(\w+)\}', _sub, entry))

  return lines


def _display_len(text: str) -> int:
  """Count display characters, treating ❤️ and color tags as single chars."""
  count = 0
  i = 0
  while i < len(text):
    if text[i : i + 2] == '❤️':
      i += 2
    elif text[i : i + 3] in _COLOR_TAGS:
      i += 3
    else:
      i += 1
    count += 1
  return count


def _truncate(
  text: str,
  max_cols: int,
  strategy: TruncationStrategy = 'hard',
) -> str:
  """Truncate text to at most max_cols display characters.

  Strategies:
    hard     — cut at the column limit, mid-word if necessary.
    word     — cut at the last full word boundary that fits.
    ellipsis — cut at the last full word boundary and append '...'.
  """
  if _display_len(text) <= max_cols:
    return text
  target = max_cols - (3 if strategy == 'ellipsis' else 0)
  result: list[str] = []
  last_word_end = -1  # len(result) just before the most recent space
  count = 0
  i = 0
  while i < len(text) and count < target:
    if text[i : i + 2] == '❤️':
      result.append('❤️')
      i += 2
    elif (tag := text[i : i + 3]) in _COLOR_TAGS:
      result.append(tag)
      i += 3
    else:
      if text[i] == ' ' and strategy in ('word', 'ellipsis'):
        last_word_end = len(result)
      result.append(text[i])
      i += 1
    count += 1
  if strategy == 'hard' or last_word_end < 0:
    return ''.join(result)
  base = ''.join(result[:last_word_end])
  return base + ('...' if strategy == 'ellipsis' else '')


def _wrap_lines(
  lines: list[str],
  truncation: TruncationStrategy = 'hard',
) -> list[str]:
  """Word-wrap lines to fit model.cols, returning at most model.rows lines.

  Each input line is split on spaces and words are packed greedily into rows.
  A word that alone exceeds model.cols is truncated using `truncation`. Lines
  are never joined together — wrapping only splits; short lines pass through
  unchanged.
  """
  cols = model.cols
  result: list[str] = []
  for line in lines:
    if _display_len(line) <= cols:
      result.append(line)
      continue
    words = line.split(' ')
    current: list[str] = []
    current_len = 0
    for word in words:
      word_len = _display_len(word)
      if word_len > cols:
        # Word alone won't fit: flush current row, then truncate the word.
        if current:
          result.append(' '.join(current))
          current = []
          current_len = 0
        result.append(_truncate(word, cols, truncation))
        continue
      if not current:
        current = [word]
        current_len = word_len
      elif current_len + 1 + word_len <= cols:
        current.append(word)
        current_len += 1 + word_len
      else:
        result.append(' '.join(current))
        current = [word]
        current_len = word_len
    if current:
      result.append(' '.join(current))
  return result[: model.rows]


def _build_grid(lines: list[str]) -> list[list[int]]:
  """Encode lines into a model.rows × model.cols integer grid.

  Expects lines already wrapped and truncated to model.rows by _wrap_lines.
  Missing rows are filled with blanks.
  """
  grid = [_encode_line(line) for line in lines[: model.rows]]
  while len(grid) < model.rows:
    grid.append([0] * model.cols)
  return grid


# --- Writing ---


class BoardLockedError(Exception):
  """Raised when the Vestaboard returns 423 (rate-limited or quiet hours)."""


def set_state(
  templates: list[dict],
  variables: dict[str, list],
  truncation: TruncationStrategy = 'hard',
) -> None:
  """Render a template and write it to the Vestaboard.

  `templates` is the list of {"format": [...]} objects from the content JSON.
  One entry is chosen at random each time.

  Raises BoardLockedError on HTTP 423 so the caller can decide whether to
  retry. All other HTTP errors raise requests.exceptions.HTTPError.
  """
  template = random.choice(templates)  # nosec B311
  lines = _expand_format(template['format'], variables)
  lines = _wrap_lines(lines, truncation)
  grid = _build_grid(lines)
  print(render_grid(grid))
  r = requests.post(_HOST, json=grid, headers=_HEADERS, timeout=10)
  if r.status_code == 423:
    raise BoardLockedError('board is locked (rate-limited or quiet hours)')
  r.raise_for_status()
