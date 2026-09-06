# youtube.json

Live streams from YouTube subscriptions. Entertainment — fires twice daily.

## Schedule

**Cron:** `45 12,20 * * *` — fires at 12:45 and 20:45.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 4 (Entertainment)

Slot context: uses the `:45` slot at 12 and 20, no contention with existing
templates.

To override schedule fields without editing this file:

```toml
[youtube.schedules.live]
cron = "45 10,18 * * *"
hold = 300
timeout = 3600
priority = 4
disabled = true   # skip this template entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[google]
client_id = "your-google-oauth-client-id"
client_secret = "your-google-oauth-client-secret"
```

OAuth credentials live in `[google]` (not `[youtube]`) so they can be shared
across future Google integrations.

Create a Google Cloud project with the YouTube Data API v3 enabled, then create
an OAuth 2.0 client ID of type "TVs and Limited Input devices" in the
Credentials page. The client must have the
`https://www.googleapis.com/auth/youtube.readonly` scope.

**Recommended:** Set the OAuth consent screen to "Production" publishing status
(no verification required for personal-use apps with < 100 users). This gives
refresh tokens an indefinite lifetime — no re-authorization needed. If you use
"Testing" mode instead, refresh tokens expire after 7 days (Google-imposed
constraint), requiring weekly re-authorization via the device code flow.

| Key | Required | Description |
|---|---|---|
| `client_id` | Yes | Google OAuth 2.0 client ID |
| `client_secret` | Yes | Google OAuth 2.0 client secret |


OAuth state is machine-managed and lives in `[google.auth]`, created on first
auth. Do not edit it by hand, and keep the subsection last in `[google]` —
anything below a `[google.auth]` header belongs to the subsection.

| Key (in `[google.auth]`) | Description |
|---|---|
| `access_token` | Written by the auth flow |
| `refresh_token` | Written by the auth flow |
| `expires_at` | Written by the auth flow |

On first startup, the scheduler logs a URL and code. Visit the URL, sign in
with your Google account, and enter the code. Tokens are saved to `config.toml`
and refreshed automatically.

## How it works

1. Fetches subscribed channel IDs via `subscriptions.list` (cached 6 hours)
2. Polls each channel's public RSS feed (free, no quota)
3. Checks recent videos via `videos.list` with `liveStreamingDetails` (1 quota
   unit per 50 videos)
4. Filters to currently live streams and displays the most recently started one

**Quota budget:** ~15 units/day at 2 runs — negligible against the 10,000 daily
YouTube Data API quota.

## Keeping data current

### Google OAuth tokens

Access tokens expire after ~1 hour and are refreshed automatically.

In Production mode, refresh tokens do not expire — no maintenance needed. In
Testing mode, refresh tokens expire after 7 days; the scheduler automatically
clears stale tokens and re-initiates the device code auth flow when this
happens (check the logs for the new code and URL).

If tokens become corrupted, delete the whole `[google.auth]` section from
`config.toml` and restart — the auth flow recreates it.

### YouTube Data API quota

The default quota is 10,000 units/day. This integration uses ~15 units per run.
Monitor quota usage in the Google Cloud Console under APIs & Services → YouTube
Data API v3 → Quotas. If you add more frequent polling, watch the quota budget.
