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
