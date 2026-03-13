# integrations/color.py
#
# Shared utility: derive the dominant color from image bytes and map it to the
# nearest Vestaboard color square tag.
#
# Used by: integrations/discogs.py (album art), and future music integrations.
#
# Color extraction approach:
#   1. Decode the image with Pillow, convert to RGB, and resize to at most
#      _SAMPLE_SIZE×_SAMPLE_SIZE pixels (preserving aspect ratio) for fast
#      clustering.
#   2. Filter out near-white (all channels > 230), near-black (all channels
#      < 25), and dark shadow pixels (BT.601 luminance < 40) — these are
#      background/border artifacts that skew the result.
#   3. Convert filtered pixels to Oklab (a perceptually uniform color space)
#      and run k-means++ clustering (k=3). Euclidean distance in Oklab is
#      perceptually uniform, which gives better cluster shapes than sRGB.
#   4. Score clusters by population × chroma (C = √(a² + b²)). Chromatic
#      clusters (C ≥ _CHROMA_THRESHOLD) are preferred; if none exist, the
#      largest achromatic cluster wins.
#   5. Convert the winning centroid to OKLCH. If chroma is below
#      _CHROMA_THRESHOLD, map to [W] or [K] by Oklab lightness. Otherwise
#      find the nearest entry in _CHROMATIC_PALETTE by circular OKLCH hue
#      distance.
#
# If the image cannot be decoded, the request fails, or all pixels are
# filtered, the caller-supplied fallback tag is returned instead.

import io
import logging
import math
import random

import requests
from PIL import Image

from integrations.http import fetch_with_retry, user_agent

logger = logging.getLogger(__name__)

# OKLCH hue angles (degrees) for the 6 chromatic Vestaboard color squares.
# Defined in Oklab/OKLCH space (perceptually uniform hue).
# Matching is by circular hue distance; [W] and [K] are handled separately.
_CHROMATIC_PALETTE: list[tuple[float, str]] = [
  (27.0, '[R]'),  # red
  (55.0, '[O]'),  # orange
  (110.0, '[Y]'),  # yellow
  (142.0, '[G]'),  # green
  (264.0, '[B]'),  # blue
  (307.0, '[V]'),  # violet
]

# Image is resized to at most this dimension on each side before clustering.
# 100×100 = 10 k pixels — enough color information, fast to cluster.
_SAMPLE_SIZE = 100

# Maximum image size to read (bytes). Cover art thumbnails are well under
# 500 KB; this guards against unexpectedly large redirect targets.
_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB

# Pixel brightness thresholds for background filtering (sRGB 0–255 scale).
_NEAR_WHITE = 230  # all channels above this → skip
_NEAR_BLACK = 25  # all channels below this → skip
_DARK_LUM_FLOOR = 40  # ITU-R BT.601 luminance below this → skip

# Oklab chroma (C = √(a² + b²)) below which a color is considered achromatic.
# Pure greys have C ≈ 0; vivid saturated colors have C ≈ 0.1–0.3.
_CHROMA_THRESHOLD = 0.05

# Oklab lightness above which achromatic colors map to [W]; below maps to [K].
# Calibrated to match the perceptual midpoint of the sRGB grey ramp (~128/255).
_WHITE_L_THRESHOLD = 0.595

# k-means parameters.
_KMEANS_K = 3
_KMEANS_MAX_ITER = 20

# Known Discogs placeholder image indicators.
_PLACEHOLDER_SUFFIXES = ('spacer.gif', 'placeholder.gif')


def _srgb_to_linear(c: int) -> float:
  """Convert a single sRGB channel value (0–255) to linear light (0–1)."""
  f = c / 255.0
  if f <= 0.04045:
    return f / 12.92
  return ((f + 0.055) / 1.055) ** 2.4


def _rgb_to_oklab(r: int, g: int, b: int) -> tuple[float, float, float]:
  """Convert sRGB (0–255) to Oklab (L, a, b).

  Uses the exact matrix coefficients from Björn Ottosson's Oklab spec.
  Euclidean distance in this space is perceptually uniform.
  """
  r_lin = _srgb_to_linear(r)
  g_lin = _srgb_to_linear(g)
  b_lin = _srgb_to_linear(b)

  lms_l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
  lms_m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
  lms_s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

  l_ = math.cbrt(lms_l)
  m_ = math.cbrt(lms_m)
  s_ = math.cbrt(lms_s)

  L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
  a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
  b_out = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_

  return L, a, b_out


