from datetime import date
from unittest.mock import MagicMock, patch

import pytest

import config as _config_mod
from integrations.calendar import _resolve_display_name

# ── _resolve_display_name ──────────────────────────────────────────────────────


def test_resolve_display_name_prefers_nickname() -> None:
  assert _resolve_display_name('Jay', ['Jason', 'Puglisi']) == 'JAY'


def test_resolve_display_name_nickname_strips_whitespace() -> None:
  assert _resolve_display_name('  Jay  ', ['Jason', 'Puglisi']) == 'JAY'


def test_resolve_display_name_empty_nickname_falls_through() -> None:
  assert _resolve_display_name('', ['Jason', 'Puglisi']) == 'JASON P'


def test_resolve_display_name_whitespace_nickname_falls_through() -> None:
  assert _resolve_display_name('   ', ['Jason', 'Puglisi']) == 'JASON P'


def test_resolve_display_name_none_nickname_falls_through() -> None:
  assert _resolve_display_name(None, ['Jason', 'Puglisi']) == 'JASON P'


def test_resolve_display_name_first_last_initial() -> None:
  assert _resolve_display_name(None, ['Jane', 'Smith']) == 'JANE S'


def test_resolve_display_name_single_name_no_initial() -> None:
  assert _resolve_display_name(None, ['Cher']) == 'CHER'


def test_resolve_display_name_empty_parts_returns_empty() -> None:
  assert _resolve_display_name(None, []) == ''


def test_resolve_display_name_uppercases_nickname() -> None:
  assert _resolve_display_name('jay', ['Jason', 'Puglisi']) == 'JAY'


def test_resolve_display_name_uppercases_fn() -> None:
  assert _resolve_display_name(None, ['jason', 'puglisi']) == 'JASON P'


# ── _fetch_birthday_contacts vCard parsing ─────────────────────────────────────

# Minimal CardDAV REPORT response with one contact.
_CARDDAV_RESPONSE_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<multistatus xmlns="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <response>
    <propstat>
      <prop>
        <card:address-data>{vcard}</card:address-data>
      </prop>
    </propstat>
  </response>
