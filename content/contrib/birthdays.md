# birthdays.json

Upcoming birthdays from iCloud Contacts, shown once daily at 9am. Displays
contacts whose birthday falls today or within the lookahead window, one per
line. Requires the same iCloud app-specific password as the CalDAV calendar
integration.

Omit `carddav_url` from `[calendar]` to disable this integration entirely.

## Configuration

Add the following to your `[calendar]` section in `config.toml`:

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
| `birthdays_lookahead_days` | No | How many days ahead to show birthdays. Default: `7`. |

The `username` and `password` keys are shared with CalDAV calendar mode — if
both are configured under `[calendar]`, one set of credentials serves both.

## Display format

```
❤️ BIRTHDAYS
ADAM TODAY
BRIANNA FRI
```

Each line shows the contact's first name and either `TODAY` or a 3-letter
weekday abbreviation (`MON`–`SUN`). Lines are sorted: today first, then
ascending days ahead, then alphabetically by name.

## Keeping data current

Birthdays are read live from iCloud Contacts — no hardcoded data to maintain.
App-specific passwords do not expire but can be revoked at
https://appleid.apple.com → Security → App-Specific Passwords. If the
password is revoked, generate a new one and update `config.toml`.
