from typing import Any

import pytest

import config as _cfg
import integrations.plex as _plex
import integrations.vestaboard as _vb
import scheduler as _mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _episode_payload(
  event: str = 'media.play',
  title: str = 'The Beef',
  show: str = 'The Bear',
) -> dict[str, Any]:
  """Return a minimal Plex webhook payload for an episode event."""
  return {
    'event': event,
    'Metadata': {
      'type': 'episode',
      'grandparentTitle': show,
      'parentIndex': 2,
      'index': 1,
      'title': title,
    },
  }


def _movie_payload(event: str = 'media.play', title: str = 'A Quiet Place') -> dict[str, Any]:
  """Return a minimal Plex webhook payload for a movie event."""
  return {
    'event': event,
    'Metadata': {
      'type': 'movie',
      'title': title,
    },
  }


@pytest.fixture(autouse=True)
def _empty_config(monkeypatch: pytest.MonkeyPatch) -> None:
  """Ensure config has no plex schedule overrides for most tests."""
  monkeypatch.setattr(_cfg, '_config', {})


@pytest.fixture(autouse=True)
def _reset_stop_processed(monkeypatch: pytest.MonkeyPatch) -> None:
  """Reset _stop_processed module state before each test."""
  monkeypatch.setattr(_plex, '_stop_processed', False)


# ---------------------------------------------------------------------------
# play / resume → now_playing
# ---------------------------------------------------------------------------


def test_handle_webhook_play_returns_indefinite_now_playing() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play'))
  assert isinstance(result, _mod.WebhookMessage)
  assert result.indefinite is True
  assert result.interrupt is True
  assert result.interrupt_only is False
  assert 'NOW PLAYING' in str(result.data['templates'])


def test_handle_webhook_resume_returns_indefinite_now_playing() -> None:
  result = _plex.handle_webhook(_episode_payload('media.resume'))
  assert isinstance(result, _mod.WebhookMessage)
  assert result.indefinite is True
  assert 'NOW PLAYING' in str(result.data['templates'])


# ---------------------------------------------------------------------------
# pause → paused
# ---------------------------------------------------------------------------


def test_handle_webhook_pause_returns_indefinite_now_playing_yellow() -> None:
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert isinstance(result, _mod.WebhookMessage)
  assert result.indefinite is True
  assert result.interrupt is True
  assert '[Y] NOW PLAYING' in str(result.data['templates'])


# ---------------------------------------------------------------------------
# stop → stopped card
# ---------------------------------------------------------------------------


def test_handle_webhook_stop_returns_stopped_card() -> None:
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert isinstance(result, _mod.WebhookMessage)
  assert result.interrupt_only is False
  assert result.interrupt is True
  assert result.indefinite is False
  assert '[R] NOW PLAYING' in str(result.data['templates'])


def test_handle_webhook_stop_has_finite_hold() -> None:
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is not None
  assert result.hold > 0
  assert result.timeout > 0


def test_handle_webhook_stop_with_episode_metadata_includes_show_variables() -> None:
  result = _plex.handle_webhook(_episode_payload('media.stop'))
  assert result is not None
  assert '[R] NOW PLAYING' in str(result.data['templates'])
  variables = result.data['variables']
  assert variables['show_name'] == [['THE BEAR']]
  assert variables['episode_line'] == [['S2E1 BEEF']]
  assert result.indefinite is False
  assert result.interrupt is True


def test_handle_webhook_stop_with_movie_metadata_includes_show_variables() -> None:
  result = _plex.handle_webhook(_movie_payload('media.stop', 'Inception'))
  assert result is not None
  variables = result.data['variables']
  assert variables['show_name'] == [['INCEPTION']]
  assert variables['episode_line'] == [['']]


def test_handle_webhook_stop_without_metadata_returns_bare_stopped_card() -> None:
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is not None
  assert result.data['variables'] == {}


def test_handle_webhook_stop_with_non_video_metadata_returns_bare_stopped_card() -> None:
  payload = {
    'event': 'media.stop',
    'Metadata': {'type': 'track', 'title': 'Some Song'},
  }
  result = _plex.handle_webhook(payload)
  assert result is not None
  assert result.data['variables'] == {}


# ---------------------------------------------------------------------------
# Ignored events
# ---------------------------------------------------------------------------


def test_handle_webhook_unknown_event_returns_none() -> None:
  assert _plex.handle_webhook({'event': 'media.rate'}) is None


def test_handle_webhook_scrobble_returns_none() -> None:
  """media.scrobble is not handled — it fires at ~80% playback and is too noisy."""
  assert _plex.handle_webhook({'event': 'media.scrobble'}) is None


# ---------------------------------------------------------------------------
# Media type filtering
# ---------------------------------------------------------------------------


def test_handle_webhook_play_non_video_type_returns_none() -> None:
  payload = {
    'event': 'media.play',
    'Metadata': {'type': 'track', 'title': 'Some Song'},
  }
  assert _plex.handle_webhook(payload) is None


def test_handle_webhook_missing_metadata_returns_none() -> None:
  assert _plex.handle_webhook({'event': 'media.play'}) is None


