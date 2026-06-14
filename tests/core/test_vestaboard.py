import json
import logging
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.vestaboard as vb


@pytest.fixture(autouse=True)
def _reset_vestaboard_pacing() -> Generator[None, None, None]:
  """Reset module-level pacing state between tests so one test's _last_post_time
  cannot leak into the next (which would trigger unexpected real sleeps).
  """
  vb._last_post_time = 0.0  # noqa: SLF001
  yield
  vb._last_post_time = 0.0  # noqa: SLF001


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


# --- _encode_char: unicode normalization ---


def test_encode_line_accented_lowercase() -> None:
  # ï (U+00EF) → NFKD → i + combining diaeresis → i → I (code 9)
  result = vb._encode_line('ï')  # noqa: SLF001
  assert result[0] == 9  # I


def test_encode_line_accented_uppercase() -> None:
  # Ï (U+00CF) — as produced by .upper() on ï — should also normalize to I
  result = vb._encode_line('Ï')  # noqa: SLF001
  assert result[0] == 9  # I


def test_encode_line_accented_e() -> None:
  result = vb._encode_line('é')  # noqa: SLF001
  assert result[0] == 5  # E


def test_encode_line_accented_n() -> None:
  result = vb._encode_line('ñ')  # noqa: SLF001
  assert result[0] == 14  # N


def test_encode_line_accented_word() -> None:
  # 'ANAÏS' should encode with no blank tiles — regression test for Anaïs Mitchell
  result = vb._encode_line('ANAÏS')  # noqa: SLF001
  assert result[0] == 1  # A
  assert result[1] == 14  # N
  assert result[2] == 1  # A
  assert result[3] == 9  # I (from Ï)
  assert result[4] == 19  # S


def test_encode_line_combining_mark_standalone() -> None:
  # A bare combining diaeresis (U+0308) with no base letter → blank (code 0), no crash
  result = vb._encode_line('\u0308')  # noqa: SLF001
  assert result[0] == 0


def test_encode_line_padded_to_cols() -> None:
  result = vb._encode_line('A')  # noqa: SLF001
  assert len(result) == vb.model.cols
  assert all(x == 0 for x in result[1:])


def test_encode_line_truncated_at_cols() -> None:
  # Input longer than model.cols should be truncated, not overflow
  result = vb._encode_line('A' * (vb.model.cols + 10))  # noqa: SLF001
  assert len(result) == vb.model.cols


# --- truncate_line ---


def test_truncate_exact_fit_unchanged() -> None:
  text = 'A' * vb.model.cols
  assert vb.truncate_line(text, vb.model.cols) == text


def test_truncate_short_text_unchanged() -> None:
  assert vb.truncate_line('HI', 10) == 'HI'


def test_truncate_hard() -> None:
  assert vb.truncate_line('HELLO WORLD', 7) == 'HELLO W'


def test_truncate_word() -> None:
  assert vb.truncate_line('HELLO WORLD', 7, 'word') == 'HELLO'


def test_truncate_ellipsis() -> None:
  # target=7 (10-3): hard-cuts to 'HELLO W' (7 chars), then appends '...'
  assert vb.truncate_line('HELLO WORLD', 10, 'ellipsis') == 'HELLO W...'


def test_truncate_ellipsis_strips_trailing_space() -> None:
  # target=6 (9-3): hard-cuts to 'HELLO ' (trailing space), must strip before '...'
  assert vb.truncate_line('HELLO WORLD', 9, 'ellipsis') == 'HELLO...'


def test_truncate_ellipsis_strips_trailing_hyphen() -> None:
  # 'FOO - BAR' truncated to 8 chars (5+3): hard-cut to 'FOO - ' → strip ' - '
  assert vb.truncate_line('FOO - BAR EXTRA', 8, 'ellipsis') == 'FOO...'


def test_truncate_ellipsis_strips_trailing_colon() -> None:
  # 'FOO: BAR' truncated to 8 chars (5+3): hard-cut to 'FOO: ' → strip ': '
  assert vb.truncate_line('FOO: BAR EXTRA', 8, 'ellipsis') == 'FOO...'


