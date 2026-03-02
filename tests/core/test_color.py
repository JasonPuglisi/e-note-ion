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
