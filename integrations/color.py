# integrations/color.py
#
# Shared utility: derive the dominant color from image bytes and map it to the
# nearest Vestaboard color square tag.
#
# Used by: integrations/discogs.py (album art), and future integrations such as
# Apple Music now-playing (#22).
#
# Color extraction approach:
#   1. Decode the image with Pillow, convert to RGB, and resize to at most
#      _SAMPLE_SIZE×_SAMPLE_SIZE pixels (preserving aspect ratio) for fast
#      clustering.
#   2. Filter out near-white (all channels > 230) and near-black (all channels
#      < 25) pixels — these are background/border artifacts that skew the result.
#   3. Run k-means++ clustering (k=3) on the filtered pixels. This finds the
#      dominant color region even when the image has multiple distinct hues
#      (e.g. blue background + skin tones). The centroid of the largest cluster
#      represents the dominant color.
#   4. Check HSV saturation of the dominant centroid. If below
#      _SATURATION_THRESHOLD the color is achromatic (grey/B&W); map directly
#      to [W] or [K] by luminance (ITU-R BT.601).
#   5. For chromatic colors, compute the HSV hue angle and find the nearest
#      entry in _CHROMATIC_PALETTE by circular hue distance. Hue matching is
#      invariant to lightness, so pale blue and deep navy both map to [B].
#
# If the image cannot be decoded, the request fails, or all pixels are filtered,
# the caller-supplied fallback tag is returned instead.

import io
import logging
import random

import requests
from PIL import Image

from integrations.http import fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

# (hue_degrees, tag) for the 6 chromatic Vestaboard color squares.
# Matching is by circular hue distance, so all lightness variants of a hue
# (pale blue, sky blue, deep navy) map to the same tag.
# [W] and [K] are achromatic and handled separately by luminance.
_CHROMATIC_PALETTE: list[tuple[float, str]] = [
  (0.0, '[R]'),  # red
  (30.0, '[O]'),  # orange
  (60.0, '[Y]'),  # yellow
  (120.0, '[G]'),  # green
  (240.0, '[B]'),  # blue
  (275.0, '[V]'),  # violet
]

# Image is resized to at most this dimension on each side before clustering.
# 100×100 = 10k pixels — enough color information, fast to cluster.
_SAMPLE_SIZE = 100

# Maximum image size to read (bytes). Cover art thumbnails are well under 500 KB;
# this guards against unexpectedly large redirect targets.
_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB

# Pixel brightness thresholds for background filtering.
_NEAR_WHITE = 230  # all channels above this → skip
_NEAR_BLACK = 25  # all channels below this → skip
_DARK_LUM_FLOOR = 40  # ITU-R BT.601 luminance below this → skip (dark shadows pass per-channel but read as black)

# HSV saturation below this threshold → treat as achromatic (grey/B&W).
_SATURATION_THRESHOLD = 0.15

# k-means parameters.
_KMEANS_K = 3
_KMEANS_MAX_ITER = 20

# Known Discogs placeholder image indicators.
_PLACEHOLDER_SUFFIXES = ('spacer.gif', 'placeholder.gif')