def test_truncate_ellipsis_strips_trailing_comma() -> None:
  assert vb.truncate_line('FOO, BAR EXTRA', 8, 'ellipsis') == 'FOO...'


def test_truncate_ellipsis_strips_trailing_semicolon() -> None:
  assert vb.truncate_line('FOO; BAR EXTRA', 8, 'ellipsis') == 'FOO...'


def test_truncate_ellipsis_strips_trailing_em_dash() -> None:
  # em-dash counts as one display char
  assert vb.truncate_line('FOO—BAR EXTRA', 7, 'ellipsis') == 'FOO...'


def test_truncate_ellipsis_strips_trailing_apostrophe() -> None:
  # Cut lands after a closing apostrophe — strip it so '...' reads cleanly.
  # target=9 (12-3): keeps "SAID 'HI'", trim drops trailing apostrophe.
  assert vb.truncate_line("SAID 'HI' AGAIN", 12, 'ellipsis') == "SAID 'HI..."


def test_truncate_ellipsis_strips_trailing_double_quote() -> None:
  assert vb.truncate_line('SAID "HI" AGAIN', 12, 'ellipsis') == 'SAID "HI...'


def test_truncate_ellipsis_preserves_period() -> None:
  # Period is intentionally excluded from the trim set so abbreviations like
  # 'U.S.' aren't mangled. Result keeps the period; user sees four dots total.
  assert vb.truncate_line('U.S. ARMY GUYS', 7, 'ellipsis') == 'U.S....'


def test_truncate_ellipsis_no_word_backtrack() -> None:
  # ellipsis must NOT backtrack to the word boundary — result is longer than word cut
  word_result = vb.truncate_line('HELLO WORLD', 10, 'word')
  ellipsis_result = vb.truncate_line('HELLO WORLD', 10, 'ellipsis')
  assert len(ellipsis_result.rstrip('.')) > len(word_result)


def test_truncate_word_no_space_falls_back_to_hard() -> None:
  # No space before the limit — word strategy behaves like hard
  assert vb.truncate_line('HELLOWORLD', 5, 'word') == 'HELLO'


def test_truncate_word_last_word_fits_exactly() -> None:
  # Last word ends exactly at the column limit — must include it, not trim back
  # (regression: was returning 'AB' instead of 'AB CD')
  assert vb.truncate_line('AB CD EF', 5, 'word') == 'AB CD'


def test_truncate_preserves_color_tag() -> None:
  # Truncating to 1 display char should return the full [G] token, not split it
  assert vb.truncate_line('[G]AB', 1) == '[G]'


def test_truncate_preserves_heart() -> None:
  assert vb.truncate_line('❤️AB', 1) == '❤️'


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


def test_wrap_lines_ellipsis_truncates_instead_of_wrapping() -> None:
  # With ellipsis strategy a long line must stay on one row, not wrap.
  # This is the Discogs bug: a long album title was wrapping onto row 3,
  # pushing the artist name off the board entirely.
  long_line = 'HOLLOW KNIGHT GODS AND MONSTERS'  # > 15 cols
  result = vb._wrap_lines([long_line], truncation='ellipsis')  # noqa: SLF001
  assert len(result) == 1
  assert vb.display_len(result[0]) <= vb.model.cols
  assert result[0].endswith('...')


def test_wrap_lines_ellipsis_preserves_fixed_layout() -> None:
  # Three-line fixed layout: long album must not push artist off row 3.
  lines = ['[Y] MORNING SPIN', 'HOLLOW KNIGHT GODS AND MONSTERS', 'TEAM CHERRY']
  result = vb._wrap_lines(lines, truncation='ellipsis')  # noqa: SLF001
  assert result[0] == '[Y] MORNING SPIN'
  assert result[1].endswith('...')
  assert result[2] == 'TEAM CHERRY'


# --- wrap_ellipsis ---


