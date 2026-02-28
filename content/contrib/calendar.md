# calendar.json

Today's calendar events, updated every half-hour. Shows timed events (sorted
soonest first) then all-day events. Supports multiple calendars with optional
per-calendar color squares.

Both modes can be active simultaneously — events from ICS feeds and CalDAV
are merged and sorted together.

- **ICS mode**: public or secret-address `.ics` feeds (Google Calendar, iCloud
  public link, any provider). No authentication required.
- **CalDAV mode**: private iCloud calendars via app-specific password.

## Configuration

### ICS mode

```toml
[calendar]
urls = [
  "https://calendar.google.com/calendar/ical/.../basic.ics",
]
# colors = ["B", "G"]
```

| Key | Required | Description |
|---|---|---|
| `urls` | Yes | One or more public `.ics` URLs. Google: Settings → calendar → "Secret address in iCal format". iCloud: share → enable "Public Calendar" → copy link (replace `webcal://` with `https://`). |
| `colors` | No | Vestaboard color letter per URL, parallel to `urls`. Options: `R` `O` `Y` `G` `B` `V` `W` `K`. iCloud feeds include the calendar color automatically — omit `colors` to use it. |

iCloud public feeds include `X-APPLE-CALENDAR-COLOR` in the feed itself, so
the calendar's color is auto-detected with no config. Google feeds include no
color; assign one via `colors` or omit for no color prefix.

### CalDAV mode (private iCloud)

```toml
[calendar]
caldav_url = "https://caldav.icloud.com/"
username = "you@icloud.com"
password = "xxxx-xxxx-xxxx-xxxx"
# calendar_names = ["Work", "Personal"]
```

| Key | Required | Description |
|---|---|---|
| `caldav_url` | Yes | CalDAV server URL. iCloud: `https://caldav.icloud.com/` |
| `username` | Yes | Apple ID email address |
| `password` | Yes | App-specific password. Generate at https://appleid.apple.com → Security → App-Specific Passwords. Required when your Apple ID has two-factor authentication. |
| `calendar_names` | No | List of calendar names to include. Default: all calendars. Order controls tie-breaking when two events start at the same time. |

Calendar colors are read automatically from each calendar's CalDAV properties
(`apple:calendar-color`) and mapped to the nearest Vestaboard color tag.

> **Note on Google CalDAV:** Google requires OAuth 2.0 for CalDAV as of
> March 2025 — use the ICS secret-address URL for Google Calendar instead.

### Both modes together

```toml
[calendar]
urls = ["https://calendar.google.com/calendar/ical/.../basic.ics"]
colors = ["B"]
caldav_url = "https://caldav.icloud.com/"
username = "you@icloud.com"
password = "xxxx-xxxx-xxxx-xxxx"
```

Events from all sources are merged and sorted together. ICS events sort before
CalDAV events when start times tie.

## Color mapping

When a calendar color is present (auto-detected or configured), each event
line is prefixed with the corresponding Vestaboard color square:

```
[B] 14:30 TEAM MEETING
[G] PROJECT DEADLINE
```

The nearest Vestaboard color is chosen by Euclidean distance in RGB space from
the calendar's hex color value.

## Keeping data current

### iCloud `.ics` URLs

If you re-share or disable public sharing on an iCloud calendar, the URL
changes. Update `urls` in `config.toml` with the new link.

### CalDAV

App-specific passwords do not expire but can be revoked at
https://appleid.apple.com → Security → App-Specific Passwords. If the
password is revoked, generate a new one and update `config.toml`.
