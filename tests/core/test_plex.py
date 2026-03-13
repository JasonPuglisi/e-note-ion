import threading
from typing import Any
from unittest.mock import patch

import pytest

import config as _cfg
import integrations.plex as _plex
import integrations.tmdb as _tmdb
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
def _clear_tmdb_caches() -> None:
  """Clear TMDb LRU caches before each test to prevent cross-test contamination."""
  _tmdb.get_show_title.cache_clear()
  _tmdb.get_movie_title.cache_clear()
  _tmdb.find_episode_by_tvdb_id.cache_clear()
  _tmdb.find_episode_by_imdb_id.cache_clear()
  _tmdb.search_show_by_title.cache_clear()
  _tmdb.get_episode_by_number.cache_clear()


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
  """A show name longer than model.cols must be ellipsis-truncated, not left to wrap."""
  long_show = 'Star Trek The Next Generation'
  result = _plex.handle_webhook(_episode_payload(show=long_show))
  assert result is not None
  show_name = result.data['variables']['show_name'][0][0]
  upper = long_show.upper()
  assert _vb.display_len(show_name) <= _vb.model.cols
  assert show_name.endswith('...')
  assert upper.startswith(show_name[:-3])


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


# ---------------------------------------------------------------------------
# credential_name
# ---------------------------------------------------------------------------


def test_credential_name_none_accepted() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play'), credential_name=None)
  assert isinstance(result, _mod.WebhookMessage)


def test_credential_name_passed() -> None:
  result = _plex.handle_webhook(_episode_payload('media.play'), credential_name='plex')
  assert isinstance(result, _mod.WebhookMessage)


# ---------------------------------------------------------------------------
# TMDb canonical title lookup
# ---------------------------------------------------------------------------


def _episode_payload_with_guid(tvdb_id: int, show: str = 'MasterChef (US)') -> dict[str, Any]:
  """Return an episode payload with a Plex Metadata.Guid array.

  Reflects the real Plex Series agent structure: tmdb:// is the episode-level
  TMDb ID (not the series ID); tvdb:// is the TVDb episode ID used for lookup.
  """
  return {
    'event': 'media.play',
    'Metadata': {
      'type': 'episode',
      'grandparentTitle': show,
      'parentIndex': 1,
      'index': 3,
      'title': 'The Dish',
      'Guid': [
        {'id': 'tmdb://1800938'},  # episode-level TMDb ID — not the series ID
        {'id': f'tvdb://{tvdb_id}'},
      ],
    },
  }


def _movie_payload_with_guid(tmdb_id: int, title: str = 'Inception') -> dict[str, Any]:
  """Return a movie payload that includes a Plex Metadata.Guid array."""
  return {
    'event': 'media.play',
    'Metadata': {
      'type': 'movie',
      'title': title,
      'Guid': [{'id': f'tmdb://{tmdb_id}'}],
    },
  }


def test_handle_webhook_episode_uses_tmdb_canonical_show_name(monkeypatch: pytest.MonkeyPatch) -> None:
  """TVDb episode ID → find_episode_by_tvdb_id → show_id → get_show_title."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  # find returns (season, episode, title, show_id=40290, tmdb_episode_id=99001)
  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: (1, 3, 'The Dish', 40290, 99001))
  show_calls: list[int] = []

  def _mock_get_show_title(tmdb_id: int) -> str:
    show_calls.append(tmdb_id)
    return 'MasterChef'

  monkeypatch.setattr(_tmdb, 'get_show_title', _mock_get_show_title)
  result = _plex.handle_webhook(_episode_payload_with_guid(tvdb_id=8765432, show='MasterChef (US)'))

  assert result is not None
  assert show_calls == [40290]
  assert result.data['variables']['show_name'] == [['MASTERCHEF']]


def test_handle_webhook_episode_falls_back_when_no_guid(monkeypatch: pytest.MonkeyPatch) -> None:
  """When the payload has no Guid array and title search fails, falls back to grandparentTitle."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  find_calls: list[int] = []
  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: find_calls.append(tvdb_id) or None)
  monkeypatch.setattr(_tmdb, 'search_show_by_title', lambda title: None)

  result = _plex.handle_webhook(_episode_payload('media.play', show='Masterchef'))

  assert result is not None
  assert find_calls == []
  assert result.data['variables']['show_name'] == [['MASTERCHEF']]