def test_wrap_ellipsis_wraps_long_line() -> None:
  # A line that exceeds cols should wrap rather than truncate on the first row.
  # Note board: 3 rows x 15 cols. Header uses row 1; body has 2 rows.
  lines = ['[R] FROM ALICE', 'HEY THE FOOD IS READY']  # body is 21 chars
  result = vb._wrap_lines(lines, truncation='wrap_ellipsis')  # noqa: SLF001
  assert result[0] == '[R] FROM ALICE'
  assert result[1] == 'HEY THE FOOD IS'
  assert result[2] == 'READY'


def test_wrap_ellipsis_adds_ellipsis_on_row_overflow() -> None:
  # When wrapped content exceeds model.rows, the last kept row gets '...'.
  # Note board: 3 rows. Header + 3 body words that wrap to 3 rows = overflow.
  lines = ['[R] FROM ALICE', 'WORD ONE TWO THREE FOUR FIVE SIX SEVEN']
  result = vb._wrap_lines(lines, truncation='wrap_ellipsis')  # noqa: SLF001
  assert len(result) == vb.model.rows
  assert result[-1].endswith('...')


def test_wrap_ellipsis_no_ellipsis_when_fits() -> None:
  # No ellipsis when content fits within model.rows without overflow.
  lines = ['[R] FROM ALICE', 'SHORT MSG']
  result = vb._wrap_lines(lines, truncation='wrap_ellipsis')  # noqa: SLF001
  assert result == ['[R] FROM ALICE', 'SHORT MSG']
  assert not result[-1].endswith('...')


# --- _strip_unsupported ---


def test_strip_unsupported_removes_hiragana() -> None:
  # Japanese hiragana has no Vestaboard mapping and must be dropped.
  result = vb._strip_unsupported('あつまれ ANIMAL CROSSING')  # noqa: SLF001
  assert result == 'ANIMAL CROSSING'


def test_strip_unsupported_collapses_spaces() -> None:
  # Stripping characters that were surrounded by spaces must not leave double spaces.
  result = vb._strip_unsupported('HELLO あ WORLD')  # noqa: SLF001
  assert result == 'HELLO WORLD'


def test_strip_unsupported_preserves_color_tags() -> None:
  result = vb._strip_unsupported('[R] MORNING SPIN')  # noqa: SLF001
  assert result == '[R] MORNING SPIN'


def test_strip_unsupported_preserves_ascii() -> None:
  result = vb._strip_unsupported('ANIMAL CROSSING: NEW HORIZONS')  # noqa: SLF001
  assert result == 'ANIMAL CROSSING: NEW HORIZONS'


def test_strip_unsupported_normalizes_smart_single_quotes() -> None:
  # iOS/macOS auto-replace ' with \u2018/\u2019 smart quotes.
  result = vb._strip_unsupported('\u2018HELLO\u2019')  # noqa: SLF001
  assert result == "'HELLO'"


def test_strip_unsupported_normalizes_smart_double_quotes() -> None:
  result = vb._strip_unsupported('\u201cWORLD\u201d')  # noqa: SLF001
  assert result == '"WORLD"'


def test_strip_unsupported_normalizes_em_dash() -> None:
  result = vb._strip_unsupported('A\u2014B')  # noqa: SLF001
  assert result == 'A-B'


def test_strip_unsupported_normalizes_en_dash() -> None:
  result = vb._strip_unsupported('3\u20135')  # noqa: SLF001
  assert result == '3-5'


def test_strip_unsupported_normalizes_ellipsis() -> None:
  result = vb._strip_unsupported('WAIT\u2026')  # noqa: SLF001
  assert result == 'WAIT...'


def test_strip_unsupported_normalizes_mixed_unicode_punct() -> None:
  # Combined: smart quotes + em dash + ellipsis in one string.
  result = vb._strip_unsupported('\u201cHI\u201d \u2014 BYE\u2026')  # noqa: SLF001
  assert result == '"HI" - BYE...'


