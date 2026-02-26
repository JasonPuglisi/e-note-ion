from typing import Any

import pytest

import config as _cfg
import integrations.plex as _plex
import scheduler as _mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _episode_payload(event: str = 'media.play', title: str = 'The Beef') -> dict[str, Any]:
  """Return a minimal Plex webhook payload for an episode event."""
  return {
    'event': event,
    'Metadata': {
      'type': 'episode',
      'grandparentTitle': 'The Bear',
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


def test_handle_webhook_pause_returns_indefinite_paused() -> None:
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert isinstance(result, _mod.WebhookMessage)
  assert result.indefinite is True
  assert result.interrupt is True
  assert 'PAUSED' in str(result.data['templates'])


# ---------------------------------------------------------------------------
# stop → interrupt_only
# ---------------------------------------------------------------------------


def test_handle_webhook_stop_returns_interrupt_only() -> None:
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert isinstance(result, _mod.WebhookMessage)
  assert result.interrupt_only is True
  assert result.indefinite is False


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


def test_handle_webhook_non_video_type_returns_none() -> None:
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


def test_handle_webhook_movie_has_empty_episode_detail() -> None:
  result = _plex.handle_webhook(_movie_payload('media.play', 'Inception'))
  assert result is not None
  variables = result.data['variables']
  assert variables['episode_detail'] == [['']]
  assert variables['show_name'] == [['INCEPTION']]


def test_handle_webhook_movie_has_empty_episode_ref() -> None:
  result = _plex.handle_webhook(_movie_payload('media.play', 'Inception'))
  assert result is not None
  assert result.data['variables']['episode_ref'] == [['']]


# ---------------------------------------------------------------------------
# Article stripping (episode titles only)
# ---------------------------------------------------------------------------


def test_handle_webhook_episode_strips_article_from_episode_title() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play', title='The Beef'))
  assert result is not None
  assert result.data['variables']['episode_detail'] == [['BEEF']]


def test_handle_webhook_episode_strips_a_article() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play', title='A New Hope'))
  assert result is not None
  assert result.data['variables']['episode_detail'] == [['NEW HOPE']]


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