def test_handle_webhook_episode_falls_back_when_find_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
  """When find_episode_by_tvdb_id returns None, falls back to grandparentTitle."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: None)

  result = _plex.handle_webhook(_episode_payload_with_guid(tvdb_id=12345, show='Clue'))

  assert result is not None
  assert result.data['variables']['show_name'] == [['CLUE']]


def test_handle_webhook_episode_falls_back_when_tmdb_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
  """When TMDb is not configured, no lookup is attempted even with Guid."""
  monkeypatch.setattr(_cfg, '_config', {})
  find_calls: list[int] = []
  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: find_calls.append(tvdb_id) or None)

  result = _plex.handle_webhook(_episode_payload_with_guid(tvdb_id=8765432, show='Masterchef'))

  assert result is not None
  assert find_calls == []
  assert result.data['variables']['show_name'] == [['MASTERCHEF']]


def test_handle_webhook_movie_uses_tmdb_canonical_title(monkeypatch: pytest.MonkeyPatch) -> None:
  """When TMDb is configured, movie title uses the canonical TMDb title."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  calls: list[int] = []

  def _mock_get_movie_title(tmdb_id: int) -> str:
    calls.append(tmdb_id)
    return 'Inception'

  monkeypatch.setattr(_tmdb, 'get_movie_title', _mock_get_movie_title)
  # Raw title has disambiguation that TMDb canonical doesn't
  result = _plex.handle_webhook(_movie_payload_with_guid(tmdb_id=27205, title='Inception'))

  assert result is not None
  assert calls == [27205]
  assert result.data['variables']['show_name'] == [['INCEPTION']]


def test_handle_webhook_movie_falls_back_when_tmdb_lookup_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
  """When the TMDb lookup returns None, falls back to Plex's native title."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  monkeypatch.setattr(_tmdb, 'get_movie_title', lambda tmdb_id: None)

  # Use a short raw title to avoid truncation masking the fallback
  result = _plex.handle_webhook(_movie_payload_with_guid(tmdb_id=27205, title='Clue'))

  assert result is not None
  assert result.data['variables']['show_name'] == [['CLUE']]


def _episode_payload_with_imdb_guid(imdb_id: str, show: str = 'Jujutsu Kaisen') -> dict[str, Any]:
  """Return an episode payload with imdb:// guid but no tvdb:// guid."""
  return {
    'event': 'media.play',
    'Metadata': {
      'type': 'episode',
      'grandparentTitle': show,
      'parentIndex': 3,
      'index': 4,
      'title': 'Episode 4',
      'Guid': [
        {'id': 'tmdb://6827061'},
        {'id': f'imdb://{imdb_id}'},
      ],
    },
  }


def test_handle_webhook_episode_uses_tmdb_episode_title(monkeypatch: pytest.MonkeyPatch) -> None:
  """TMDb episode title is used instead of Plex's native episode title."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: (1, 51, 'Perfect Preparation', 95479, 6827061))
  monkeypatch.setattr(_tmdb, 'get_show_title', lambda show_id: 'Jujutsu Kaisen')

  result = _plex.handle_webhook(_episode_payload_with_guid(tvdb_id=11547510, show='JUJUTSU KAISEN'))

  assert result is not None
  # S/E ref is always from Plex metadata (parentIndex=1, index=3 in the fixture);
  # only the episode title comes from TMDb.
  assert result.data['variables']['episode_line'] == [['S1E3 PERFECT PREPARATION']]


def test_handle_webhook_episode_falls_back_to_plex_title_when_no_tmdb_ep_title(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """When TMDb returns an empty episode title, Plex's native title is used."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  # empty string title from TMDb (episode exists but title not yet filled in)
  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: (1, 3, '', 40290, 99001))
  monkeypatch.setattr(_tmdb, 'get_show_title', lambda show_id: 'MasterChef')

  result = _plex.handle_webhook(_episode_payload_with_guid(tvdb_id=8765432, show='MasterChef (US)'))

  assert result is not None
  assert result.data['variables']['episode_line'] == [['S1E3 DISH']]  # 'THE' stripped by strip_leading_article


