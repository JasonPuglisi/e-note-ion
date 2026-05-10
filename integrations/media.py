# integrations/media.py
#
# Shared media title utilities used by Plex and Trakt integrations.

_LEADING_ARTICLES = ('THE ', 'AN ', 'A ')


def strip_leading_article(title: str) -> str:
  """Remove a leading article (A, An, The) from an uppercased title."""
  for article in _LEADING_ARTICLES:
    if title.startswith(article):
      return title[len(article) :]
  return title


def strip_leading_article_if_needed(title: str, max_width: int, prefix: str = '') -> str:
  """Strip a leading article only when prefix+title exceeds max_width."""
  if len(prefix + title) <= max_width:
    return title
  return strip_leading_article(title)


def format_episode_ref(season: int, episode: int) -> str:
  """Return a compact episode ref, e.g. S9E8 (no zero-padding)."""
  return f'S{season}E{episode}'


def wrap_title_to_rows(title: str, cols: int, max_rows: int) -> list[str]:
  """Word-wrap a title to at most max_rows rows of cols width.

  A single word longer than cols is hard-cut to one row and its remainder
  dropped. If the wrapped result exceeds max_rows, the last kept row is
  hard-truncated to cols-3 and '...' is appended.
  """
  if len(title) <= cols:
    return [title]
  rows: list[str] = []
  current: list[str] = []
  current_len = 0
  for word in title.split(' '):
    if len(word) > cols:
      if current:
        rows.append(' '.join(current))
        current = []
        current_len = 0
      rows.append(word[:cols])
      continue
    if not current:
      current = [word]
      current_len = len(word)
    elif current_len + 1 + len(word) <= cols:
      current.append(word)
      current_len += 1 + len(word)
    else:
      rows.append(' '.join(current))
      current = [word]
      current_len = len(word)
  if current:
    rows.append(' '.join(current))
  if len(rows) > max_rows:
    rows = rows[:max_rows]
    rows[-1] = rows[-1][: cols - 3].rstrip() + '...'
  return rows
