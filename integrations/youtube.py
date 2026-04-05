# integrations/youtube.py
#
# YouTube integration — live streams from subscriptions.
#
# Polls YouTube RSS feeds for each subscribed channel and checks for active
# live streams via the YouTube Data API v3 videos.list endpoint. Shows the
# most recently started live stream on the display.
#
# Authentication is handled by integrations/google.py (shared Google OAuth
# device code flow). Credentials live in the [google] section of config.toml.

import logging
import time
import xml.etree.ElementTree as ET  # noqa: N817  # nosec B405 — trusted YouTube RSS feeds, not arbitrary XML
from datetime import datetime

import requests

import integrations.google as _google
import integrations.media as _media
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry, fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

_YOUTUBE_API_BASE = 'https://www.googleapis.com/youtube/v3'
_YOUTUBE_RSS_BASE = 'https://www.youtube.com/feeds/videos.xml'
_YOUTUBE_SCOPE = 'https://www.googleapis.com/auth/youtube.readonly'

# Atom namespace used in YouTube RSS feeds.
_ATOM_NS = '{http://www.w3.org/2005/Atom}'
_YT_NS = '{http://www.youtube.com/xml/schemas/2015}'

# Subscription cache — channel IDs rarely change, so cache aggressively.
_sub_cache: CacheEntry | None = None
_SUB_CACHE_TTL = 21600  # 6 hours

# Variables cache for transient API failures during refresh cycles.
_vars_cache: CacheEntry | None = None
_VARS_CACHE_TTL = 3600  # 1 hour

# YouTube RSS returns 404 under rate limiting — retry alongside the default 5xx.
_RSS_RETRY_STATUSES = frozenset({404})


def preflight() -> None:
  """Called at startup. Delegates to the shared Google auth preflight."""
  _google.preflight(_YOUTUBE_SCOPE)


# --- Subscription fetching ---


def _fetch_subscriptions(token: str) -> list[str]:
  """Fetch all subscribed channel IDs via the YouTube Data API.

  Paginates through subscriptions.list (50 per page, 1 quota unit each).
  Returns a list of channel ID strings.
  """
  channel_ids: list[str] = []
  page_token: str | None = None

  while True:
    params: dict[str, str | int] = {
      'part': 'snippet',
      'mine': 'true',
      'maxResults': 50,
    }
    if page_token:
      params['pageToken'] = page_token

    r = fetch_with_retry(
      'GET',
      f'{_YOUTUBE_API_BASE}/subscriptions',
      params=params,
      headers={
        'Authorization': f'Bearer {token}',
        'User-Agent': user_agent(),
      },
      timeout=10,
    )
    if r.status_code == 401:
      raise requests.HTTPError('YouTube API returned 401', response=r)
    r.raise_for_status()

    data = r.json()
    for item in data.get('items', []):
      channel_id = item.get('snippet', {}).get('resourceId', {}).get('channelId')
      if channel_id:
        channel_ids.append(channel_id)

    page_token = data.get('nextPageToken')
    if not page_token:
      break

  logger.debug('YouTube: fetched %d subscriptions', len(channel_ids))
  return channel_ids


def _get_subscriptions() -> list[str]:
  """Return cached subscription channel IDs, refreshing if stale."""
  global _sub_cache

  if _sub_cache is not None and _sub_cache.is_valid(_SUB_CACHE_TTL):
    logger.debug('YouTube: subscription cache hit (%d channels)', len(_sub_cache.value.get('ids', [[]])[0]))
    return _sub_cache.value.get('ids', [[]])[0]

  token = _google.get_token(_YOUTUBE_SCOPE)
  try:
    channel_ids = _fetch_subscriptions(token)
  except requests.RequestException as e:
    if _sub_cache is not None:
      logger.warning('YouTube: subscription fetch failed (%s) — using stale cache', e)
      return _sub_cache.value.get('ids', [[]])[0]
    raise IntegrationDataUnavailableError(f'YouTube: subscription fetch failed — {e}') from None

  # Store in CacheEntry format for consistency; value is a dict but we only
  # use the 'ids' key internally.
  _sub_cache = CacheEntry({'ids': [channel_ids]})
  return channel_ids


# --- RSS feed polling ---