def test_wrap_lines_strips_unsupported_before_wrap() -> None:
  # Japanese prefix + English suffix: unsupported chars stripped, English visible.
  lines = ['あつまれ どうぶつの森 = ANIMAL CROSSING']
  result = vb._wrap_lines(lines, truncation='ellipsis')  # noqa: SLF001
  assert result[0].startswith('= ANIMAL')
  assert 'あ' not in result[0]


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


# --- truncate_line (escaped tags) ---


def test_truncate_does_not_split_escaped_color_tag() -> None:
  # [[G]] is 3 display chars; truncating to 2 must not produce a partial sequence
  result = vb.truncate_line('[[G]]AB', 2)
  assert '[[G' not in result
  assert vb.display_len(result) <= 2


def test_truncate_includes_escaped_color_tag_when_it_fits() -> None:
  # Truncating to 4 display chars: [[G]] (3) + A (1) fits
  result = vb.truncate_line('[[G]]AB', 4)
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


def test_get_headers_uses_config_key(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'my-test-key'}})
  headers = vb._get_headers()  # noqa: SLF001
  assert headers['X-Vestaboard-Read-Write-Key'] == 'my-test-key'
  assert headers['Content-Type'] == 'application/json'


def test_get_headers_missing_key_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {})
  with pytest.raises(ValueError, match='vestaboard'):
    vb._get_headers()  # noqa: SLF001


# --- get_state ---


def test_get_state_returns_state(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
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
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'sentinel-key'}})
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
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
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
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 423
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(vb.BoardLockedError):
      vb.set_state([{'format': ['HELLO']}], {})


def test_set_state_propagates_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'sentinel-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 500
  mock_resp.reason = 'Internal Server Error'
  mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(requests.HTTPError, match='Vestaboard API error: 500'):
      vb.set_state([{'format': ['HELLO']}], {})


def test_set_state_http_error_does_not_leak_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'sentinel-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 500
  mock_resp.reason = 'Internal Server Error'
  mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(requests.HTTPError) as exc_info:
      vb.set_state([{'format': ['HELLO']}], {})
  assert 'sentinel-key' not in str(exc_info.value)


def test_set_state_raises_duplicate_on_409(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 409
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(vb.DuplicateContentError):
      vb.set_state([{'format': ['HELLO']}], {})


def test_get_state_raises_empty_board_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 404
  with patch('integrations.vestaboard.requests.get', return_value=mock_resp):
    with pytest.raises(vb.EmptyBoardError):
      vb.get_state()


def test_get_state_http_error_does_not_leak_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'sentinel-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 401
  mock_resp.reason = 'Unauthorized'
  mock_resp.raise_for_status.side_effect = requests.HTTPError(response=mock_resp)
  with patch('integrations.vestaboard.requests.get', return_value=mock_resp):
    with pytest.raises(requests.HTTPError, match='Vestaboard API error: 401') as exc_info:
      vb.get_state()
  assert 'sentinel-key' not in str(exc_info.value)


def test_set_state_passes_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'sentinel-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp) as mock_post:
    vb.set_state([{'format': ['HELLO']}], {})
  _, kwargs = mock_post.call_args
  assert kwargs['headers']['X-Vestaboard-Read-Write-Key'] == 'sentinel-key'


def test_set_state_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  rate_limited = MagicMock()
  rate_limited.status_code = 429
  rate_limited.reason = 'Too Many Requests'
  ok = MagicMock()
  ok.status_code = 200
  ok.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.post', side_effect=[rate_limited, ok]) as mock_post,
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
  ):
    vb.set_state([{'format': ['HELLO']}], {})
  assert mock_post.call_count == 2
  mock_sleep.assert_called_once_with(vb._TRANSIENT_BACKOFF)  # noqa: SLF001


