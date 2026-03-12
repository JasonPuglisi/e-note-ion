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


def format_episode_ref(season: int, episode: int) -> str:
  """Return a compact episode ref, e.g. S9E8 (no zero-padding)."""
  return f'S{season}E{episode}'
