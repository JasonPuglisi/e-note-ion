"""Unit tests for integrations/media.py."""

import integrations.media as media

# --- format_episode_ref ---


def test_format_episode_ref_no_padding() -> None:
  assert media.format_episode_ref(9, 8) == 'S9E8'


def test_format_episode_ref_double_digit() -> None:
  assert media.format_episode_ref(12, 24) == 'S12E24'


def test_format_episode_ref_single_digit_each() -> None:
  assert media.format_episode_ref(1, 1) == 'S1E1'


# --- strip_leading_article ---


def test_strip_leading_article_the() -> None:
  assert media.strip_leading_article('THE FINAL SHOWDOWN') == 'FINAL SHOWDOWN'


def test_strip_leading_article_a() -> None:
  assert media.strip_leading_article('A QUIET MAN') == 'QUIET MAN'


def test_strip_leading_article_an() -> None:
  assert media.strip_leading_article('AN UNEXPECTED JOURNEY') == 'UNEXPECTED JOURNEY'


def test_strip_leading_article_no_article() -> None:
  assert media.strip_leading_article('PILOT') == 'PILOT'


def test_strip_leading_article_word_starting_with_the() -> None:
  # "THEORY" should not be stripped — must match full word boundary
  assert media.strip_leading_article('THEORY OF EVERYTHING') == 'THEORY OF EVERYTHING'


def test_strip_leading_article_word_starting_with_a() -> None:
  # "AFTERMATH" should not be stripped
  assert media.strip_leading_article('AFTERMATH') == 'AFTERMATH'


def test_strip_leading_article_empty() -> None:
  assert media.strip_leading_article('') == ''