def test_set_state_raises_after_exhausted_429_retries(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  rate_limited = MagicMock()
  rate_limited.status_code = 429
  rate_limited.reason = 'Too Many Requests'
  with (
    patch('integrations.vestaboard.requests.post', return_value=rate_limited) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(requests.HTTPError, match='429 Too Many Requests'):
      vb.set_state([{'format': ['HELLO']}], {})
  assert mock_post.call_count == vb._MAX_TRANSIENT_RETRIES + 1  # noqa: SLF001


def test_set_state_logs_warning_on_429_retry(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  rate_limited = MagicMock()
  rate_limited.status_code = 429
  rate_limited.reason = 'Too Many Requests'
  ok = MagicMock()
  ok.status_code = 200
  ok.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.post', side_effect=[rate_limited, ok]),
    patch('integrations.vestaboard.time.sleep'),
    caplog.at_level(logging.WARNING, logger='integrations.vestaboard'),
  ):
    vb.set_state([{'format': ['HELLO']}], {})
  assert any('Vestaboard 429' in r.message for r in caplog.records)


# --- _expand_format (random selection) ---


def test_expand_format_picks_from_multiple_options() -> None:
  opts = [['FIRST'], ['SECOND']]
  with patch('integrations.vestaboard.random.choice', return_value=opts[1]):
    result = vb._expand_format(['{v}'], {'v': opts})  # noqa: SLF001
  assert result == ['SECOND']


# --- render_grid ---


def test_render_grid_note_dimensions() -> None:
  grid = [[0] * vb.VestaboardModel.NOTE.cols for _ in range(vb.VestaboardModel.NOTE.rows)]
  output = vb.render_grid(grid)
  lines = output.splitlines()
  # top border + rows + bottom border
  assert len(lines) == vb.VestaboardModel.NOTE.rows + 2
  assert lines[0].startswith('┌')
  assert lines[-1].startswith('└')


def test_render_grid_flagship_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(vb, 'model', vb.VestaboardModel.FLAGSHIP)
  grid = [[0] * vb.VestaboardModel.FLAGSHIP.cols for _ in range(vb.VestaboardModel.FLAGSHIP.rows)]
  output = vb.render_grid(grid)
  lines = output.splitlines()
  assert len(lines) == vb.VestaboardModel.FLAGSHIP.rows + 2


# --- render ---


def test_render_returns_character_grid() -> None:
  grid = vb.render([{'format': ['HELLO']}], {})
  assert len(grid) == vb.model.rows
  assert all(len(row) == vb.model.cols for row in grid)
  # First row should start with H=8, E=5, L=12, L=12, O=15
  assert grid[0][:5] == [8, 5, 12, 12, 15]


def test_render_does_not_call_api() -> None:
  with patch('integrations.vestaboard.requests.post') as mock_post:
    vb.render([{'format': ['HELLO']}], {})
  mock_post.assert_not_called()


# --- set_state_raw ---


def test_set_state_raw_posts_grid(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp) as mock_post:
    vb.set_state_raw(grid)
  mock_post.assert_called_once()
  _, kwargs = mock_post.call_args
  assert kwargs['json'] == grid


def test_set_state_raw_raises_board_locked(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 423
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp):
    with pytest.raises(vb.BoardLockedError):
      vb.set_state_raw([[0] * vb.model.cols] * vb.model.rows)


def test_set_state_calls_render_then_raw(monkeypatch: pytest.MonkeyPatch) -> None:
  """set_state() convenience wrapper calls render() then set_state_raw()."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with patch('integrations.vestaboard.requests.post', return_value=mock_resp) as mock_post:
    vb.set_state([{'format': ['TEST']}], {})
  mock_post.assert_called_once()
  _, kwargs = mock_post.call_args
  grid = kwargs['json']
  assert len(grid) == vb.model.rows


# --- pacing gate ---


def test_pacing_gate_first_call_no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
  """The first call after process start (or test reset) must not sleep."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.post', return_value=mock_resp),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
  ):
    vb.set_state_raw([[0] * vb.model.cols for _ in range(vb.model.rows)])
  mock_sleep.assert_not_called()


def test_pacing_gate_second_call_waits(monkeypatch: pytest.MonkeyPatch) -> None:
  """A rapid second call must sleep approximately _MIN_POST_INTERVAL seconds."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  # Freeze monotonic time so elapsed is exactly 0 between calls.
  with (
    patch('integrations.vestaboard.requests.post', return_value=mock_resp),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
    patch('integrations.vestaboard.time.monotonic', return_value=100.0),
  ):
    grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
    vb.set_state_raw(grid)
    vb.set_state_raw(grid)
  # First call: no sleep. Second call: full interval.
  mock_sleep.assert_called_once_with(vb._MIN_POST_INTERVAL)  # noqa: SLF001


def test_pacing_gate_elapsed_exceeds_interval(monkeypatch: pytest.MonkeyPatch) -> None:
  """If enough time has passed since the last call, no wait is needed."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  # Simulate a prior call well outside the pacing window.
  vb._last_post_time = 100.0  # noqa: SLF001
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.post', return_value=mock_resp),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
    patch('integrations.vestaboard.time.monotonic', return_value=200.0),
  ):
    vb.set_state_raw([[0] * vb.model.cols for _ in range(vb.model.rows)])
  mock_sleep.assert_not_called()