# ---------------------------------------------------------------------------
# Movie metadata
# ---------------------------------------------------------------------------


def test_handle_webhook_movie_has_empty_episode_line() -> None:
  result = _plex.handle_webhook(_movie_payload('media.play', 'Inception'))
  assert result is not None
  variables = result.data['variables']
  assert variables['episode_line'] == [['']]
  assert variables['show_name'] == [['INCEPTION']]


# ---------------------------------------------------------------------------
# episode_line formatting and article stripping
# ---------------------------------------------------------------------------


def test_handle_webhook_episode_line_includes_season_episode_ref() -> None:
  """episode_line must include the S/E reference so it appears on the board."""
  result = _plex.handle_webhook(_episode_payload('media.play', title='The Beef'))
  assert result is not None
  # parentIndex=2, index=1 → S2E1; article stripped from title → BEEF
  assert result.data['variables']['episode_line'] == [['S2E1 BEEF']]


def test_handle_webhook_episode_strips_a_article_in_episode_line() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play', title='A New Hope'))
  assert result is not None
  assert result.data['variables']['episode_line'] == [['S2E1 NEW HOPE']]


def test_handle_webhook_show_name_preserves_article() -> None:
  """Show names are NOT article-stripped — "THE BEAR" stays "THE BEAR"."""
  result = _plex.handle_webhook(_episode_payload('media.play'))
  assert result is not None
  assert result.data['variables']['show_name'] == [['THE BEAR']]


def test_handle_webhook_movie_title_preserves_article() -> None:
  """Movie titles are NOT article-stripped."""
  result = _plex.handle_webhook(_movie_payload('media.play', 'A Quiet Place'))
  assert result is not None
  assert result.data['variables']['show_name'] == [['A QUIET PLACE']]


def test_handle_webhook_long_show_name_truncated_to_one_row() -> None:
  """A show name longer than model.cols must be word-truncated, not left to wrap."""
  long_show = 'Star Trek The Next Generation'
  result = _plex.handle_webhook(_episode_payload(show=long_show))
  assert result is not None
  show_name = result.data['variables']['show_name'][0][0]
  upper = long_show.upper()
  # Must fit in one display row.
  assert _vb.display_len(show_name) <= _vb.model.cols
  # Must be a whole-word prefix of the original (no mid-word cut).
  assert upper.startswith(show_name)
  assert show_name == upper or upper[len(show_name)] == ' '


# ---------------------------------------------------------------------------
# Config override
# ---------------------------------------------------------------------------


def test_handle_webhook_applies_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    _cfg,
    '_config',
    {'plex': {'schedules': {'now_playing': {'hold': 7200, 'priority': 9}}}},
  )
  result = _plex.handle_webhook(_episode_payload('media.play'))
  assert result is not None
  assert result.hold == 7200
  assert result.priority == 9


# ---------------------------------------------------------------------------
# Trakt coordination
# ---------------------------------------------------------------------------


def test_handle_webhook_clears_trakt_watching_state(monkeypatch: pytest.MonkeyPatch) -> None:
  """Any handled Plex event clears Trakt's cached watching state."""
  import integrations.trakt as _trakt

  _trakt._last_watching_vars = {'show_name': [['SOME SHOW']]}
  _plex.handle_webhook(_episode_payload('media.play'))
  assert _trakt._last_watching_vars is None


# ---------------------------------------------------------------------------
# supersede_tag
# ---------------------------------------------------------------------------


def test_handle_webhook_play_has_supersede_tag() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play'))
  assert result is not None
  assert result.supersede_tag == 'plex'


def test_handle_webhook_pause_has_supersede_tag() -> None:
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert result is not None
  assert result.supersede_tag == 'plex'


def test_handle_webhook_stop_has_supersede_tag() -> None:
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is not None
  assert result.supersede_tag == 'plex'


# ---------------------------------------------------------------------------
# Duplicate stop suppression
# ---------------------------------------------------------------------------


def test_handle_webhook_first_stop_returns_message() -> None:
  """The first media.stop in a session is always processed."""
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert isinstance(result, _mod.WebhookMessage)


def test_handle_webhook_duplicate_stop_returns_none() -> None:
  """A second media.stop with no intervening play/resume is silently discarded."""
  _plex.handle_webhook({'event': 'media.stop'})
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is None


def test_handle_webhook_stop_after_play_resets_and_fires() -> None:
  """media.play clears _stop_processed so the next stop is processed normally."""
  _plex.handle_webhook({'event': 'media.stop'})
  _plex.handle_webhook(_episode_payload('media.play'))
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert isinstance(result, _mod.WebhookMessage)


def test_handle_webhook_stop_after_resume_resets_and_fires() -> None:
  """media.resume clears _stop_processed so the next stop is processed normally."""
  _plex.handle_webhook({'event': 'media.stop'})
  _plex.handle_webhook(_episode_payload('media.resume'))
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert isinstance(result, _mod.WebhookMessage)


def test_handle_webhook_pause_does_not_set_stop_processed() -> None:
  """media.pause is informational and must not suppress a subsequent media.stop."""
  _plex.handle_webhook(_episode_payload('media.pause'))
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert isinstance(result, _mod.WebhookMessage)
