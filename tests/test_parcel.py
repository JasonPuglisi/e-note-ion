"""Unit tests for integrations/parcel.py."""

from unittest.mock import MagicMock, patch

import pytest

import integrations.parcel as pc
from exceptions import IntegrationDataUnavailableError
from integrations.http import CacheEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delivery(
  *,
  description: str = 'Test Package',
  status_code: int = 2,
  carrier_code: str = 'usps',
  date_expected: str | None = '2026-03-20',
) -> dict:
  """Build a minimal Parcel delivery dict."""
  d: dict = {
    'description': description,
    'status_code': status_code,
    'carrier_code': carrier_code,
  }
  if date_expected is not None:
    d['date_expected'] = date_expected
  return d


def _api_response(deliveries: list[dict]) -> MagicMock:
  resp = MagicMock()
  resp.json.return_value = {'deliveries': deliveries}
  resp.raise_for_status = MagicMock()
  return resp


def _patched_config() -> dict:
  return {'parcel': {'api_key': 'test-key'}}


# ---------------------------------------------------------------------------
# _carrier_color
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
  'carrier_code,expected',
  [
    ('usps', '[B]'),
    ('USPS', '[B]'),
    ('ups', '[O]'),
    ('fedex', '[V]'),
    ('dhl', '[Y]'),
    ('ontrac', '[B]'),
    ('laser', '[B]'),
    ('amzlus', '[B]'),
    ('amzluk', '[B]'),
    ('amzlde', '[B]'),
    ('unknown_carrier', '[O]'),
    ('', '[O]'),
  ],
)
def test_carrier_color(carrier_code: str, expected: str) -> None:
  assert pc._carrier_color(carrier_code) == expected


# ---------------------------------------------------------------------------
# _detail_line
# ---------------------------------------------------------------------------


def test_detail_out_for_delivery() -> None:
  assert pc._detail_line(4, '2026-03-18') == 'OUT FOR DELIVERY'


def test_detail_today(monkeypatch: pytest.MonkeyPatch) -> None:
  from datetime import date

  monkeypatch.setattr(
    pc,
    'date',
    type(
      'MockDate',
      (),
      {
        'today': staticmethod(lambda: date(2026, 3, 18)),
      },
    ),
  )
  assert pc._detail_line(2, '2026-03-18') == 'TODAY'


def test_detail_tomorrow(monkeypatch: pytest.MonkeyPatch) -> None:
  from datetime import date

  monkeypatch.setattr(
    pc,
    'date',
    type(
      'MockDate',
      (),
      {
        'today': staticmethod(lambda: date(2026, 3, 18)),
      },
    ),
  )
  assert pc._detail_line(2, '2026-03-19') == 'TOMORROW'


def test_detail_in_n_days(monkeypatch: pytest.MonkeyPatch) -> None:
  from datetime import date

  monkeypatch.setattr(
    pc,
    'date',
    type(
      'MockDate',
      (),
      {
        'today': staticmethod(lambda: date(2026, 3, 18)),
      },
    ),
  )
  assert pc._detail_line(2, '2026-03-21') == 'IN 3 DAYS'


def test_detail_datetime_format(monkeypatch: pytest.MonkeyPatch) -> None:
  from datetime import date

  monkeypatch.setattr(
    pc,
    'date',
    type(
      'MockDate',
      (),
      {
        'today': staticmethod(lambda: date(2026, 3, 18)),
      },
    ),
  )
  assert pc._detail_line(2, '2026-03-19 00:00:00') == 'TOMORROW'


def test_detail_no_date() -> None:
  assert pc._detail_line(2, None) == ''


def test_detail_bad_date() -> None:
  assert pc._detail_line(2, 'not-a-date') == ''


def test_detail_past_date(monkeypatch: pytest.MonkeyPatch) -> None:
  from datetime import date

  monkeypatch.setattr(
    pc,
    'date',
    type(
      'MockDate',
      (),
      {
        'today': staticmethod(lambda: date(2026, 3, 18)),
      },
    ),
  )
  assert pc._detail_line(2, '2026-03-16') == 'TODAY'


# ---------------------------------------------------------------------------
# _sort_key
# ---------------------------------------------------------------------------


