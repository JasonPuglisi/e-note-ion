import threading
from typing import Any
from unittest.mock import patch

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
def _reset_plex_state(monkeypatch: pytest.MonkeyPatch) -> None:
  """Reset _state and cancel any pending stop timer before each test."""
  if _plex._pending_stop_timer is not None:
    _plex._pending_stop_timer.cancel()
  monkeypatch.setattr(_plex, '_state', _plex._State.IDLE)
  monkeypatch.setattr(_plex, '_pending_stop_timer', None)


@pytest.fixture()
def _plex_playing(monkeypatch: pytest.MonkeyPatch) -> None:
  """Set state to PLAYING and board tag to 'plex' — simulates active session."""
  monkeypatch.setattr(_plex, '_state', _plex._State.PLAYING)


@pytest.fixture()
def _plex_paused(monkeypatch: pytest.MonkeyPatch) -> None:
  """Set state to PAUSED and board tag to 'plex' — simulates paused session."""
  monkeypatch.setattr(_plex, '_state', _plex._State.PAUSED)


@pytest.fixture(autouse=True)
def _board_shows_plex() -> Any:
  """Default: board tag is 'plex' so board-displacement checks pass."""
  with patch('scheduler.current_hold_tag', return_value='plex'):
    yield


# ---------------------------------------------------------------------------
# Helper: invoke stop timer callback synchronously without waiting
# ---------------------------------------------------------------------------


def _fire_stop_timer() -> None:
  """Cancel the pending stop timer and invoke its callback synchronously."""
  timer = _plex._pending_stop_timer
  assert timer is not None, '_pending_stop_timer was not set'
  timer.cancel()
  timer.function(*timer.args, **timer.kwargs)


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


def test_handle_webhook_play_transitions_state_to_playing() -> None:
  _plex.handle_webhook(_episode_payload('media.play'))
  assert _plex._state == _plex._State.PLAYING


def test_handle_webhook_resume_transitions_state_to_playing() -> None:
  _plex.handle_webhook(_episode_payload('media.resume'))
  assert _plex._state == _plex._State.PLAYING


def test_handle_webhook_play_always_fires_regardless_of_board(monkeypatch: pytest.MonkeyPatch) -> None:
  """play fires even when the board is showing non-Plex content."""
  with patch('scheduler.current_hold_tag', return_value=''):
    result = _plex.handle_webhook(_episode_payload('media.play'))
  assert isinstance(result, _mod.WebhookMessage)


def test_handle_webhook_resume_always_fires_regardless_of_board() -> None:
  """resume fires even when the board is showing non-Plex content."""
  with patch('scheduler.current_hold_tag', return_value=''):
    result = _plex.handle_webhook(_episode_payload('media.resume'))
  assert isinstance(result, _mod.WebhookMessage)


# ---------------------------------------------------------------------------
# pause → paused
# ---------------------------------------------------------------------------


def test_handle_webhook_pause_returns_indefinite_paused_yellow(_plex_playing: None) -> None:
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert isinstance(result, _mod.WebhookMessage)
  assert result.indefinite is True
  assert result.interrupt is True
  assert '[Y] NOW PLAYING' in str(result.data['templates'])


def test_handle_webhook_pause_transitions_state_to_paused(_plex_playing: None) -> None:
  _plex.handle_webhook(_episode_payload('media.pause'))
  assert _plex._state == _plex._State.PAUSED


def test_handle_webhook_pause_in_idle_returns_none() -> None:
  """pause is invalid from IDLE — no session to pause."""
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert result is None


def test_handle_webhook_pause_in_idle_does_not_change_state() -> None:
  _plex.handle_webhook(_episode_payload('media.pause'))
  assert _plex._state == _plex._State.IDLE


def test_handle_webhook_pause_in_paused_returns_none(_plex_paused: None) -> None:
  """pause is a no-op when already paused."""
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert result is None


def test_handle_webhook_pause_when_board_displaced_returns_none(_plex_playing: None) -> None:
  """pause suppressed when Plex hold has been displaced by other content."""
  with patch('scheduler.current_hold_tag', return_value=''):
    result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert result is None


def test_handle_webhook_pause_when_board_displaced_still_transitions_state(_plex_playing: None) -> None:
  """State transitions to PAUSED even when board check suppresses the message."""
  with patch('scheduler.current_hold_tag', return_value=''):
    _plex.handle_webhook(_episode_payload('media.pause'))
  assert _plex._state == _plex._State.PAUSED


# ---------------------------------------------------------------------------
# stop → debounced stopped card
# ---------------------------------------------------------------------------


