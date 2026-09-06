import io
from unittest.mock import MagicMock, patch

import requests
from PIL import Image

import integrations.color as color_mod


def _png_bytes(r: int, g: int, b: int, size: int = 1) -> bytes:
  """Return raw PNG bytes for a solid-color image of the given size."""
  img = Image.new('RGB', (size, size), color=(r, g, b))
  buf = io.BytesIO()
  img.save(buf, format='PNG')
  return buf.getvalue()


def _png_bytes_regions(
  dominant: tuple[int, int, int],
  accent: tuple[int, int, int],
  dominant_count: int = 8,
  accent_count: int = 2,
) -> bytes:
  """Return PNG bytes with a dominant color region and a smaller accent region."""
  total = dominant_count + accent_count
  img = Image.new('RGB', (total, 1))
  for x in range(dominant_count):
    img.putpixel((x, 0), dominant)
  for x in range(dominant_count, total):
    img.putpixel((x, 0), accent)
  buf = io.BytesIO()
  img.save(buf, format='PNG')
  return buf.getvalue()


# --- dominant_color_tag ---


def test_dominant_color_tag_pure_red() -> None:
  tag = color_mod.dominant_color_tag(_png_bytes(200, 20, 30))
  assert tag == '[R]'


def test_dominant_color_tag_pure_blue() -> None:
  tag = color_mod.dominant_color_tag(_png_bytes(20, 60, 200))
  assert tag == '[B]'


def test_dominant_color_tag_pure_green() -> None:
  tag = color_mod.dominant_color_tag(_png_bytes(20, 150, 40))
  assert tag == '[G]'


def test_dominant_color_tag_fallback_on_bad_bytes() -> None:
  tag = color_mod.dominant_color_tag(b'not an image')
  assert tag == '[Y]'


def test_dominant_color_tag_custom_fallback_on_bad_bytes() -> None:
  tag = color_mod.dominant_color_tag(b'not an image', fallback='[R]')
  assert tag == '[R]'


def test_dominant_color_tag_fallback_when_all_pixels_near_white() -> None:
  # Pure white image — all pixels filtered out.
  tag = color_mod.dominant_color_tag(_png_bytes(255, 255, 255))
  assert tag == '[Y]'


def test_dominant_color_tag_fallback_when_all_pixels_near_black() -> None:
  # Pure black image — all pixels filtered out.
  tag = color_mod.dominant_color_tag(_png_bytes(0, 0, 0))
  assert tag == '[Y]'


def test_dominant_color_tag_returns_string() -> None:
  tag = color_mod.dominant_color_tag(_png_bytes(200, 20, 30))
  assert isinstance(tag, str)
  assert tag.startswith('[') and tag.endswith(']')


def test_dominant_color_tag_grey_maps_to_white() -> None:
  # Mid-grey is achromatic with luminance >= 128 → [W], not [V].
  tag = color_mod.dominant_color_tag(_png_bytes(128, 128, 128))
  assert tag == '[W]'


def test_dominant_color_tag_dark_grey_maps_to_black() -> None:
  # Dark grey (passes near-black filter at 25) is achromatic with low luminance → [K].
  tag = color_mod.dominant_color_tag(_png_bytes(60, 60, 60))
  assert tag == '[K]'


def test_dominant_color_tag_dark_warm_shadow_maps_to_achromatic() -> None:
  # Dark shadow pixels (e.g. face on a black album cover): lum ~36, passes per-channel
  # near-black filter but is visually black. Slight warm R>G>B bias must not map to [O].
  # These are filtered by the luminance floor — fallback [Y] is returned.
  tag = color_mod.dominant_color_tag(_png_bytes(40, 35, 32))
  assert tag == '[Y]'  # all pixels filtered → fallback


def test_dominant_color_tag_bw_image_maps_to_white_or_black() -> None:
  # A B&W image (black silhouette on white bg): black pixels filtered,
  # white pixels filtered, grey midtones (if any) map achromatic.
  # Use a known mid-grey to confirm achromatic path, not [V].
  tag = color_mod.dominant_color_tag(_png_bytes(150, 150, 150))
  assert tag in ('[W]', '[K]')
  assert tag != '[V]'


def test_dominant_color_tag_light_blue_maps_to_blue() -> None:
  # Pale/light blue — this was the original bug: Euclidean RGB matched [W]
  # because pale blue is closer to (220,220,220) than to the dark navy (30,80,185).
  # Hue-based matching puts it at ~220° → [B] at 240°.
  tag = color_mod.dominant_color_tag(_png_bytes(150, 170, 210))
  assert tag == '[B]'


def test_dominant_color_tag_light_red_maps_to_red() -> None:
  tag = color_mod.dominant_color_tag(_png_bytes(220, 120, 120))
  assert tag == '[R]'


def test_dominant_color_tag_light_green_maps_to_green() -> None:
  tag = color_mod.dominant_color_tag(_png_bytes(120, 200, 130))
  assert tag == '[G]'


