import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.vestaboard as vb

# --- display_len ---


def test_display_len_plain_text() -> None:
  assert vb.display_len('HELLO') == 5


def test_display_len_empty() -> None:
  assert vb.display_len('') == 0


def test_display_len_color_tag() -> None:
  assert vb.display_len('[G]') == 1


def test_display_len_all_color_tags() -> None:
  # Each tag counts as 1; 8 tags = 8 display chars
  tags = '[R][O][Y][G][B][V][W][K]'
  assert vb.display_len(tags) == 8


def test_display_len_heart_emoji() -> None:
  assert vb.display_len('❤️') == 1


def test_display_len_mixed() -> None:
  # '[G] 5' = [G](1) + space(1) + 5(1) = 3
  assert vb.display_len('[G] 5') == 3


# --- _encode_line ---


def test_encode_line_letters() -> None:
  result = vb._encode_line('ABC')  # noqa: SLF001
  assert result[0] == 1  # A
  assert result[1] == 2  # B
  assert result[2] == 3  # C


def test_encode_line_digit() -> None:
  result = vb._encode_line('5')  # noqa: SLF001
  assert result[0] == 31  # '5' is code 31 (digits start at 27 for '1')


def test_encode_line_color_tag() -> None:
  result = vb._encode_line('[G]')  # noqa: SLF001
  assert result[0] == 66  # green


def test_encode_line_heart() -> None:
  result = vb._encode_line('❤️')  # noqa: SLF001
  assert result[0] == 62


def test_encode_line_unknown_char_is_blank() -> None:
  result = vb._encode_line('{')  # noqa: SLF001
  assert result[0] == 0


def test_encode_line_padded_to_cols() -> None:
  result = vb._encode_line('A')  # noqa: SLF001
  assert len(result) == vb.model.cols
  assert all(x == 0 for x in result[1:])


def test_encode_line_truncated_at_cols() -> None:
  # Input longer than model.cols should be truncated, not overflow
  result = vb._encode_line('A' * (vb.model.cols + 10))  # noqa: SLF001
  assert len(result) == vb.model.cols


# --- _truncate ---


def test_truncate_exact_fit_unchanged() -> None:
  text = 'A' * vb.model.cols
  assert vb._truncate(text, vb.model.cols) == text  # noqa: SLF001


def test_truncate_short_text_unchanged() -> None:
  assert vb._truncate('HI', 10) == 'HI'  # noqa: SLF001


def test_truncate_hard() -> None:
  assert vb._truncate('HELLO WORLD', 7) == 'HELLO W'  # noqa: SLF001


def test_truncate_word() -> None:
  assert vb._truncate('HELLO WORLD', 7, 'word') == 'HELLO'  # noqa: SLF001


def test_truncate_ellipsis() -> None:
  # target=7 (10-3): fits 'HELLO'(5) + space(6) + 'W'(7); base='HELLO' + '...'
  assert vb._truncate('HELLO WORLD', 10, 'ellipsis') == 'HELLO...'  # noqa: SLF001


def test_truncate_word_no_space_falls_back_to_hard() -> None:
  # No space before the limit — word strategy behaves like hard
  assert vb._truncate('HELLOWORLD', 5, 'word') == 'HELLO'  # noqa: SLF001


def test_truncate_preserves_color_tag() -> None:
  # Truncating to 1 display char should return the full [G] token, not split it
  assert vb._truncate('[G]AB', 1) == '[G]'  # noqa: SLF001


def test_truncate_preserves_heart() -> None:
  assert vb._truncate('❤️AB', 1) == '❤️'  # noqa: SLF001


# --- _wrap_lines ---


def test_wrap_lines_short_passes_through() -> None:
  assert vb._wrap_lines(['SHORT']) == ['SHORT']  # noqa: SLF001


def test_wrap_lines_wraps_long_line() -> None:
  # 'HELLO WORLD THIS IS LONG' exceeds 15 cols, should be split
  result = vb._wrap_lines(['HELLO WORLD THIS IS'])  # noqa: SLF001
  assert len(result) >= 2
  assert all(vb.display_len(r) <= vb.model.cols for r in result)


def test_wrap_lines_drops_excess_rows() -> None:
  # Six distinct words will produce many wrapped rows; only model.rows kept
  lines = ['A B C D E F G H I J K']
  result = vb._wrap_lines(lines)  # noqa: SLF001
  assert len(result) <= vb.model.rows