def test_pacing_gate_failure_still_updates_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
  """A failed POST still counts against pacing — the next call must wait."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  # Use 501 (non-retryable) so we test the pacing-on-failure path without
  # 5xx retry backoff sleeps muddying the assertion.
  fail = MagicMock()
  fail.status_code = 501
  fail.reason = 'Not Implemented'
  fail.raise_for_status.side_effect = requests.HTTPError(response=fail)
  ok = MagicMock()
  ok.status_code = 200
  ok.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.post', side_effect=[fail, ok]),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
    patch('integrations.vestaboard.time.monotonic', return_value=100.0),
  ):
    grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
    with pytest.raises(requests.HTTPError):
      vb.set_state_raw(grid)
    vb.set_state_raw(grid)
  # First call: no wait (fresh state). Second call: full pacing interval.
  mock_sleep.assert_called_once_with(vb._MIN_POST_INTERVAL)  # noqa: SLF001


def test_pacing_gate_applies_to_get_state(monkeypatch: pytest.MonkeyPatch) -> None:
  """get_state() must also respect the pacing gate for consistency."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  layout = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  mock_resp = MagicMock()
  mock_resp.json.return_value = {
    'currentMessage': {
      'id': 'abc',
      'appeared': '2024-01-01T00:00:00Z',
      'layout': json.dumps(layout),
    }
  }
  mock_resp.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.get', return_value=mock_resp),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
    patch('integrations.vestaboard.time.monotonic', return_value=100.0),
  ):
    vb.get_state()
    vb.get_state()
  mock_sleep.assert_called_once_with(vb._MIN_POST_INTERVAL)  # noqa: SLF001


def test_pacing_gate_disabled_via_interval_zero(monkeypatch: pytest.MonkeyPatch) -> None:
  """Setting _MIN_POST_INTERVAL to 0 disables pacing entirely."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  monkeypatch.setattr(vb, '_MIN_POST_INTERVAL', 0.0)
  mock_resp = MagicMock()
  mock_resp.status_code = 200
  mock_resp.raise_for_status.return_value = None
  with (
    patch('integrations.vestaboard.requests.post', return_value=mock_resp),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
    patch('integrations.vestaboard.time.monotonic', return_value=100.0),
  ):
    grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
    vb.set_state_raw(grid)
    vb.set_state_raw(grid)
  mock_sleep.assert_not_called()


# --- 5xx retry and combined retry budget (#511) ---


def _mock_resp(status: int, reason: str = '') -> MagicMock:
  """Build a MagicMock response with the given status and a matching reason."""
  m = MagicMock()
  m.status_code = status
  m.reason = reason or {
    429: 'Too Many Requests',
    500: 'Internal Server Error',
    501: 'Not Implemented',
    502: 'Bad Gateway',
    503: 'Service Unavailable',
    504: 'Gateway Timeout',
    400: 'Bad Request',
  }.get(status, 'Unknown')
  if 200 <= status < 300:
    m.raise_for_status.return_value = None
  else:
    m.raise_for_status.side_effect = requests.HTTPError(f'{status} {m.reason}', response=m)
  return m


def test_set_state_raw_retries_on_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  responses = [_mock_resp(500), _mock_resp(500), _mock_resp(200)]
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  with (
    patch('integrations.vestaboard.requests.post', side_effect=responses) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    vb.set_state_raw(grid)
  assert mock_post.call_count == 3


def test_set_state_raw_retries_on_502_503_504(monkeypatch: pytest.MonkeyPatch) -> None:
  """Each of the 5xx codes in _RETRYABLE_STATUS triggers a retry."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  for status in (502, 503, 504):
    responses = [_mock_resp(status), _mock_resp(200)]
    with (
      patch('integrations.vestaboard.requests.post', side_effect=responses) as mock_post,
      patch('integrations.vestaboard.time.sleep'),
    ):
      vb.set_state_raw(grid)
    assert mock_post.call_count == 2, f'expected retry on {status}'