def _kmeans_dominant(pixels: list[tuple[float, float, float]]) -> tuple[float, float, float]:
  """Return the centroid of the best-scoring k-means cluster in Oklab space.

  Uses k-means++ initialization then iterates until convergence or
  _KMEANS_MAX_ITER. Scores clusters by population × chroma; chromatic
  clusters (C ≥ _CHROMA_THRESHOLD) are preferred over achromatic ones.
  Falls back to a simple average when there are too few distinct pixels.
  """
  k = _KMEANS_K

  if len(pixels) <= k:
    n = len(pixels)
    return (
      sum(p[0] for p in pixels) / n,
      sum(p[1] for p in pixels) / n,
      sum(p[2] for p in pixels) / n,
    )

  # k-means++ initialization: spread starting centroids across the color space.
  first = random.choice(pixels)  # nosec S311
  centroids: list[tuple[float, float, float]] = [first]

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
        centroids.append(p)
        break

  # Pad with duplicates if initialization produced fewer than k centroids.
  while len(centroids) < k:
    centroids.append(centroids[-1])

  # Iterate: assign → update → check convergence.
  assignments: list[list[tuple[float, float, float]]] = [[] for _ in range(k)]
  for _ in range(_KMEANS_MAX_ITER):
    new_assignments: list[list[tuple[float, float, float]]] = [[] for _ in range(k)]
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

  # Score clusters: prefer chromatic (C ≥ threshold) by population × chroma.
  # If no chromatic cluster exists, fall back to the largest cluster.
  def _chroma(c: tuple[float, float, float]) -> float:
    return math.sqrt(c[1] ** 2 + c[2] ** 2)

  chromatic = [
    (len(assignments[i]), _chroma(centroids[i]), centroids[i])
    for i in range(k)
    if assignments[i] and _chroma(centroids[i]) >= _CHROMA_THRESHOLD
  ]

  if chromatic:
    return max(chromatic, key=lambda t: t[0] * t[1])[2]

  # No chromatic cluster — return the largest achromatic centroid.
  largest = max(range(k), key=lambda i: len(assignments[i]))
  return centroids[largest]


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
  # RGB: 3 bytes per pixel.
  srgb_pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]

  # Filter near-white, near-black, and dark shadow pixels.
  # The luminance floor catches dark pixels (e.g. face shadows on a black cover)
  # that pass the per-channel near-black check but are visually indistinguishable
  # from black and skew hue detection with their slight warm undertones.
  filtered = [
    (r, g, b)
    for r, g, b in srgb_pixels
    if not (r > _NEAR_WHITE and g > _NEAR_WHITE and b > _NEAR_WHITE)
    and not (r < _NEAR_BLACK and g < _NEAR_BLACK and b < _NEAR_BLACK)
    and (r * 299 + g * 587 + b * 114) // 1000 >= _DARK_LUM_FLOOR
  ]

  if not filtered:
    logger.debug('color: all pixels filtered (near-white/black); using fallback %s', fallback)
    return fallback

  # Convert filtered pixels to Oklab for perceptually-uniform clustering.
  oklab_pixels = [_rgb_to_oklab(r, g, b) for r, g, b in filtered]

  # Find dominant color via k-means in Oklab, scored by population × chroma.
  L, a, b_val = _kmeans_dominant(oklab_pixels)

  # Compute OKLCH chroma to decide chromatic vs achromatic.
  chroma = math.sqrt(a**2 + b_val**2)

  if chroma < _CHROMA_THRESHOLD:
    # Achromatic: choose [W] or [K] by Oklab lightness.
    tag = '[W]' if L >= _WHITE_L_THRESHOLD else '[K]'
    logger.debug('color: Oklab L=%.3f C=%.3f → achromatic %s', L, chroma, tag)
  else:
    # Chromatic: compute OKLCH hue angle and find nearest palette entry.
    hue = math.degrees(math.atan2(b_val, a)) % 360
    tag = min(
      _CHROMATIC_PALETTE,
      key=lambda entry: min(abs(entry[0] - hue), 360 - abs(entry[0] - hue)),
    )[1]
    logger.debug('color: Oklab L=%.3f C=%.3f hue=%.1f° → %s', L, chroma, hue, tag)

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
