import time
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
import requests

import integrations.youtube as youtube
from exceptions import IntegrationDataUnavailableError


def _mock_response(data: Any, status_code: int = 200) -> MagicMock:
  resp = MagicMock()
  resp.status_code = status_code
  resp.json.return_value = data
  resp.raise_for_status = MagicMock()
  return resp


@pytest.fixture(autouse=True)
def _mock_config(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'google': {
        'client_id': 'test-client-id',
        'client_secret': 'test-client-secret',
        'access_token': 'test-access-token',
        'refresh_token': 'test-refresh-token',
        'expires_at': int(time.time()) + 3600,
      }
    },
  )
  yield


@pytest.fixture(autouse=True)
def _reset_state() -> Generator[None, None, None]:
  youtube._sub_cache = None
  youtube._vars_cache = None
  yield
  youtube._sub_cache = None
  youtube._vars_cache = None


# ---------------------------------------------------------------------------
# Subscription fetching
# ---------------------------------------------------------------------------

_SUBS_RESPONSE = {
  'items': [
    {'snippet': {'resourceId': {'channelId': 'UC_ch1'}}},
    {'snippet': {'resourceId': {'channelId': 'UC_ch2'}}},
  ],
}


def test_fetch_subscriptions_extracts_channel_ids() -> None:
  resp = _mock_response(_SUBS_RESPONSE)
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    ids = youtube._fetch_subscriptions('token')
  assert ids == ['UC_ch1', 'UC_ch2']


def test_fetch_subscriptions_paginates() -> None:
  page1 = {
    'items': [{'snippet': {'resourceId': {'channelId': 'UC_1'}}}],
    'nextPageToken': 'page2',
  }
  page2 = {
    'items': [{'snippet': {'resourceId': {'channelId': 'UC_2'}}}],
  }
  responses = [_mock_response(page1), _mock_response(page2)]
  with patch('integrations.youtube.fetch_with_retry', side_effect=responses):
    ids = youtube._fetch_subscriptions('token')
  assert ids == ['UC_1', 'UC_2']


def test_fetch_subscriptions_401_raises() -> None:
  resp = MagicMock()
  resp.status_code = 401
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    with pytest.raises(requests.HTTPError):
      youtube._fetch_subscriptions('token')


def test_get_subscriptions_uses_cache() -> None:
  from integrations.http import CacheEntry

  youtube._sub_cache = CacheEntry({'ids': [['UC_cached']]})
  with patch('integrations.youtube._fetch_subscriptions') as mock_fetch:
    ids = youtube._get_subscriptions()
    mock_fetch.assert_not_called()
  assert ids == ['UC_cached']


# ---------------------------------------------------------------------------
# RSS feed parsing
# ---------------------------------------------------------------------------

_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <yt:videoId>vid_001</yt:videoId>
  </entry>
  <entry>
    <yt:videoId>vid_002</yt:videoId>
  </entry>
