# birthdays.json

Two birthday templates, both requiring iCloud CardDAV:

- **`today`** — upcoming birthdays from contacts, shown once daily at 9am.
  Social — priority 6.
- **`self`** — prominent happy birthday display on the board owner's own
  birthday, shown at 8am (priority 9). The owner is suppressed from the
  `today` list — their birthday is shown exclusively via `self`. Personal —
  priority 9.

Omit `carddav_url` from `[calendar]` to disable both templates entirely.

## Schedule

### `today` — other birthdays

**Cron:** `0 9 * * *` — fires daily at 09:00.
**Hold:** 600 s | **Timeout:** 3600 s | **Priority:** 6 (Social)

Fires in the `9:00` slot alongside `weather` (pri 5, 600 s) — combined
budget 1200 s. At priority 6 `today` queues behind weather but shows before
any lower-priority templates. No-op when no contacts have a birthday today.

### `self` — your birthday

**Cron:** `0 8 * * *` — fires daily at 08:00.
**Hold:** 3600 s | **Timeout:** 7200 s | **Priority:** 9 (Personal)

No-op on non-birthday days (integration returns early). On your birthday,
`self` wins the 8:00 queue at priority 9 and holds the display for 1 h. This
intentionally causes lower-priority same-slot templates (Trakt calendar,
timeout 1800 s) to expire — on your birthday the display is yours. Priority-9
personal-tier templates are excluded from the shared slot budget check.

To override schedule fields without editing this file:

```toml
[birthdays.schedules.today]
hold = 300
priority = 5
disabled = true   # skip entirely

[birthdays.schedules.self]
hold = 1800       # shorter birthday hold
```

## Configuration

```toml
[calendar]
carddav_url = "https://contacts.icloud.com/"
username = "you@icloud.com"
password = "xxxx-xxxx-xxxx-xxxx"
# birthdays_lookahead_days = 7
```

| Key | Required | Description |
|---|---|---|
| `carddav_url` | Yes | CardDAV server URL. iCloud: `https://contacts.icloud.com/` |
| `username` | Yes | Apple ID email address (shared with CalDAV mode if both are enabled) |
| `password` | Yes | App-specific password. Generate at https://appleid.apple.com → Security → App-Specific Passwords. |
| `birthdays_lookahead_days` | No | How many days ahead to show birthdays in `today`. Default: `7`. |

The `username` and `password` keys are shared with CalDAV calendar mode — if
both are configured under `[calendar]`, one set of credentials serves both.

The `self` template requires iCloud CardDAV specifically — it uses Apple's
CalendarServer me-card extension (`{http://calendarserver.org/ns/}me-card`)
to identify the board owner. See issue #393 for planned Google support.

## Display format

**`today` template:**
```
❤️ BIRTHDAYS
ADAM TODAY
BRIANNA FRI
```

Each line shows the contact's first name and either `TODAY` or a 3-letter
weekday abbreviation (`MON`–`SUN`). Lines are sorted: today first, then
ascending days ahead, then alphabetically by name.

**`self` template:**
```
HAPPY BIRTHDAY
YOURNAME! ❤️
```

On the Note (3×15), both lines fit for most first names. If your name and `!`
fill the full row, `❤️` wraps to row 3.

## Keeping data current

Birthdays are read live from iCloud Contacts — no hardcoded data to maintain.
App-specific passwords do not expire but can be revoked at
https://appleid.apple.com → Security → App-Specific Passwords. If the
password is revoked, generate a new one and update `config.toml`.