def test_wrap_lines_word_longer_than_cols_truncated() -> None:
  long_word = 'A' * (vb.model.cols + 5)
  result = vb._wrap_lines([long_word])  # noqa: SLF001
  assert vb.display_len(result[0]) <= vb.model.cols


def test_wrap_lines_does_not_join_separate_lines() -> None:
  # Two short lines must remain separate, not be merged
  result = vb._wrap_lines(['LINE ONE', 'LINE TWO'])  # noqa: SLF001
  assert result[0] == 'LINE ONE'
  assert result[1] == 'LINE TWO'


# --- _expand_format ---


def test_expand_format_plain_text() -> None:
  result = vb._expand_format(['HELLO', 'WORLD'], {})  # noqa: SLF001
  assert result == ['HELLO', 'WORLD']


def test_expand_format_inline_substitution() -> None:
  result = vb._expand_format(['HI {name}'], {'name': [['JASON']]})  # noqa: SLF001
  assert result == ['HI JASON']


def test_expand_format_whole_line_expansion() -> None:
  # A standalone {var} entry expands to all lines of the chosen option
  result = vb._expand_format(['{lines}'], {'lines': [['LINE 1', 'LINE 2']]})  # noqa: SLF001
  assert result == ['LINE 1', 'LINE 2']


def test_expand_format_inline_uses_first_line_of_option() -> None:
  # Inline {var} within other text uses only the first line of the option
  result = vb._expand_format(['X {v} Y'], {'v': [['A', 'B']]})  # noqa: SLF001
  assert result == ['X A Y']


def test_expand_format_missing_variable_is_blank() -> None:
  result = vb._expand_format(['{missing}'], {})  # noqa: SLF001
  assert result == ['']


# --- _build_grid ---


def test_build_grid_correct_dimensions() -> None:
  grid = vb._build_grid(['HELLO', 'WORLD', 'TEST'])  # noqa: SLF001
  assert len(grid) == vb.model.rows
  assert all(len(row) == vb.model.cols for row in grid)


def test_build_grid_blank_row_padding() -> None:
  # One input line — remaining rows should be all zeros
  grid = vb._build_grid(['HELLO'])  # noqa: SLF001
  assert len(grid) == vb.model.rows
  for row in grid[1:]:
    assert all(x == 0 for x in row)


def test_build_grid_flagship_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(vb, 'model', vb.VestaboardModel.FLAGSHIP)
  grid = vb._build_grid(['A'] * vb.VestaboardModel.FLAGSHIP.rows)  # noqa: SLF001
  assert len(grid) == vb.VestaboardModel.FLAGSHIP.rows
  assert all(len(row) == vb.VestaboardModel.FLAGSHIP.cols for row in grid)


# --- _next_token ---


def test_next_token_heart() -> None:
  assert vb._next_token('❤️', 0) == ('❤️', 2)  # noqa: SLF001


def test_next_token_color_tag() -> None:
  assert vb._next_token('[G]', 0) == ('[G]', 3)  # noqa: SLF001


def test_next_token_escaped_color_tag() -> None:
  tok, consumed = vb._next_token('[[G]]', 0)  # noqa: SLF001
  assert tok == '[[G]]'
  assert consumed == 5


def test_next_token_single_char() -> None:
  assert vb._next_token('A', 0) == ('A', 1)  # noqa: SLF001


def test_next_token_incomplete_escaped_tag_not_matched() -> None:
  # [[G] without closing ]] is NOT an escaped tag
  tok, consumed = vb._next_token('[[G]', 0)  # noqa: SLF001
  assert tok == '['  # falls through to single char
  assert consumed == 1


# --- display_len (escaped tags) ---


def test_display_len_escaped_color_tag() -> None:
  assert vb.display_len('[[G]]') == 3


def test_display_len_real_vs_escaped_tag() -> None:
  assert vb.display_len('[G]') == 1
  assert vb.display_len('[[G]]') == 3


# --- _encode_line (escaped tags) ---


def test_encode_line_escaped_color_tag_not_green() -> None:
  result = vb._encode_line('[[G]]')  # noqa: SLF001
  assert result[0] != 66  # must not be green (code 66)
  assert result[1] == 7  # 'G' is code 7
  assert result[2] == 0  # ']' is not in char map → blank
  assert len(result) == vb.model.cols


# --- _truncate (escaped tags) ---