</multistatus>"""

_VCARD_NICKNAME = 'BEGIN:VCARD\r\nFN:Jason Puglisi\r\nNICKNAME:Jay\r\nBDAY:1990-06-15\r\nEND:VCARD\r\n'

_VCARD_NO_NICKNAME = 'BEGIN:VCARD\r\nFN:Jason Puglisi\r\nBDAY:1990-06-15\r\nEND:VCARD\r\n'

_VCARD_SINGLE_NAME = 'BEGIN:VCARD\r\nFN:Cher\r\nBDAY:1946-05-20\r\nEND:VCARD\r\n'


def _make_mock_response(vcard: str) -> MagicMock:
  xml = _CARDDAV_RESPONSE_TEMPLATE.format(vcard=vcard).encode()
  mock = MagicMock()
  mock.content = xml
  return mock


@patch('integrations.calendar._birthday_cache', None)
@patch('integrations.calendar.requests.request')
def test_fetch_birthday_contacts_uses_nickname(mock_req: MagicMock) -> None:
  from integrations.calendar import _fetch_birthday_contacts

  mock_req.return_value = _make_mock_response(_VCARD_NICKNAME)
  contacts = _fetch_birthday_contacts('http://example.com/ab/', 'user', 'pass')

  assert len(contacts) == 1
  display_name, month, day = contacts[0]
  assert display_name == 'JAY'
  assert month == 6
  assert day == 15


@patch('integrations.calendar._birthday_cache', None)
@patch('integrations.calendar.requests.request')
def test_fetch_birthday_contacts_first_last_initial(mock_req: MagicMock) -> None:
  from integrations.calendar import _fetch_birthday_contacts

  mock_req.return_value = _make_mock_response(_VCARD_NO_NICKNAME)
  contacts = _fetch_birthday_contacts('http://example.com/ab/', 'user', 'pass')

  assert len(contacts) == 1
  display_name, _, _ = contacts[0]
  assert display_name == 'JASON P'


@patch('integrations.calendar._birthday_cache', None)
@patch('integrations.calendar.requests.request')
def test_fetch_birthday_contacts_single_name(mock_req: MagicMock) -> None:
  from integrations.calendar import _fetch_birthday_contacts

  mock_req.return_value = _make_mock_response(_VCARD_SINGLE_NAME)
  contacts = _fetch_birthday_contacts('http://example.com/ab/', 'user', 'pass')

  assert len(contacts) == 1
  display_name, _, _ = contacts[0]
  assert display_name == 'CHER'


# ── get_variables_birthdays display format ─────────────────────────────────────


@patch('integrations.calendar._resolve_self_contact', return_value=None)
@patch('integrations.calendar._get_addressbook_url', return_value='http://x/')
@patch('integrations.calendar._fetch_birthday_contacts')
def test_get_variables_birthdays_today_format(
  mock_fetch: MagicMock,
  _mock_ab: MagicMock,
  _mock_self: MagicMock,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import integrations.calendar as cal

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {'carddav_url': 'x', 'username': 'u', 'password': 'p'}})
  today = date.today()
  mock_fetch.return_value = [('JAY', today.month, today.day)]

  with patch.object(cal, '_get_now', return_value=MagicMock(date=lambda: today)):
    result = cal.get_variables_birthdays()

  lines = result['birthdays'][0]
  assert lines == ['TODAY JAY']


@patch('integrations.calendar._resolve_self_contact', return_value=None)
@patch('integrations.calendar._get_addressbook_url', return_value='http://x/')
@patch('integrations.calendar._fetch_birthday_contacts')
def test_get_variables_birthdays_future_format(
  mock_fetch: MagicMock,
  _mock_ab: MagicMock,
  _mock_self: MagicMock,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import integrations.calendar as cal

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {'carddav_url': 'x', 'username': 'u', 'password': 'p'}})
  # Use a fixed "today" of 2026-03-14 (Saturday) and a birthday 2 days later (Monday).
  fixed_today = date(2026, 3, 14)
  mock_fetch.return_value = [('JANE S', 3, 16)]  # March 16 = Monday

  with patch.object(cal, '_get_now', return_value=MagicMock(date=lambda: fixed_today)):
    result = cal.get_variables_birthdays()

  lines = result['birthdays'][0]
  assert lines == ['MON JANE S']


@patch('integrations.calendar._resolve_self_contact', return_value=None)
@patch('integrations.calendar._get_addressbook_url', return_value='http://x/')
@patch('integrations.calendar._fetch_birthday_contacts')
def test_get_variables_birthdays_sorted_by_days_ahead(
  mock_fetch: MagicMock,
  _mock_ab: MagicMock,
  _mock_self: MagicMock,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import integrations.calendar as cal

  monkeypatch.setattr(_config_mod, '_config', {'calendar': {'carddav_url': 'x', 'username': 'u', 'password': 'p'}})
  fixed_today = date(2026, 3, 14)
  # Two contacts: one in 3 days, one in 1 day.
  mock_fetch.return_value = [('FAR A', 3, 17), ('NEAR B', 3, 15)]

  with patch.object(cal, '_get_now', return_value=MagicMock(date=lambda: fixed_today)):
    result = cal.get_variables_birthdays()

  lines = result['birthdays'][0]
  # NEAR B (1 day) should come before FAR A (3 days).
  assert lines[0].endswith('NEAR B')
  assert lines[1].endswith('FAR A')


# ── get_variables_self_birthday display name ───────────────────────────────────


@patch('integrations.calendar._resolve_self_contact')
def test_get_variables_self_birthday_uses_display_name(
  mock_self: MagicMock,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  import integrations.calendar as cal

  today = date.today()
  monkeypatch.setattr(_config_mod, '_config', {'calendar': {'carddav_url': 'x', 'username': 'u', 'password': 'p'}})
  mock_self.return_value = ('JAY', today.month, today.day)

  with patch.object(cal, '_get_now', return_value=MagicMock(date=lambda: today)):
    result = cal.get_variables_self_birthday()

  assert result == {'name': [['JAY']]}