def test_dominant_color_tag_kmeans_picks_dominant_region() -> None:
  # Blue dominant region (8px) + red accent (2px) → should resolve to [B],
  # not [R] or some blend. This validates that k-means identifies the largest
  # cluster rather than averaging all pixels together.
  png = _png_bytes_regions(dominant=(30, 80, 200), accent=(200, 20, 30))
  tag = color_mod.dominant_color_tag(png)
  assert tag == '[B]'


def test_dominant_color_tag_kmeans_picks_dominant_over_skin_tone() -> None:
  # Blue background (8px) + skin tone accent (2px) → [B], not [W]/[Y].
  # Simulates covers like IM NAYEON where averaging would wash out to grey.
  png = _png_bytes_regions(dominant=(60, 100, 180), accent=(210, 160, 120))
  tag = color_mod.dominant_color_tag(png)
  assert tag == '[B]'


def test_dominant_color_tag_chroma_score_vivid_minority_beats_washed_majority() -> None:
  # Small vivid cluster should beat large washed-out cluster when scored by
  # population × chroma. Grey (8px) has near-zero chroma; vivid red (2px) has
  # high chroma — red's score wins even though grey dominates by count.
  png = _png_bytes_regions(dominant=(150, 150, 150), accent=(200, 20, 30))
  tag = color_mod.dominant_color_tag(png)
  assert tag == '[R]'


def test_dominant_color_tag_warm_neutral_maps_to_achromatic() -> None:
  # Warm cream/sepia tones have very low Oklab chroma despite non-zero HSV
  # saturation. They should map to [W] (achromatic), not [O] or [Y].
  tag = color_mod.dominant_color_tag(_png_bytes(200, 180, 160))
  assert tag in ('[W]', '[K]')
  assert tag not in ('[O]', '[Y]')


# --- hex_to_color_tag ---


def test_hex_to_color_tag_red() -> None:
  assert color_mod.hex_to_color_tag('#FF2D30FF') == '[R]'


def test_hex_to_color_tag_blue() -> None:
  assert color_mod.hex_to_color_tag('#007AFF') == '[B]'


def test_hex_to_color_tag_green() -> None:
  assert color_mod.hex_to_color_tag('#34C759FF') == '[G]'


def test_hex_to_color_tag_white() -> None:
  assert color_mod.hex_to_color_tag('#FFFFFFFF') == '[W]'


def test_hex_to_color_tag_black() -> None:
  assert color_mod.hex_to_color_tag('#000000FF') == '[K]'


def test_hex_to_color_tag_strips_alpha() -> None:
  assert color_mod.hex_to_color_tag('#52C755FF') == color_mod.hex_to_color_tag('#52C755')


def test_hex_to_color_tag_apple_red() -> None:
  """Apple's default red #FF2D55FF maps to [R]."""
  assert color_mod.hex_to_color_tag('#FF2D55FF') == '[R]'


# --- fetch_cover_color ---


def _mock_response(image_bytes: bytes, content_type: str = 'image/jpeg') -> MagicMock:
  mock = MagicMock()
  mock.raise_for_status.return_value = None
  mock.headers = {'Content-Type': content_type}
  mock.raw.read.return_value = image_bytes
  mock.status_code = 200
  return mock


def test_fetch_cover_color_returns_tag_on_success() -> None:
  png = _png_bytes(200, 20, 30)
  with patch('integrations.color.fetch_with_retry', return_value=_mock_response(png)):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg')
  assert tag == '[R]'


def test_fetch_cover_color_fallback_on_timeout() -> None:
  with patch('integrations.color.fetch_with_retry', side_effect=requests.Timeout()):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg')
  assert tag == '[Y]'


def test_fetch_cover_color_fallback_on_connection_error() -> None:
  with patch('integrations.color.fetch_with_retry', side_effect=requests.ConnectionError()):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg')
  assert tag == '[Y]'


def test_fetch_cover_color_fallback_on_http_error() -> None:
  mock = MagicMock()
  mock.raise_for_status.side_effect = requests.HTTPError(response=MagicMock(status_code=404))
  with patch('integrations.color.fetch_with_retry', return_value=mock):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg')
  assert tag == '[Y]'


def test_fetch_cover_color_skips_spacer_gif_url() -> None:
  with patch('integrations.color.fetch_with_retry') as mock_fetch:
    tag = color_mod.fetch_cover_color('https://st.discogs.com/abc/spacer.gif')
  mock_fetch.assert_not_called()
  assert tag == '[Y]'


def test_fetch_cover_color_skips_placeholder_gif_url() -> None:
  with patch('integrations.color.fetch_with_retry') as mock_fetch:
    tag = color_mod.fetch_cover_color('https://example.com/placeholder.gif')
  mock_fetch.assert_not_called()
  assert tag == '[Y]'


def test_fetch_cover_color_skips_data_uri() -> None:
  with patch('integrations.color.fetch_with_retry') as mock_fetch:
    tag = color_mod.fetch_cover_color('data:image/gif;base64,R0lGODlh')
  mock_fetch.assert_not_called()
  assert tag == '[Y]'


