# trakt.json

Trakt.tv integration — shows the next upcoming episode from your calendar,
the next unwatched episode for your most recently watched show, and what you
are currently watching. Calendar and on-deck each fire twice daily; now-playing
polls every 3 minutes and only shows when something is actively playing.
Entertainment — three templates spanning background context to real-time.

## Schedule

### `calendar` — next airing episode

**Cron:** `0 8,16 * * *` — fires at 08:00 and 16:00 daily.
**Hold:** 1200 s | **Timeout:** 1800 s | **Priority:** 4 (Entertainment)

Private template. Fires in the `:00` slot alongside `weather` (pri 5, 600 s) —
combined budget 1800 s, exactly at the ceiling. Weather shows first; Trakt
calendar follows for 20 min. The 1800 s timeout lets it survive the 600 s
weather hold with 1200 s to spare.

### `next_up` — next unwatched episode

**Cron:** `0 12,20 * * *` — fires at 12:00 and 20:00 daily.
**Hold:** 1200 s | **Timeout:** 1800 s | **Priority:** 4 (Entertainment)

Private template. Same slot pattern as `calendar` — fires at `:00` hours that
don't overlap with `calendar` (noon = planning context; 20:00 = settling in
for the evening). Combined budget with weather is 1800 s at both hours.

### `watching` — now playing

**Cron:** `*/3 7-23 * * *` — polls every 3 minutes from 07:00 to 23:00.
**Hold:** 180 s | **Timeout:** 120 s | **Priority:** 7 (Entertainment)
**Refresh interval:** 30 s

Private template. No-op when nothing is playing. The 120 s timeout (shorter
than the 180 s hold) means a queued `watching` message is discarded if it
waits more than 2 minutes — stale now-playing data is worse than nothing.
Polling stops at 23:00 to avoid unnecessary overnight API calls.

For movies, the title word-wraps into rows 2–3 (with ellipsis on the second
row only if it overflows). Episodes use row 3 for the season/episode
reference and title as before.

To override schedule fields without editing this file:

```toml
[trakt.schedules.calendar]
disabled = true        # skip entirely

[trakt.schedules.next_up]
cron = "0 11,19 * * *" # shift windows earlier

[trakt.schedules.watching]
cron = "*/5 7-23 * * *" # poll less frequently
hold = 300
```

## Configuration

Add the following to your `config.toml`:

```toml
[trakt]
client_id = "your-trakt-client-id"
client_secret = "your-trakt-client-secret"

# Set automatically by the auth flow — do not edit manually:
# access_token = "..."
# refresh_token = "..."
# expires_at = 1234567890

# Optional: number of days ahead to show in the calendar (default 7, max 33)
# calendar_days = 7
```

| Key | Required | Description |
|---|---|---|
| `client_id` | Yes | OAuth client ID from your Trakt application |
| `client_secret` | Yes | OAuth client secret from the same application |
| `access_token` | Auto | Written by the auth flow — do not set manually |
| `refresh_token` | Auto | Written by the auth flow — do not set manually |
| `expires_at` | Auto | Written by the auth flow — do not set manually |
| `calendar_days` | No | Days ahead for the calendar window (default `7`, max `33`) |

### Creating a Trakt application

1. Sign in at [trakt.tv](https://trakt.tv) and go to
   **Settings → Your API Apps → New Application**
2. Give it a name (e.g. `e-note-ion`)
3. Set **Redirect URI** to `urn:ietf:wg:oauth:2.0:oob`
4. Copy the **Client ID** and **Client Secret** into your `config.toml`

Trakt profiles can be private. No special account type is required — the
calendar and watching endpoints used here are available to all users.

## Authentication

This integration uses the OAuth **device code flow**: no browser redirect is
needed on the scheduler host, making it well-suited for Docker and Unraid
deployments.

**Flow:**

1. Start the container with the Trakt integration enabled
2. Check the container logs — you will see:
   ```
   Trakt auth required. Go to https://trakt.tv/activate and enter: XXXX-XXXX
   ```
3. On any device, visit the URL and enter the code
4. The scheduler detects approval and writes tokens to `config.toml`
5. Trakt templates start showing immediately

Until auth is complete, Trakt templates are silently skipped — no error is
logged, the display just shows other content.

### Viewing logs

**Docker:**
```bash
docker logs e-note-ion
```

**Unraid:** In the Unraid web UI, go to **Docker** → click the container icon
next to **e-note-ion** → **Logs**. The auth code and URL will appear here.
For quick access, Unraid also shows container output in the Docker tab when you
expand a container row.

### Token refresh

Access tokens expire approximately every 90 days. The scheduler refreshes
automatically when the token is within 1 hour of expiry. Each refresh rotates
the refresh token (the old one is invalidated), so the updated values are
written back to `config.toml` without any user action.

If `config.toml` is mounted read-only in Docker, token refresh will fail. Mount
it read-write so tokens can be persisted:

```bash
# Read-write mount (required for token persistence):
-v /path/to/config.toml:/app/config.toml

# Do NOT use :ro — token refresh writes to this file
```

## TMDb integration (canonical titles and episode numbers)

Trakt uses TVDb episode ordering, which represents many anime series as a
single flat season (e.g. Attack on Titan Season 4 appears as Season 1 Episode
X). TMDb uses the canonical multi-season structure. When a TMDb read access
token is configured, both the Trakt and Plex integrations resolve titles and
episode numbers through TMDb automatically.

To enable, add a `[tmdb]` section to `config.toml`:

```toml
[tmdb]
api_read_access_token = "your-tmdb-api-read-access-token"
```

Get a free read access token at https://www.themoviedb.org/settings/api.
No TMDb account tier is required — the read access token is available on
all accounts. TMDb lookups are cached in-memory for the lifetime of the
process; results are stable and restarts simply repopulate the cache.

When TMDb is not configured, both integrations continue to use their native
titles and episode numbers unchanged.

## Keeping data current

### API announcements

Authoritative source: https://github.com/trakt/trakt-api/discussions

Trakt requires all API developers to watch and subscribe to notifications on
this repository. Breaking changes, deprecations, and policy updates are
announced there. Subscribe to the repository to receive notifications, and
review new discussions periodically for anything that may affect this integration.

### Trakt API endpoints

This integration uses:
- `GET /calendars/my/shows/{date}/{days}` — episodes airing in the next N days
- `GET /users/me/watched/shows` — shows sorted by most recently watched
- `GET /shows/{trakt_id}/progress/watched` — next unwatched episode for a show
- `GET /users/me/watching` — currently playing episode or movie

All endpoints are available on all Trakt accounts (no VIP required).

Authoritative API docs: https://trakt.docs.apiary.io