def test_set_state_raw_exhausts_retries_on_persistent_500(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  with (
    patch('integrations.vestaboard.requests.post', return_value=_mock_resp(500)) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(requests.HTTPError, match=r'500 Internal Server Error \(exhausted 3 retries\)'):
      vb.set_state_raw(grid)
  assert mock_post.call_count == vb._MAX_TRANSIENT_RETRIES + 1  # noqa: SLF001


def test_set_state_raw_mixed_429_and_500_shares_budget(monkeypatch: pytest.MonkeyPatch) -> None:
  """429 and 5xx use the same retry budget — not separate counters."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  # 4 attempts total (1 + 3 retries). Mix 429 and 500 until the last attempt succeeds.
  responses = [_mock_resp(429), _mock_resp(500), _mock_resp(500), _mock_resp(200)]
  with (
    patch('integrations.vestaboard.requests.post', side_effect=responses) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    vb.set_state_raw(grid)
  assert mock_post.call_count == 4


def test_set_state_raw_501_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
  """501 Not Implemented is permanent — do not retry."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  with (
    patch('integrations.vestaboard.requests.post', return_value=_mock_resp(501)) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(requests.HTTPError, match='501'):
      vb.set_state_raw(grid)
  assert mock_post.call_count == 1


def test_set_state_raw_4xx_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
  """Non-retryable 4xx fails on the first attempt."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  with (
    patch('integrations.vestaboard.requests.post', return_value=_mock_resp(400)) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(requests.HTTPError, match='400'):
      vb.set_state_raw(grid)
  assert mock_post.call_count == 1


def test_set_state_raw_retry_backoff_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
  """Exponential backoff: 5s, 10s, 20s."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  with (
    patch('integrations.vestaboard.requests.post', return_value=_mock_resp(500)),
    patch('integrations.vestaboard.time.sleep') as mock_sleep,
  ):
    with pytest.raises(requests.HTTPError):
      vb.set_state_raw(grid)
  delays = [call.args[0] for call in mock_sleep.call_args_list]
  assert delays == [5.0, 10.0, 20.0]


def test_set_state_raw_409_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
  """409 Duplicate raises DuplicateContentError immediately."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  dup = MagicMock()
  dup.status_code = 409
  with (
    patch('integrations.vestaboard.requests.post', return_value=dup) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(vb.DuplicateContentError):
      vb.set_state_raw(grid)
  assert mock_post.call_count == 1


def test_set_state_raw_423_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
  """423 BoardLocked raises immediately — scheduler handles via re-enqueue."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  grid = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  locked = MagicMock()
  locked.status_code = 423
  with (
    patch('integrations.vestaboard.requests.post', return_value=locked) as mock_post,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(vb.BoardLockedError):
      vb.set_state_raw(grid)
  assert mock_post.call_count == 1


# --- get_state retry ---


def _mock_get_ok() -> MagicMock:
  layout = [[0] * vb.model.cols for _ in range(vb.model.rows)]
  m = MagicMock()
  m.status_code = 200
  m.json.return_value = {
    'currentMessage': {
      'id': 'abc',
      'appeared': '2024-01-01T00:00:00Z',
      'layout': json.dumps(layout),
    }
  }
  m.raise_for_status.return_value = None
  return m


def test_get_state_retries_on_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  responses = [_mock_resp(500), _mock_get_ok()]
  with (
    patch('integrations.vestaboard.requests.get', side_effect=responses) as mock_get,
    patch('integrations.vestaboard.time.sleep'),
  ):
    state = vb.get_state()
  assert mock_get.call_count == 2
  assert state.id == 'abc'


def test_get_state_exhausts_retries_on_persistent_500(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  with (
    patch('integrations.vestaboard.requests.get', return_value=_mock_resp(500)) as mock_get,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(requests.HTTPError, match=r'500 Internal Server Error \(exhausted 3 retries\)'):
      vb.get_state()
  assert mock_get.call_count == vb._MAX_TRANSIENT_RETRIES + 1  # noqa: SLF001


def test_get_state_404_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
  """404 raises EmptyBoardError immediately — no retries."""
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  empty = MagicMock()
  empty.status_code = 404
  with (
    patch('integrations.vestaboard.requests.get', return_value=empty) as mock_get,
    patch('integrations.vestaboard.time.sleep'),
  ):
    with pytest.raises(vb.EmptyBoardError):
      vb.get_state()
  assert mock_get.call_count == 1


# --- render_grid_text (plain-text /state rendering) ---


def test_render_grid_text_letters_and_blanks() -> None:
  # code 1 → 'A' (first letter); code 0 → blank.
  row = [1, 0, 0]
  out = vb.render_grid_text([row])
  assert out[0] == vb._CHAR_MAP[1]  # noqa: SLF001
  assert out[1] == ' '
  assert len(out) == 3


def test_render_grid_text_color_squares_use_letters() -> None:
  # codes 63-70 → single color letters, keeping rows column-aligned.
  out = vb.render_grid_text([[63, 66, 67]])
  assert out == 'RGB'


def test_render_grid_text_no_ansi_escapes() -> None:
  out = vb.render_grid_text([[1, 63, 71], [62, 0, 2]])
  assert '\033' not in out


def test_render_grid_text_rows_joined_with_newline() -> None:
  out = vb.render_grid_text([[0, 0], [0, 0]])
  assert out.count('\n') == 1


def test_render_grid_text_heart_on_note() -> None:
  vb.model = vb.VestaboardModel.NOTE
  out = vb.render_grid_text([[62]])
  assert out == '❤'


def test_render_grid_text_degree_on_flagship() -> None:
  original = vb.model
  vb.model = vb.VestaboardModel.FLAGSHIP
  try:
    out = vb.render_grid_text([[62]])
    assert out == '°'
  finally:
    vb.model = original


# --- last-grid cache (for the /state endpoint) ---


def test_get_cached_grid_none_initially() -> None:
  vb._last_grid = None  # noqa: SLF001
  grid, ts = vb.get_cached_grid()
  assert grid is None
  assert ts == 0.0


def test_set_state_raw_caches_grid(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _config_mod

  monkeypatch.setattr(_config_mod, '_config', {'vestaboard': {'api_key': 'test-key'}})
  vb._last_grid = None  # noqa: SLF001
  grid = [[1, 2, 3], [0, 0, 0]]
  ok = MagicMock()
  ok.status_code = 200
  ok.raise_for_status = MagicMock()
  with (
    patch('integrations.vestaboard.requests.post', return_value=ok),
    patch('integrations.vestaboard.time.sleep'),
  ):
    vb.set_state_raw(grid)
  cached, ts = vb.get_cached_grid()
  assert cached == grid
  assert ts > 0
  # Returned grid is a copy — mutating it must not corrupt the cache.
  assert cached is not None
  cached[0][0] = 99
  again, _ = vb.get_cached_grid()
  assert again is not None
  assert again[0][0] == 1