</feed>"""


def test_fetch_rss_extracts_video_ids() -> None:
  resp = MagicMock()
  resp.status_code = 200
  resp.text = _RSS_XML
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    ids = youtube._fetch_rss_video_ids(['UC_ch1'])
  assert ids == ['vid_001', 'vid_002']


def test_fetch_rss_deduplicates_across_channels() -> None:
  resp = MagicMock()
  resp.status_code = 200
  resp.text = _RSS_XML
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    ids = youtube._fetch_rss_video_ids(['UC_ch1', 'UC_ch2'])
  # Same feed served twice — IDs should be deduplicated.
  assert ids == ['vid_001', 'vid_002']


def test_fetch_rss_skips_failed_feeds() -> None:
  resp_ok = MagicMock()
  resp_ok.status_code = 200
  resp_ok.text = _RSS_XML

  def side_effect(*args: Any, **kwargs: Any) -> MagicMock:
    channel_id = kwargs.get('params', {}).get('channel_id', '')
    if channel_id == 'UC_bad':
      raise requests.ConnectionError('refused')
    return resp_ok

  with patch('integrations.youtube.fetch_with_retry', side_effect=side_effect):
    ids = youtube._fetch_rss_video_ids(['UC_bad', 'UC_good'])
  assert ids == ['vid_001', 'vid_002']


def test_fetch_rss_handles_malformed_xml() -> None:
  resp = MagicMock()
  resp.status_code = 200
  resp.text = '<not valid xml'
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    ids = youtube._fetch_rss_video_ids(['UC_ch1'])
  assert ids == []


def test_fetch_rss_empty_feed() -> None:
  resp = MagicMock()
  resp.status_code = 200
  resp.text = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    ids = youtube._fetch_rss_video_ids(['UC_ch1'])
  assert ids == []


# ---------------------------------------------------------------------------
# Live stream detection
# ---------------------------------------------------------------------------

_VIDEOS_RESPONSE_LIVE = {
  'items': [
    {
      'id': 'vid_001',
      'snippet': {'channelTitle': 'My Channel', 'title': 'The Big Stream'},
      'liveStreamingDetails': {
        'actualStartTime': '2026-03-20T12:00:00Z',
      },
    },
  ],
}

_VIDEOS_RESPONSE_ENDED = {
  'items': [
    {
      'id': 'vid_002',
      'snippet': {'channelTitle': 'Other Channel', 'title': 'Ended Stream'},
      'liveStreamingDetails': {
        'actualStartTime': '2026-03-20T10:00:00Z',
        'actualEndTime': '2026-03-20T11:00:00Z',
      },
    },
  ],
}

_VIDEOS_RESPONSE_NOT_LIVE = {
  'items': [
    {
      'id': 'vid_003',
      'snippet': {'channelTitle': 'Normal Channel', 'title': 'Normal Video'},
    },
  ],
}


def test_check_live_videos_detects_live() -> None:
  resp = _mock_response(_VIDEOS_RESPONSE_LIVE)
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    live = youtube._check_live_videos('token', ['vid_001'])
  assert len(live) == 1
  assert live[0]['channel'] == 'My Channel'
  assert live[0]['title'] == 'The Big Stream'


def test_check_live_videos_excludes_ended() -> None:
  resp = _mock_response(_VIDEOS_RESPONSE_ENDED)
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    live = youtube._check_live_videos('token', ['vid_002'])
  assert len(live) == 0


def test_check_live_videos_excludes_non_live() -> None:
  resp = _mock_response(_VIDEOS_RESPONSE_NOT_LIVE)
  with patch('integrations.youtube.fetch_with_retry', return_value=resp):
    live = youtube._check_live_videos('token', ['vid_003'])
  assert len(live) == 0


def test_check_live_videos_batches() -> None:
  """Verify that video IDs are batched in groups of 50."""
  call_count = 0

  def mock_fetch(*args: Any, **kwargs: Any) -> MagicMock:
    nonlocal call_count
    call_count += 1
    return _mock_response({'items': []})

  ids = [f'vid_{i:03d}' for i in range(75)]  # 75 IDs → 2 batches
  with patch('integrations.youtube.fetch_with_retry', side_effect=mock_fetch):
    youtube._check_live_videos('token', ids)
  assert call_count == 2


# ---------------------------------------------------------------------------
# get_variables — full pipeline
# ---------------------------------------------------------------------------


def test_get_variables_returns_most_recent_stream() -> None:
  subs_resp = _mock_response(_SUBS_RESPONSE)
  rss_resp = MagicMock()
  rss_resp.status_code = 200
  rss_resp.text = _RSS_XML

  videos_resp = _mock_response(
    {
      'items': [
        {
          'id': 'vid_001',
          'snippet': {'channelTitle': 'Old Streamer', 'title': 'Old Stream'},
          'liveStreamingDetails': {'actualStartTime': '2026-03-20T10:00:00Z'},
        },
        {
          'id': 'vid_002',
          'snippet': {'channelTitle': 'New Streamer', 'title': 'New Stream'},
          'liveStreamingDetails': {'actualStartTime': '2026-03-20T14:00:00Z'},
        },
      ],
    }
  )

  def mock_fetch(method: str, url: str, **kwargs: Any) -> MagicMock:
    if '/subscriptions' in url:
      return subs_resp
    if 'feeds/videos.xml' in url:
      return rss_resp
    if '/videos' in url:
      return videos_resp
    return _mock_response({})

  with patch('integrations.youtube.fetch_with_retry', side_effect=mock_fetch):
    result = youtube.get_variables()

  # Most recently started stream should be selected.
  assert result['channel'] == [['NEW STREAMER']]
  assert result['title'] == [['NEW STREAM']]


def test_get_variables_strips_leading_articles() -> None:
  subs_resp = _mock_response({'items': [{'snippet': {'resourceId': {'channelId': 'UC_1'}}}]})
  rss_resp = MagicMock()
  rss_resp.status_code = 200
  rss_resp.text = _RSS_XML

  videos_resp = _mock_response(
    {
      'items': [
        {
          'id': 'vid_001',
          'snippet': {'channelTitle': 'The Gaming Channel', 'title': 'A Great Stream'},
          'liveStreamingDetails': {'actualStartTime': '2026-03-20T12:00:00Z'},
        },
      ],
    }
  )

  def mock_fetch(method: str, url: str, **kwargs: Any) -> MagicMock:
    if '/subscriptions' in url:
      return subs_resp
    if 'feeds/videos.xml' in url:
      return rss_resp
    if '/videos' in url:
      return videos_resp
    return _mock_response({})

  with patch('integrations.youtube.fetch_with_retry', side_effect=mock_fetch):
    result = youtube.get_variables()

  assert result['channel'] == [['GAMING CHANNEL']]
  assert result['title'] == [['GREAT STREAM']]


def test_get_variables_no_live_streams_raises() -> None:
  subs_resp = _mock_response(_SUBS_RESPONSE)
  rss_resp = MagicMock()
  rss_resp.status_code = 200
  rss_resp.text = _RSS_XML
  videos_resp = _mock_response({'items': []})

  def mock_fetch(method: str, url: str, **kwargs: Any) -> MagicMock:
    if '/subscriptions' in url:
      return subs_resp
    if 'feeds/videos.xml' in url:
      return rss_resp
    if '/videos' in url:
      return videos_resp
    return _mock_response({})

  with patch('integrations.youtube.fetch_with_retry', side_effect=mock_fetch):
    with pytest.raises(IntegrationDataUnavailableError, match='no subscribed channels are live'):
      youtube.get_variables()


def test_get_variables_no_subscriptions_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(
    _cfg,
    '_config',
    {
      'google': {
        'client_id': 'id',
        'client_secret': 'secret',
        'access_token': 'token',
        'refresh_token': 'refresh',
        'expires_at': int(time.time()) + 3600,
      }
    },
  )

  subs_resp = _mock_response({'items': []})
  with patch('integrations.youtube.fetch_with_retry', return_value=subs_resp):
    with pytest.raises(IntegrationDataUnavailableError, match='no subscriptions'):
      youtube.get_variables()


def test_get_variables_no_rss_videos_raises() -> None:
  subs_resp = _mock_response(_SUBS_RESPONSE)
  rss_resp = MagicMock()
  rss_resp.status_code = 200
  rss_resp.text = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

  def mock_fetch(method: str, url: str, **kwargs: Any) -> MagicMock:
    if '/subscriptions' in url:
      return subs_resp
    return rss_resp

  with patch('integrations.youtube.fetch_with_retry', side_effect=mock_fetch):
    with pytest.raises(IntegrationDataUnavailableError, match='no recent videos'):
      youtube.get_variables()


def test_get_variables_api_failure_uses_cache() -> None:
  from integrations.http import CacheEntry

  youtube._vars_cache = CacheEntry(
    {
      'channel': [['CACHED CHANNEL']],
      'title': [['CACHED TITLE']],
    }
  )
  youtube._sub_cache = CacheEntry({'ids': [['UC_1']]})

  rss_resp = MagicMock()
  rss_resp.status_code = 200
  rss_resp.text = _RSS_XML

  def mock_fetch(method: str, url: str, **kwargs: Any) -> MagicMock:
    if 'feeds/videos.xml' in url:
      return rss_resp
    if '/videos' in url:
      raise requests.ConnectionError('timeout')
    return _mock_response({})

  with patch('integrations.youtube.fetch_with_retry', side_effect=mock_fetch):
    result = youtube.get_variables()
  assert result['channel'] == [['CACHED CHANNEL']]