def test_handle_webhook_stop_returns_none_and_starts_timer(_plex_playing: None) -> None:
  """stop returns None immediately — the stopped card is enqueued via timer."""
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is None
  assert _plex._pending_stop_timer is not None


def test_handle_webhook_stop_transitions_state_to_idle(_plex_playing: None) -> None:
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._state == _plex._State.IDLE


def test_handle_webhook_stop_from_paused_starts_timer(_plex_paused: None) -> None:
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is None
  assert _plex._pending_stop_timer is not None


def test_handle_webhook_stop_from_paused_transitions_state_to_idle(_plex_paused: None) -> None:
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._state == _plex._State.IDLE


def test_handle_webhook_stop_in_idle_returns_none() -> None:
  """stop is invalid from IDLE — no session to stop."""
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is None


def test_handle_webhook_stop_in_idle_does_not_change_state() -> None:
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._state == _plex._State.IDLE


def test_handle_webhook_stop_in_idle_does_not_start_timer() -> None:
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._pending_stop_timer is None


def test_handle_webhook_stop_timer_enqueues_stopped_card(_plex_playing: None) -> None:
  """When the debounce timer fires, enqueue() is called with stopped card data."""
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook({'event': 'media.stop'})
    _fire_stop_timer()
  mock_enqueue.assert_called_once()
  kwargs = mock_enqueue.call_args.kwargs
  assert '[R] NOW PLAYING' in str(kwargs['data']['templates'])
  assert kwargs['supersede_tag'] == 'plex'


def test_handle_webhook_stop_timer_fires_hold_interrupt(_plex_playing: None) -> None:
  """When the debounce timer fires, fire_hold_interrupt is called."""
  with patch('scheduler.enqueue'), patch('scheduler.fire_hold_interrupt') as mock_interrupt:
    _plex.handle_webhook({'event': 'media.stop'})
    _fire_stop_timer()
  mock_interrupt.assert_called_once_with(supersede_tag='plex')


def test_handle_webhook_stop_timer_has_finite_hold(_plex_playing: None) -> None:
  """Stopped card uses a finite hold (not indefinite)."""
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook({'event': 'media.stop'})
    _fire_stop_timer()
  kwargs = mock_enqueue.call_args.kwargs
  assert kwargs['hold'] > 0
  assert kwargs['timeout'] > 0
  assert kwargs.get('indefinite', False) is False


def test_handle_webhook_stop_timer_includes_episode_metadata(_plex_playing: None) -> None:
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook(_episode_payload('media.stop'))
    _fire_stop_timer()
  data = mock_enqueue.call_args.kwargs['data']
  assert data['variables']['show_name'] == [['THE BEAR']]
  assert data['variables']['episode_line'] == [['S2E1 BEEF']]


def test_handle_webhook_stop_timer_includes_movie_metadata(_plex_playing: None) -> None:
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook(_movie_payload('media.stop', 'Inception'))
    _fire_stop_timer()
  data = mock_enqueue.call_args.kwargs['data']
  assert data['variables']['show_name'] == [['INCEPTION']]
  assert data['variables']['episode_line'] == [['']]


def test_handle_webhook_stop_timer_no_metadata_uses_bare_card(_plex_playing: None) -> None:
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook({'event': 'media.stop'})
    _fire_stop_timer()
  data = mock_enqueue.call_args.kwargs['data']
  assert data['variables'] == {}


def test_handle_webhook_stop_timer_skips_if_state_not_idle(_plex_playing: None) -> None:
  """If state changed before timer fires (another play arrived), skip enqueue."""
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook({'event': 'media.stop'})
    _plex._state = _plex._State.PLAYING
    _fire_stop_timer()
  mock_enqueue.assert_not_called()


def test_handle_webhook_stop_timer_skips_if_board_displaced(_plex_playing: None) -> None:
  """If board no longer shows plex when timer fires, skip enqueue."""
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook({'event': 'media.stop'})
    with patch('scheduler.current_hold_tag', return_value=''):
      _fire_stop_timer()
  mock_enqueue.assert_not_called()


def test_handle_webhook_stop_when_board_displaced_returns_none(_plex_playing: None) -> None:
  """stop suppressed when Plex hold has been displaced by other content."""
  with patch('scheduler.current_hold_tag', return_value=''):
    result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is None
  assert _plex._pending_stop_timer is None


def test_handle_webhook_stop_when_board_displaced_still_transitions_to_idle(_plex_playing: None) -> None:
  """State transitions to IDLE even when board check suppresses the message."""
  with patch('scheduler.current_hold_tag', return_value=''):
    _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._state == _plex._State.IDLE


