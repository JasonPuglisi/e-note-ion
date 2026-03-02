# integrations/color.py
#
# Shared utility: derive the dominant color from image bytes and map it to the
# nearest Vestaboard color square tag.
#
# Used by: integrations/discogs.py (album art), and future integrations such as
# Apple Music now-playing (#22).
#
# Color extraction approach:
#   1. Decode the image with Pillow and convert to RGB.
#   2. Filter out near-white (all channels > 230) and near-black (all channels
#      < 25) pixels — these are background/border artifacts that skew the result.
#   3. Compute the arithmetic mean of the remaining pixels in RGB space.
#   4. Check HSV saturation of the average color. If below _SATURATION_THRESHOLD
#      the image is achromatic (grey/B&W); map directly to [W] or [K] by
#      luminance rather than running palette matching (which would otherwise
#      spuriously return [V] for mid-grey).
#   5. For chromatic colors, find the nearest palette entry by Euclidean
#      distance in RGB space and return its tag.
#
# If the image cannot be decoded, the request fails, or all pixels are filtered,
# the caller-supplied fallback tag is returned instead.

import io
import logging

import requests
from PIL import Image

from integrations.http import fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

# (R, G, B, tag) for the 8 Vestaboard color squares.
# Palette values are approximate midpoints of each color's visual range.
_PALETTE: list[tuple[int, int, int, str]] = [
  (190, 30, 45, '[R]'),  # red
  (220, 120, 30, '[O]'),  # orange
  (220, 185, 30, '[Y]'),  # yellow
  (30, 140, 60, '[G]'),  # green
  (30, 80, 185, '[B]'),  # blue
  (110, 40, 160, '[V]'),  # violet
  (220, 220, 220, '[W]'),  # white
  (30, 30, 30, '[K]'),  # black
]

# Maximum image size to read (bytes). Cover art thumbnails are well under 500 KB;
# this guards against unexpectedly large redirect targets.
_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB

# Pixel brightness thresholds for background filtering.
_NEAR_WHITE = 230  # all channels above this → skip
_NEAR_BLACK = 25  # all channels below this → skip

# HSV saturation below this threshold → treat as achromatic (grey/B&W).
_SATURATION_THRESHOLD = 0.15

# Known Discogs placeholder image indicators.
_PLACEHOLDER_SUFFIXES = ('spacer.gif', 'placeholder.gif')


def dominant_color_tag(image_bytes: bytes, *, fallback: str = '[Y]') -> str:
  """Return the Vestaboard color tag nearest to the dominant color in the image.

  Args:
    image_bytes: Raw image bytes (JPEG, PNG, etc.).
    fallback:    Tag to return when extraction fails or all pixels are filtered.

  Returns:
    A color tag string like '[R]', '[B]', etc.
  """
  try:
    img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
  except Exception as e:
    logger.debug('color: image decode failed — %s', e)
    return fallback

  raw = img.tobytes()
  # RGB: 3 bytes per pixel; tobytes() always returns int values.
  pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]

  # Filter near-white and near-black pixels.
  filtered = [
    (r, g, b)
    for r, g, b in pixels
    if not (r > _NEAR_WHITE and g > _NEAR_WHITE and b > _NEAR_WHITE)
    and not (r < _NEAR_BLACK and g < _NEAR_BLACK and b < _NEAR_BLACK)
  ]

  if not filtered:
    logger.debug('color: all pixels filtered (near-white/black); using fallback %s', fallback)
    return fallback

  n = len(filtered)
  avg_r = sum(r for r, _, _ in filtered) // n
  avg_g = sum(g for _, g, _ in filtered) // n
  avg_b = sum(b for _, _, b in filtered) // n

  # Compute HSV saturation to detect achromatic (grey/B&W) images.
  max_c = max(avg_r, avg_g, avg_b)
  min_c = min(avg_r, avg_g, avg_b)
  saturation = (max_c - min_c) / max_c if max_c > 0 else 0.0

  if saturation < _SATURATION_THRESHOLD:
    # Achromatic: choose [W] or [K] by perceived luminance (ITU-R BT.601).
    luminance = (avg_r * 299 + avg_g * 587 + avg_b * 114) // 1000
    tag = '[W]' if luminance >= 128 else '[K]'
  else:
    tag = min(
      _PALETTE,
      key=lambda entry: (entry[0] - avg_r) ** 2 + (entry[1] - avg_g) ** 2 + (entry[2] - avg_b) ** 2,
    )[3]

  logger.debug('color: avg RGB (%d, %d, %d) sat=%.2f → %s', avg_r, avg_g, avg_b, saturation, tag)
  return tag


def fetch_cover_color(url: str, *, fallback: str = '[Y]') -> str:
  """Fetch an image from *url* and return its dominant Vestaboard color tag.

  Detects Discogs placeholder images and returns *fallback* immediately.
  Caps the response body at _MAX_IMAGE_BYTES and applies a 5 s timeout.
  Returns *fallback* on any network or decode error.

  Args:
    url:      HTTP(S) URL of the cover art image.
    fallback: Tag to return on failure or placeholder detection.

  Returns:
    A color tag string like '[R]', '[B]', etc.
  """
  # Detect placeholder URLs before making a request.
  lower_path = url.lower().split('?')[0]
  if any(lower_path.endswith(suffix) for suffix in _PLACEHOLDER_SUFFIXES):
    logger.debug('color: placeholder URL detected, skipping (%s)', url)
    return fallback

  # Also skip inline data URIs.
  if url.startswith('data:'):
    logger.debug('color: data URI skipped')
    return fallback

  try:
    r = fetch_with_retry(
      'GET',
      url,
      headers={'User-Agent': user_agent()},
      timeout=5,
      stream=True,
    )
    r.raise_for_status()

    # Guard against GIF placeholders served from non-placeholder URLs.
    content_type = r.headers.get('Content-Type', '')
    if 'image/gif' in content_type:
      logger.debug('color: GIF response skipped (likely placeholder)')
      return fallback

    image_bytes = r.raw.read(_MAX_IMAGE_BYTES)
  except requests.RequestException as e:
    logger.debug('color: image fetch failed — %s', e)
    return fallback

  tag = dominant_color_tag(image_bytes, fallback=fallback)

  # [K] (black) is invisible on a black board (the default). Substitute [W]
  # so the color square is always visible. When board_color config is added
  # (#287), read it here and skip this substitution for white boards (where
  # [W] should instead be swapped to [K]).
  if tag == '[K]':
    logger.debug('color: substituting [K] → [W] for black board visibility')
    tag = '[W]'

  return tag
