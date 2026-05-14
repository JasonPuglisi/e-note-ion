# uptimerobot.json

Service outage alerts from UptimeRobot. Logistics — polls every 5 minutes,
displays only when a monitor is down.

## Schedule

### `status` — outage alert

**Cron:** `*/5 * * * *` — fires every 5 minutes.
**Hold:** 300 s | **Timeout:** 120 s | **Priority:** 8 (Logistics)
**Refresh:** 60 s — elapsed downtime updates every minute while on-screen.

No-op when all monitors are up — the template is silently skipped via
`IntegrationDataUnavailableError`. During an outage, the display refreshes
every 60 s (duration ticks up), and the next cron fire re-enqueues with
priority 8 so outages stay prominent.

To override schedule fields without editing this file:

```toml
[uptimerobot.schedules.status]
cron = "*/3 * * * *"
hold = 180
timeout = 60
priority = 9
refresh_interval = 30
disabled = true   # skip this template entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[uptimerobot]
api_key = "your-uptimerobot-api-key"
```

| Key | Required | Description |
|---|---|---|
| `api_key` | Yes | UptimeRobot API key — Main API Key or Monitor-Specific API Key from [My Settings](https://dashboard.uptimerobot.com/integrations) |

The free plan supports up to 50 monitors and 10 API requests per minute.
With the default 5-minute cron and 60-second refresh, API usage stays well
within the free tier limit.

## Display format

**Outage (one or more monitors down):**
```
[R] OUTAGE
API.EXAMPLE.COM
DOWN 15 MINUTES
```

- Row 1: `[R]` red square + `OUTAGE`
- Row 2: Friendly name of the monitor whose outage started earliest
  (truncated with ellipsis if needed)
- Row 3: Elapsed downtime, sourced from the UptimeRobot API's latest down
  log so it reflects the true outage start (survives restarts)

Duration formatting uses a single unit, rounded down, with coarser steps as
the outage ages — keeps the display readable and reduces flap updates:

| Elapsed | Output |
|---|---|
| `< 60 s` | `0 MINUTES` |
| `1–9 min` | `N MINUTE` / `N MINUTES` (per-minute) |
| `10–59 min` | `N MINUTES` rounded down to nearest 5 |
| `1–23 hr` | `N HOUR` / `N HOURS` |
| `≥ 24 hr` | `N DAY` / `N DAYS` |

When all monitors recover, the template is silently skipped and normal
content resumes on the next cycle.

## API details

**Endpoint:** `POST https://api.uptimerobot.com/v2/getMonitors` with
`logs=1, logs_limit=1` so the latest log entry per monitor (used to derive
true outage start) is included.
**Auth:** API key sent as a form parameter in the POST body.
**Rate limit:** 10 req/min (free plan); 5000 req/min (Pro plan).

**Monitor status codes used:**

| Code | Meaning | Action |
|---|---|---|
| 2 | Up | No display (all-up → skip) |
| 8 | Seems down | Treated as down |
| 9 | Down | Treated as down |
| 0, 1 | Paused / Not checked | Ignored |

## Keeping data current

### UptimeRobot API

Authoritative source: [UptimeRobot API documentation](https://uptimerobot.com/api/)

The integration uses the `/getMonitors` endpoint which has been stable across
API versions. Monitor status codes (0, 1, 2, 8, 9) are part of the core API
contract. Verify after major UptimeRobot platform updates.