def test_handle_webhook_play_after_displaced_stop_fires(_plex_playing: None) -> None:
  """play always fires — even after a stop was suppressed due to displacement."""
  with patch('scheduler.current_hold_tag', return_value=''):
    _plex.handle_webhook({'event': 'media.stop'})
  with patch('scheduler.current_hold_tag', return_value=''):
    result = _plex.handle_webhook(_episode_payload('media.play'))
  assert isinstance(result, _mod.WebhookMessage)


# ---------------------------------------------------------------------------
# Debounce: stop followed by play/resume cancels the timer
# ---------------------------------------------------------------------------


def test_stop_followed_by_play_within_window_cancels_timer(_plex_playing: None) -> None:
  """play arriving before the stop timer fires cancels the stopped card."""
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._pending_stop_timer is not None
  with patch('scheduler.enqueue') as mock_enqueue:
    _plex.handle_webhook(_episode_payload('media.play'))
  # Timer is cleared from module state and its function will not be called.
  assert _plex._pending_stop_timer is None
  mock_enqueue.assert_not_called()


def test_stop_followed_by_resume_within_window_cancels_timer(_plex_playing: None) -> None:
  """resume arriving before the stop timer fires cancels the stopped card."""
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._pending_stop_timer is not None
  _plex.handle_webhook(_episode_payload('media.resume'))
  assert _plex._pending_stop_timer is None


# ---------------------------------------------------------------------------
# Debounce: configurable window
# ---------------------------------------------------------------------------


def test_stop_debounce_configurable(monkeypatch: pytest.MonkeyPatch, _plex_playing: None) -> None:
  """[plex] stop_debounce = 5 is respected as the timer interval."""
  monkeypatch.setattr(_cfg, '_config', {'plex': {'stop_debounce': 5}})
  captured_interval: list[float] = []
  orig_timer = threading.Timer

  def _spy_timer(interval: float, fn: Any, *args: Any, **kwargs: Any) -> threading.Timer:
    captured_interval.append(interval)
    t = orig_timer(interval, fn, *args, **kwargs)
    return t

  with patch('integrations.plex.threading.Timer', side_effect=_spy_timer):
    _plex.handle_webhook({'event': 'media.stop'})

  assert captured_interval == [5]
  if _plex._pending_stop_timer is not None:
    _plex._pending_stop_timer.cancel()


# ---------------------------------------------------------------------------
# Duplicate stop suppression (replaced by state machine)
# ---------------------------------------------------------------------------


def test_handle_webhook_first_stop_starts_timer(_plex_playing: None) -> None:
  """The first media.stop in a session starts the debounce timer."""
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._pending_stop_timer is not None


def test_handle_webhook_duplicate_stop_returns_none(_plex_playing: None) -> None:
  """A second media.stop with no intervening play/resume is silently discarded."""
  _plex.handle_webhook({'event': 'media.stop'})
  if _plex._pending_stop_timer:
    _plex._pending_stop_timer.cancel()
    _plex._pending_stop_timer = None
  result = _plex.handle_webhook({'event': 'media.stop'})
  assert result is None


def test_handle_webhook_stop_after_play_resets_and_starts_timer() -> None:
  """media.play resets to PLAYING so the next stop starts a timer."""
  _plex.handle_webhook(_episode_payload('media.play'))
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._pending_stop_timer is not None


def test_handle_webhook_stop_after_resume_resets_and_starts_timer() -> None:
  """media.resume resets to PLAYING so the next stop starts a timer."""
  _plex.handle_webhook(_episode_payload('media.resume'))
  _plex.handle_webhook({'event': 'media.stop'})
  assert _plex._pending_stop_timer is not None


def test_handle_webhook_pause_does_not_allow_subsequent_pause(_plex_playing: None) -> None:
  """media.pause transitions to PAUSED; a second pause is a no-op."""
  _plex.handle_webhook(_episode_payload('media.pause'))
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert result is None


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
  assert _vb.display_len(show_name) <= _vb.model.cols
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


def test_handle_webhook_pause_has_supersede_tag(_plex_playing: None) -> None:
  result = _plex.handle_webhook(_episode_payload('media.pause'))
  assert result is not None
  assert result.supersede_tag == 'plex'


def test_handle_webhook_stop_timer_has_supersede_tag(_plex_playing: None) -> None:
  """The stopped card enqueued by the timer carries supersede_tag='plex'."""
  with patch('scheduler.enqueue') as mock_enqueue, patch('scheduler.fire_hold_interrupt'):
    _plex.handle_webhook({'event': 'media.stop'})
    _fire_stop_timer()
  assert mock_enqueue.call_args.kwargs['supersede_tag'] == 'plex'
