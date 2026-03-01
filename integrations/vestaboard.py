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
import random
import re
import unicodedata
from enum import Enum
from typing import Literal

import requests

# --- API configuration ---

_HOST = 'https://rw.vestaboard.com'


def _get_headers() -> dict[str, str]:
  """Return the auth headers for the Vestaboard API.

  Imports config inside the function so the module can be imported without a
  config file present (e.g. in tests that don't exercise the API).
  """
  import config as _config_mod

  api_key = _config_mod.get('vestaboard', 'api_key')
  return {
    'X-Vestaboard-Read-Write-Key': api_key,
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

# Sentinels for brace escaping in _expand_format. These bytes cannot appear
# in user-supplied content, so they serve as safe placeholders for {{ and }}.
_ESC_BRACE_OPEN = '\x00'  # replacement for {{ (escaped literal {)
_ESC_BRACE_CLOSE = '\x01'  # replacement for }} (escaped literal })


def _next_token(text: str, i: int) -> tuple[str, int]:
  """Return (raw_token, chars_consumed) for the source token starting at i.

  Recognises multi-char sequences in priority order:
    ❤️  (2 chars)      — 1 display char; encodes to code 62
    [X] color tag      — 1 display char; encodes to a color square
    [[X]] escaped tag  — 3 display chars; encodes as literal [, X, ]
    any single char    — 1 display char
  """
  if text[i : i + 2] == '❤️':
    return ('❤️', 2)
  tag3 = text[i : i + 3]
  if tag3 in _COLOR_TAGS:
    return (tag3, 3)
  if (
    text[i : i + 2] == '[[' and i + 4 < len(text) and f'[{text[i + 2]}]' in _COLOR_TAGS and text[i + 3 : i + 5] == ']]'
  ):
    return (text[i : i + 5], 5)
  return (text[i], 1)


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
    self.appeared: int | str = current['appeared']  # int (virtual) or str (physical)
    self.layout: list[list[int]] = json.loads(current['layout'])
    self.color = color

  def __str__(self) -> str:
    return render_grid(self.layout, self.color)


# --- API calls ---


def get_state(color: VestaboardColor = VestaboardColor.BLACK) -> VestaboardState:
  """Fetch and return the current board state."""
  r = requests.get(_HOST, headers=_get_headers(), timeout=10)
  if r.status_code == 404:
    raise EmptyBoardError('board has no current message')
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Vestaboard API error: {e.response.status_code} {e.response.reason}') from None
  return VestaboardState(r.json(), color)


# --- Encoding ---


def _encode_char(ch: str) -> int:
  """Map a single character to its Vestaboard code (0 = blank if unknown).

  Accented and diacritic characters are normalized via NFKD decomposition
  before lookup: ï → i, é → e, ñ → n, ü → u, etc.
  """
  normalized = unicodedata.normalize('NFKD', ch).encode('ascii', 'ignore').decode('ascii')
  return _CHAR_CODES.get((normalized or ch).upper(), 0)


def _encode_line(text: str) -> list[int]:
  """Encode a text string into a row of model.cols integer character codes.

  Handles the two-character ❤️ emoji sequence, three-character color tags
  (e.g. [G], [R]), and five-character escaped color tags (e.g. [[G]] encodes
  as literal [, G, ] rather than a color square). Output is truncated to
  model.cols characters and zero-padded on the right.
  """
  cols = model.cols
  codes: list[int] = []
  i = 0
  while i < len(text) and len(codes) < cols:
    tok, consumed = _next_token(text, i)
    i += consumed
    if tok == '❤️':
      codes.append(62)
    elif tok in _COLOR_TAGS:
      codes.append(_COLOR_TAGS[tok])
    elif len(tok) == 5:  # escaped color tag [[X]]: emit [, X, ] individually
      for ch in ('[', tok[2], ']'):
        if len(codes) < cols:
          codes.append(_encode_char(ch))
    else:
      codes.append(_encode_char(tok))
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

  Brace escaping: '{{' and '}}' produce literal '{' and '}' without
  triggering variable substitution. Color tag escaping is handled by
  _encode_line (see [[X]] in _next_token).
  """
  chosen: dict[str, list[str]] = {name: random.choice(options) for name, options in variables.items()}  # nosec B311

  def _sub(match: re.Match[str]) -> str:
    opt = chosen.get(match.group(1), [''])
    return opt[0] if opt else ''

  lines: list[str] = []
  for entry in fmt:
    # Replace escaped braces with sentinels so they survive regex processing.
    entry = entry.replace('{{', _ESC_BRACE_OPEN).replace('}}', _ESC_BRACE_CLOSE)
    m = re.fullmatch(r'\{(\w+)\}', entry.strip())
    if m:
      # Whole-line variable: expand to all lines of the chosen option.
      lines.extend(chosen.get(m.group(1), ['']))
    else:
      # Inline substitution: use first line of the chosen option.
      result = re.sub(r'\{(\w+)\}', _sub, entry)
      lines.append(result.replace(_ESC_BRACE_OPEN, '{').replace(_ESC_BRACE_CLOSE, '}'))

  return lines


def display_len(text: str) -> int:
  """Count display characters, treating ❤️ and color tags as single chars.

  Escaped color tags (e.g. [[G]]) count as 3 display chars (literal [, G, ]).
  """
  count = 0
  i = 0
  while i < len(text):
    tok, consumed = _next_token(text, i)
    i += consumed
    count += 3 if len(tok) == 5 else 1
  return count


def truncate_line(
  text: str,
  max_cols: int,
  strategy: TruncationStrategy = 'hard',
) -> str:
  """Truncate text to at most max_cols display characters.

  Strategies:
    hard     — cut at the column limit, mid-word if necessary.
    word     — cut at the last full word boundary that fits.
    ellipsis — cut at the last full word boundary and append '...'.

  Multi-char tokens (❤️, color tags, escaped color tags) are never split.
  Escaped color tags (e.g. [[G]]) count as 3 display chars and are treated
  as atomic: if they don't fit entirely, they are dropped as a unit.
  """
  if display_len(text) <= max_cols:
    return text
  target = max_cols - (3 if strategy == 'ellipsis' else 0)
  result: list[str] = []
  last_word_end = -1  # len(result) just before the most recent space
  count = 0
  i = 0
  while i < len(text) and count < target:
    tok, consumed = _next_token(text, i)
    tok_display = 3 if len(tok) == 5 else 1
    if count + tok_display > target:
      break
    if tok_display == 1 and tok == ' ' and strategy in ('word', 'ellipsis'):
      last_word_end = len(result)
    result.append(tok)
    i += consumed
    count += tok_display
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

  Exception: when `truncation` is 'ellipsis', long lines are truncated to one
  row with '...' rather than wrapped. This preserves fixed-layout templates
  where each format entry must occupy exactly one row.
  """
  cols = model.cols
  result: list[str] = []
  for line in lines:
    if display_len(line) <= cols:
      result.append(line)
      continue
    if truncation == 'ellipsis':
      result.append(truncate_line(line, cols, 'ellipsis'))
      continue
    words = line.split(' ')
    current: list[str] = []
    current_len = 0
    for word in words:
      word_len = display_len(word)
      if word_len > cols:
        # Word alone won't fit: flush current row, then truncate the word.
        if current:
          result.append(' '.join(current))
          current = []
          current_len = 0
        result.append(truncate_line(word, cols, truncation))
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


class DuplicateContentError(Exception):
  """Raised when set_state() POSTs the same content already on the board (HTTP 409)."""


class EmptyBoardError(Exception):
  """Raised when get_state() finds no message on a fresh board (HTTP 404)."""


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
  r = requests.post(_HOST, json=grid, headers=_get_headers(), timeout=10)
  if r.status_code == 409:
    raise DuplicateContentError('board already shows this content')
  if r.status_code == 423:
    raise BoardLockedError('board is locked (rate-limited or quiet hours)')
  try:
    r.raise_for_status()
  except requests.HTTPError as e:
    raise requests.HTTPError(f'Vestaboard API error: {e.response.status_code} {e.response.reason}') from None