def _fetch_rss_video_ids(channel_ids: list[str]) -> list[str]:
  """Fetch recent video IDs from YouTube RSS feeds for the given channels.

  Each channel has a public Atom feed at /feeds/videos.xml?channel_id=...
  Returns a deduplicated list of video IDs from recent entries.

  YouTube rate-limits rapid RSS requests, returning 404 or 500 for bursts.
  We pass retry_on={404} to fetch_with_retry so both 404 and 5xx are retried
  with backoff, and add a brief inter-channel delay to avoid triggering limits.
  """
  video_ids: list[str] = []
  seen: set[str] = set()

  for i, channel_id in enumerate(channel_ids):
    if i > 0:
      time.sleep(0.25)

    try:
      r = fetch_with_retry(
        'GET',
        _YOUTUBE_RSS_BASE,
        params={'channel_id': channel_id},
        headers={'User-Agent': user_agent()},
        timeout=10,
        retry_on=_RSS_RETRY_STATUSES,
      )
      if r.status_code != 200:
        logger.debug('YouTube: RSS feed for %s returned %d — skipping', channel_id, r.status_code)
        continue
    except requests.RequestException as e:
      logger.debug('YouTube: RSS fetch failed for %s — %s', channel_id, e)
      continue

    try:
      root = ET.fromstring(r.text)  # nosec B314 — YouTube RSS is trusted
    except ET.ParseError:
      logger.debug('YouTube: malformed RSS for %s — skipping', channel_id)
      continue

    for entry in root.findall(f'{_ATOM_NS}entry'):
      vid_el = entry.find(f'{_YT_NS}videoId')
      if vid_el is not None and vid_el.text and vid_el.text not in seen:
        seen.add(vid_el.text)
        video_ids.append(vid_el.text)

  logger.debug('YouTube: found %d video IDs from RSS feeds', len(video_ids))
  return video_ids


# --- Live stream detection ---


def _check_live_videos(token: str, video_ids: list[str]) -> list[dict]:
  """Check which video IDs are currently live via videos.list.

  Batches up to 50 IDs per API call (1 quota unit each). Returns a list of
  dicts with keys: video_id, channel, title, started_at (ISO string).
  """
  live_streams: list[dict] = []

  for i in range(0, len(video_ids), 50):
    batch = video_ids[i : i + 50]
    r = fetch_with_retry(
      'GET',
      f'{_YOUTUBE_API_BASE}/videos',
      params={
        'part': 'snippet,liveStreamingDetails',
        'id': ','.join(batch),
      },
      headers={
        'Authorization': f'Bearer {token}',
        'User-Agent': user_agent(),
      },
      timeout=10,
    )
    if r.status_code == 401:
      raise requests.HTTPError('YouTube API returned 401', response=r)
    r.raise_for_status()

    for item in r.json().get('items', []):
      live_details = item.get('liveStreamingDetails', {})
      snippet = item.get('snippet', {})

      # Currently live: has actualStartTime but no actualEndTime.
      if live_details.get('actualStartTime') and not live_details.get('actualEndTime'):
        live_streams.append(
          {
            'video_id': item['id'],
            'channel': snippet.get('channelTitle', ''),
            'title': snippet.get('title', ''),
            'started_at': live_details['actualStartTime'],
          }
        )

  return live_streams


# --- Integration entry point ---


def get_variables() -> dict[str, list[list[str]]]:
  """Fetch live streams from YouTube subscriptions.

  Returns variables: channel (channel name), title (stream title).
  Shows the most recently started live stream. Raises
  IntegrationDataUnavailableError when nothing is live.
  """
  global _vars_cache

  channel_ids = _get_subscriptions()
  if not channel_ids:
    raise IntegrationDataUnavailableError('YouTube: no subscriptions found', expected=True)

  video_ids = _fetch_rss_video_ids(channel_ids)
  if not video_ids:
    raise IntegrationDataUnavailableError('YouTube: no recent videos in RSS feeds', expected=True)

  token = _google.get_token(_YOUTUBE_SCOPE)
  try:
    live_streams = _check_live_videos(token, video_ids)
  except requests.RequestException as e:
    if _vars_cache is not None and _vars_cache.is_valid(_VARS_CACHE_TTL):
      logger.warning('YouTube: videos.list failed (%s) — using cached result', e)
      return _vars_cache.value
    raise IntegrationDataUnavailableError(f'YouTube: videos.list failed — {e}') from None

  if not live_streams:
    raise IntegrationDataUnavailableError('YouTube: no subscribed channels are live', expected=True)

  # Sort by start time descending — most recently started stream first.
  live_streams.sort(
    key=lambda s: datetime.fromisoformat(s['started_at'].replace('Z', '+00:00')),
    reverse=True,
  )
  stream = live_streams[0]

  channel_name = _media.strip_leading_article(stream['channel'].upper())
  title = _media.strip_leading_article(stream['title'].upper())

  result: dict[str, list[list[str]]] = {
    'channel': [[channel_name]],
    'title': [[title]],
  }

  logger.debug('YouTube: live — %s: %s (started %s)', channel_name, title, stream['started_at'])
  _vars_cache = CacheEntry(result)
  return result