def test_fetch_cover_color_skips_gif_content_type() -> None:
  mock = _mock_response(b'GIF89a', content_type='image/gif')
  with patch('integrations.color.fetch_with_retry', return_value=mock):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg')
  assert tag == '[Y]'


def test_fetch_cover_color_custom_fallback() -> None:
  with patch('integrations.color.fetch_with_retry', side_effect=requests.Timeout()):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg', fallback='[G]')
  assert tag == '[G]'


def test_fetch_cover_color_substitutes_black_with_white() -> None:
  # [K] returned by dominant_color_tag must be swapped to [W] for black board visibility.
  png = _png_bytes(60, 60, 60)  # dark grey → dominant_color_tag returns [K]
  with patch('integrations.color.fetch_with_retry', return_value=_mock_response(png)):
    tag = color_mod.fetch_cover_color('https://example.com/cover.jpg')
  assert tag == '[W]'


# --- decompression bomb bound (#594) ---


def test_oversized_image_returns_fallback_instead_of_decoding() -> None:
  """The 2 MB byte cap bounds the download, not the decode.

  A highly compressed PNG can sit under the byte cap and still expand past
  Pillow's ~89 Mpx default, around 268 MB of RGB. The image is thumbnailed to
  _SAMPLE_SIZE immediately, so no real resolution is ever needed.
  """
  from PIL import Image

  # A single-colour image of this size compresses to a few KB but declares far
  # more pixels than the limit allows.
  side = int(color_mod._MAX_IMAGE_PIXELS**0.5) + 500
  buf = io.BytesIO()
  Image.new('RGB', (side, side), (200, 20, 30)).save(buf, format='PNG')
  payload = buf.getvalue()

  assert len(payload) < color_mod._MAX_IMAGE_BYTES, 'test image must pass the byte cap to be meaningful'

  assert color_mod.dominant_color_tag(payload, fallback='[Y]') == '[Y]'


def test_normal_image_still_decodes_under_the_pixel_limit() -> None:
  assert color_mod.dominant_color_tag(_png_bytes(200, 20, 30)) == '[R]'


def test_pillow_global_limit_is_left_alone() -> None:
  """The bound is an explicit header check, not a mutation of Pillow's global."""
  from PIL import Image

  before = Image.MAX_IMAGE_PIXELS
  color_mod.dominant_color_tag(_png_bytes(10, 20, 30))
  color_mod.dominant_color_tag(b'not an image')
  assert Image.MAX_IMAGE_PIXELS == before


def test_oversized_image_is_rejected_before_the_decode() -> None:
  """Rejection must happen on the header, not after materialising the raster."""
  from PIL import Image as _Image

  side = int(color_mod._MAX_IMAGE_PIXELS**0.5) + 500
  buf = io.BytesIO()
  _Image.new('RGB', (side, side), (200, 20, 30)).save(buf, format='PNG')

  with patch.object(_Image.Image, 'convert', side_effect=AssertionError('decoded an oversized image')):
    assert color_mod.dominant_color_tag(buf.getvalue(), fallback='[Y]') == '[Y]'


# --- oversized download handling (follow-up to #594) ---


def test_oversized_body_is_skipped_not_truncated() -> None:
  """An over-cap body must be recognised, not read partially.

  Reading exactly _MAX_IMAGE_BYTES silently truncated the image, which then
  failed to decode and fell back — indistinguishable from a corrupt file, with
  no signal that a size limit caused it.
  """
  oversized = b'\xff\xd8\xff' + b'x' * (color_mod._MAX_IMAGE_BYTES + 10)
  fake = MagicMock()
  fake.headers = {'Content-Type': 'image/jpeg'}
  fake.raw.read.side_effect = lambda n: oversized[:n]

  with patch('integrations.color.fetch_with_retry', return_value=fake):
    assert color_mod.fetch_cover_color('https://example.com/cover.jpg', fallback='[G]') == '[G]'

  # Read one byte past the cap, which is what makes the overage detectable.
  fake.raw.read.assert_called_once_with(color_mod._MAX_IMAGE_BYTES + 1)


def test_body_exactly_at_the_cap_is_still_processed() -> None:
  """The cap is inclusive — an image of exactly the limit is legitimate."""
  from PIL import Image as _Image

  buf = io.BytesIO()
  _Image.new('RGB', (60, 60), (200, 20, 30)).save(buf, format='PNG')
  payload = buf.getvalue()
  assert len(payload) < color_mod._MAX_IMAGE_BYTES

  fake = MagicMock()
  fake.headers = {'Content-Type': 'image/png'}
  fake.raw.read.side_effect = lambda n: payload[:n]

  with patch('integrations.color.fetch_with_retry', return_value=fake):
    assert color_mod.fetch_cover_color('https://example.com/cover.png') == '[R]'


def test_byte_cap_has_real_headroom_over_observed_cover_art() -> None:
  """Guards against re-tightening this to a size that clips real covers.

  Measured against a real Discogs collection: median 90 KB, largest 153 KB.
  """
  assert color_mod._MAX_IMAGE_BYTES >= 4 * 1024 * 1024
