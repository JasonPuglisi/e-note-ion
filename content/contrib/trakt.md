# trakt.json

Trakt.tv integration — shows the next upcoming episode from your calendar and
what you are currently watching. Calendar updates every 4 hours; now-playing
polls every 3 minutes and only shows when something is actively playing.

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
- `GET /users/me/watching` — currently playing episode or movie

Both endpoints are available on all Trakt accounts (no VIP required).

Authoritative API docs: https://trakt.docs.apiary.io