def test_sort_key_soonest_first() -> None:
  d1 = _delivery(description='Alpha', date_expected='2026-03-21')
  d2 = _delivery(description='Beta', date_expected='2026-03-19')
  assert sorted([d1, d2], key=pc._sort_key) == [d2, d1]


def test_sort_key_alphabetical_tiebreak() -> None:
  d1 = _delivery(description='Zebra', date_expected='2026-03-20')
  d2 = _delivery(description='Alpha', date_expected='2026-03-20')
  assert sorted([d1, d2], key=pc._sort_key) == [d2, d1]


def test_sort_key_null_dates_last() -> None:
  d1 = _delivery(description='No Date', date_expected=None)
  d2 = _delivery(description='Has Date', date_expected='2026-03-25')
  assert sorted([d1, d2], key=pc._sort_key) == [d2, d1]


# ---------------------------------------------------------------------------
# get_variables
# ---------------------------------------------------------------------------


def test_get_variables_out_for_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  pc._cache = None

  resp = _api_response(
    [
      _delivery(
        description='Amazon Order',
        status_code=4,
        carrier_code='amzlus',
        date_expected='2026-03-18',
      )
    ]
  )
  with patch('integrations.parcel.fetch_with_retry', return_value=resp):
    result = pc.get_variables()

  assert result['status_line'] == [['[B] ON THE WAY']]
  assert result['description'] == [['AMAZON ORDER']]
  assert result['detail'] == [['OUT FOR DELIVERY']]
  pc._cache = None


def test_get_variables_no_active_deliveries(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  pc._cache = None

  resp = _api_response([_delivery(status_code=0)])  # completed
  with patch('integrations.parcel.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError, match='no active deliveries'):
      pc.get_variables()

  pc._cache = None


def test_get_variables_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  pc._cache = None

  resp = _api_response([])
  with patch('integrations.parcel.fetch_with_retry', return_value=resp):
    with pytest.raises(IntegrationDataUnavailableError, match='no active deliveries'):
      pc.get_variables()

  pc._cache = None


def test_get_variables_selects_soonest(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  pc._cache = None

  resp = _api_response(
    [
      _delivery(description='Later', date_expected='2026-03-25', carrier_code='ups'),
      _delivery(description='Sooner', date_expected='2026-03-19', carrier_code='fedex'),
    ]
  )
  with patch('integrations.parcel.fetch_with_retry', return_value=resp):
    result = pc.get_variables()

  assert result['description'] == [['SOONER']]
  assert result['status_line'] == [['[V] ON THE WAY']]
  pc._cache = None


def test_get_variables_no_description_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  pc._cache = None

  resp = _api_response([_delivery(description='')])
  with patch('integrations.parcel.fetch_with_retry', return_value=resp):
    result = pc.get_variables()

  assert result['description'] == [['PACKAGE']]
  pc._cache = None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  cached_value: dict = {
    'status_line': [['[B] ON THE WAY']],
    'description': [['CACHED']],
    'detail': [['TODAY']],
  }
  pc._cache = CacheEntry(cached_value)

  with patch('integrations.parcel.fetch_with_retry') as mock_fetch:
    result = pc.get_variables()
    mock_fetch.assert_not_called()

  assert result == cached_value
  pc._cache = None


def test_stale_cache_served_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
  import requests as _requests

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())

  stale_value: dict = {
    'status_line': [['[O] ON THE WAY']],
    'description': [['STALE']],
    'detail': [['TOMORROW']],
  }
  pc._cache = CacheEntry(stale_value)
  # Force cache to appear expired so we attempt a fetch.
  import time

  pc._cache.cached_at = time.monotonic() - pc._CACHE_TTL - 1

  with patch(
    'integrations.parcel.fetch_with_retry',
    side_effect=_requests.RequestException('timeout'),
  ):
    result = pc.get_variables()

  assert result == stale_value
  pc._cache = None


def test_no_cache_on_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  import requests as _requests

  import config as _cfg

  monkeypatch.setattr(_cfg, '_config', _patched_config())
  pc._cache = None

  with patch(
    'integrations.parcel.fetch_with_retry',
    side_effect=_requests.RequestException('timeout'),
  ):
    with pytest.raises(IntegrationDataUnavailableError, match='API request failed'):
      pc.get_variables()