def test_handle_webhook_episode_uses_imdb_fallback_when_no_tvdb_guid(monkeypatch: pytest.MonkeyPatch) -> None:
  """When no tvdb:// guid is present, imdb:// is tried and the TMDb title is used."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  imdb_calls: list[str] = []

  def _mock_find_imdb(imdb_id: str) -> tuple[int, int, str, int, int]:
    imdb_calls.append(imdb_id)
    return (1, 51, 'Perfect Preparation', 95479, 6827061)

  monkeypatch.setattr(_tmdb, 'find_episode_by_tvdb_id', lambda tvdb_id: None)
  monkeypatch.setattr(_tmdb, 'find_episode_by_imdb_id', _mock_find_imdb)
  monkeypatch.setattr(_tmdb, 'get_show_title', lambda show_id: 'Jujutsu Kaisen')

  result = _plex.handle_webhook(_episode_payload_with_imdb_guid('tt39370459', show='JUJUTSU KAISEN'))

  assert result is not None
  assert imdb_calls == ['tt39370459']
  assert result.data['variables']['show_name'] == [['JUJUTSU KAISEN']]
  assert result.data['variables']['episode_line'] == [['S3E4 PERFECT PREPARATION']]


def test_handle_webhook_episode_falls_back_when_no_tvdb_and_no_imdb_guid(monkeypatch: pytest.MonkeyPatch) -> None:
  """When no tvdb://, imdb://, and title search all fail, falls back to Plex's raw title."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  imdb_calls: list[str] = []
  monkeypatch.setattr(_tmdb, 'find_episode_by_imdb_id', lambda imdb_id: imdb_calls.append(imdb_id) or None)
  monkeypatch.setattr(_tmdb, 'search_show_by_title', lambda title: None)

  payload = {
    'event': 'media.play',
    'Metadata': {
      'type': 'episode',
      'grandparentTitle': 'Jujutsu Kaisen',
      'parentIndex': 3,
      'index': 4,
      'title': 'Episode 4',
      'Guid': [{'id': 'tmdb://6827061'}],  # only tmdb://, no tvdb:// or imdb://
    },
  }
  result = _plex.handle_webhook(payload)

  assert result is not None
  assert imdb_calls == []
  assert result.data['variables']['show_name'] == [['JUJUTSU KAISEN']]
  assert result.data['variables']['episode_line'] == [['S3E4 EPISODE 4']]


def test_handle_webhook_episode_uses_title_search_when_no_guid(monkeypatch: pytest.MonkeyPatch) -> None:
  """When no external guid is present, title search + S/E lookup returns TMDb episode title."""
  monkeypatch.setattr(_cfg, '_config', {'tmdb': {'api_read_access_token': 'tok'}})
  monkeypatch.setattr(_tmdb, 'search_show_by_title', lambda title: 95479)
  monkeypatch.setattr(_tmdb, 'get_episode_by_number', lambda show_id, s, e: ('Perfect Preparation', 6827061))
  monkeypatch.setattr(_tmdb, 'get_episode_group_position', lambda show_id, ep_id: None)
  monkeypatch.setattr(_tmdb, 'get_show_title', lambda show_id: 'Jujutsu Kaisen')

  payload = {
    'event': 'media.play',
    'Metadata': {
      'type': 'episode',
      'grandparentTitle': 'JUJUTSU KAISEN',
      'parentIndex': 3,
      'index': 4,
      'title': 'Episode 4',
      'Guid': [],
    },
  }
  result = _plex.handle_webhook(payload)

  assert result is not None
  assert result.data['variables']['show_name'] == [['JUJUTSU KAISEN']]
  assert result.data['variables']['episode_line'] == [['S3E4 PERFECT PREPARATION']]