def test_truncate_does_not_split_escaped_color_tag() -> None:
  # [[G]] is 3 display chars; truncating to 2 must not produce a partial sequence
  result = vb._truncate('[[G]]AB', 2)  # noqa: SLF001
  assert '[[G' not in result
  assert vb.display_len(result) <= 2


def test_truncate_includes_escaped_color_tag_when_it_fits() -> None:
  # Truncating to 4 display chars: [[G]] (3) + A (1) fits
  result = vb._truncate('[[G]]AB', 4)  # noqa: SLF001
  assert result == '[[G]]A'
  assert vb.display_len(result) == 4


# --- _expand_format (brace escaping) ---


def test_expand_format_escaped_braces_not_substituted() -> None:
  result = vb._expand_format(['{{variable}}'], {'variable': [['VALUE']]})  # noqa: SLF001
  assert result == ['{variable}']


def test_expand_format_escaped_braces_in_inline() -> None:
  result = vb._expand_format(['{{hi}} {name}'], {'name': [['WORLD']]})  # noqa: SLF001
  assert result == ['{hi} WORLD']


def test_expand_format_escaped_brace_whole_line_not_expanded() -> None:
  result = vb._expand_format(['{{lines}}'], {'lines': [['A', 'B']]})  # noqa: SLF001
  assert result == ['{lines}']


# --- _get_headers ---


def test_get_headers_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.delenv('VESTABOARD_API_KEY', raising=False)
  with pytest.raises(RuntimeError, match='VESTABOARD_API_KEY'):
    vb._get_headers()  # noqa: SLF001


def test_get_headers_returns_correct_headers(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'my-test-key')
  headers = vb._get_headers()  # noqa: SLF001
  assert headers['X-Vestaboard-Read-Write-Key'] == 'my-test-key'
  assert headers['Content-Type'] == 'application/json'


# --- get_state ---


def test_get_state_returns_state(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'test-key')
  layout = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  mock_resp = MagicMock()
  mock_resp.json.return_value = {
    'currentMessage': {
      'id': 'abc123',
      'appeared': '2024-01-01T00:00:00Z',
      'layout': json.dumps(layout),
    }
  }
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.get', return_value=mock_resp):
    state = vb.get_state()
  assert state.id == 'abc123'
  assert state.appeared == '2024-01-01T00:00:00Z'
  assert state.layout == layout


def test_get_state_passes_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'sentinel-key')
  layout = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  mock_resp = MagicMock()
  mock_resp.json.return_value = {'currentMessage': {'id': 'x', 'appeared': 'y', 'layout': json.dumps(layout)}}
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.get', return_value=mock_resp) as mock_get:
    vb.get_state()
  _, kwargs = mock_get.call_args
  assert kwargs['headers']['X-Vestaboard-Read-Write-Key'] == 'sentinel-key'


# --- set_state ---


def test_set_state_posts_grid_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'test-key')
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp) as mock_post:
    vb.set_state([{'format': ['HELLO']}], {})
  mock_post.assert_called_once()
  _, kwargs = mock_post.call_args
  grid = kwargs['json']
  assert len(grid) == vb.model.rows
  assert all(len(row) == vb.model.cols for row in grid)


def test_set_state_raises_board_locked_on_423(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'test-key')
  mock_resp = MagicMock()
  mock_resp.status_code = 423
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(vb.BoardLockedError):
      vb.set_state([{'format': ['HELLO']}], {})


def test_set_state_propagates_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'test-key')
  mock_resp = MagicMock()
  mock_resp.status_code = 500
  mock_resp.raise_for_status.side_effect = requests.HTTPError('server error')
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(requests.HTTPError):
      vb.set_state([{'format': ['HELLO']}], {})


def test_set_state_passes_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setenv('VESTABOARD_API_KEY', 'sentinel-key')
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp) as mock_post:
    vb.set_state([{'format': ['HELLO']}], {})
  _, kwargs = mock_post.call_args
  assert kwargs['headers']['X-Vestaboard-Read-Write-Key'] == 'sentinel-key'


# --- _expand_format (random selection) ---


def test_expand_format_picks_from_multiple_options() -> None:
  opts = [['FIRST'], ['SECOND']]
  with patch('integrations.vestaboard.random.choice', return_value=opts[1]):
    result = vb._expand_format(['{v}'], {'v': opts})  # noqa: SLF001
  assert result == ['SECOND']