def _kmeans_dominant(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
  """Return the centroid of the largest k-means cluster (k=_KMEANS_K).

  Uses k-means++ initialization to spread starting centroids across the color
  space, then iterates until convergence or _KMEANS_MAX_ITER. Falls back to
  a simple average when there are too few distinct pixels to cluster.
  """
  k = _KMEANS_K

  if len(pixels) <= k:
    n = len(pixels)
    return (
      sum(p[0] for p in pixels) // n,
      sum(p[1] for p in pixels) // n,
      sum(p[2] for p in pixels) // n,
    )

  # k-means++ initialization: spread starting centroids across the color space.
  first = random.choice(pixels)  # nosec S311
  centroids: list[tuple[float, float, float]] = [(float(first[0]), float(first[1]), float(first[2]))]

  for _ in range(k - 1):
    dists = [min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2 for c in centroids) for p in pixels]
    total = sum(dists)
    if total == 0:
      break
    threshold = random.random() * total  # nosec S311
    cumulative = 0.0
    for p, d in zip(pixels, dists):
      cumulative += d
      if cumulative >= threshold:
        centroids.append((float(p[0]), float(p[1]), float(p[2])))
        break

  # Pad with duplicates if initialization produced fewer than k centroids
  # (can happen when all pixels are identical).
  while len(centroids) < k:
    centroids.append(centroids[-1])

  # Iterate: assign → update → check convergence.
  assignments: list[list[tuple[int, int, int]]] = [[] for _ in range(k)]
  for _ in range(_KMEANS_MAX_ITER):
    new_assignments: list[list[tuple[int, int, int]]] = [[] for _ in range(k)]
    for p in pixels:
      nearest = min(
        range(k),
        key=lambda i: (p[0] - centroids[i][0]) ** 2 + (p[1] - centroids[i][1]) ** 2 + (p[2] - centroids[i][2]) ** 2,
      )
      new_assignments[nearest].append(p)

    changed = False
    for i, cluster in enumerate(new_assignments):
      if not cluster:
        continue
      n = len(cluster)
      nc: tuple[float, float, float] = (
        sum(p[0] for p in cluster) / n,
        sum(p[1] for p in cluster) / n,
        sum(p[2] for p in cluster) / n,
      )
      if nc != centroids[i]:
        changed = True
        centroids[i] = nc

    assignments = new_assignments
    if not changed:
      break

  largest = max(range(k), key=lambda i: len(assignments[i]))
  c = centroids[largest]
  return (int(round(c[0])), int(round(c[1])), int(round(c[2])))


def dominant_color_tag(image_bytes: bytes, *, fallback: str = '[Y]') -> str:
  """Return the Vestaboard color tag for the dominant color in the image.

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

  # Resize to at most _SAMPLE_SIZE×_SAMPLE_SIZE for fast clustering.
  img.thumbnail((_SAMPLE_SIZE, _SAMPLE_SIZE), Image.Resampling.LANCZOS)

  raw = img.tobytes()
  # RGB: 3 bytes per pixel; tobytes() always returns int values.
  pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]

  # Filter near-white, near-black, and dark shadow pixels.
  # The luminance floor catches dark pixels (e.g. face shadows on a black cover)
  # that pass the per-channel near-black check but are visually indistinguishable
  # from black and skew hue detection with their slight warm undertones.
  filtered = [
    (r, g, b)
    for r, g, b in pixels
    if not (r > _NEAR_WHITE and g > _NEAR_WHITE and b > _NEAR_WHITE)
    and not (r < _NEAR_BLACK and g < _NEAR_BLACK and b < _NEAR_BLACK)
    and (r * 299 + g * 587 + b * 114) // 1000 >= _DARK_LUM_FLOOR
  ]

  if not filtered:
    logger.debug('color: all pixels filtered (near-white/black); using fallback %s', fallback)
    return fallback

  # Find dominant color via k-means clustering.
  avg_r, avg_g, avg_b = _kmeans_dominant(filtered)

  # Compute HSV saturation to detect achromatic (grey/B&W) images.
  max_c = max(avg_r, avg_g, avg_b)
  min_c = min(avg_r, avg_g, avg_b)
  saturation = (max_c - min_c) / max_c if max_c > 0 else 0.0

  if saturation < _SATURATION_THRESHOLD:
    # Achromatic: choose [W] or [K] by perceived luminance (ITU-R BT.601).
    luminance = (avg_r * 299 + avg_g * 587 + avg_b * 114) // 1000
    tag = '[W]' if luminance >= 128 else '[K]'
    logger.debug('color: avg RGB (%d,%d,%d) sat=%.2f lum=%d → %s', avg_r, avg_g, avg_b, saturation, luminance, tag)
  else:
    # Chromatic: compute HSV hue and match by circular distance.
    delta = max_c - min_c
    if max_c == avg_r:
      hue = 60.0 * (((avg_g - avg_b) / delta) % 6)
    elif max_c == avg_g:
      hue = 60.0 * ((avg_b - avg_r) / delta + 2)
    else:
      hue = 60.0 * ((avg_r - avg_g) / delta + 4)
    tag = min(
      _CHROMATIC_PALETTE,
      key=lambda entry: min(abs(entry[0] - hue), 360 - abs(entry[0] - hue)),
    )[1]
    logger.debug('color: avg RGB (%d,%d,%d) sat=%.2f hue=%.1f → %s', avg_r, avg_g, avg_b, saturation, hue, tag)

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
