# Health alerts

`GET /health` reports integration health, but polling it is work. Set
`alert_url` under `[health]` in `config.toml` and e-note-ion will push a
notification whenever overall status changes.

```toml
[health]
alert_url = "https://ntfy.sh/my-private-topic"
alert_secret = "a-long-random-string"   # optional
alert_confirm_seconds = 120             # optional, default 120
```

## What gets sent

A `POST` to `alert_url` with:

```json
{
  "previous": "healthy",
  "current": "error",
  "uptime_seconds": 84213,
  "unhealthy": {
    "bart": {
      "status": "error",
      "last_error": "2026-09-06T21:04:11Z",
      "last_error_message": "BART: departures request failed — ...",
      "success_rate": 0.0
    }
  }
}
```

`unhealthy` carries only the targets that are actually unwell, so the payload
stays readable in a chat client and does not grow with the number of
integrations you run. If `alert_secret` is set it is sent as the
`X-Webhook-Secret` header.

Statuses are `healthy`, `degraded`, `overdue` (a cron that has stopped firing),
`error`, and `unknown`.

## Why transitions are delayed slightly

A status has to hold for `alert_confirm_seconds` before it is reported.
Without that, an integration that fails one run and recovers on the next
produces a `healthy → error → healthy` pair of alerts for a blip nobody needed
to know about. Lower it if you want faster paging; set it to `0` to report
every change immediately.

Status is re-evaluated every 60 seconds. That polling is deliberate rather than
event-driven: an integration going `overdue` is defined by the *absence* of
events, so there is nothing to react to.

## Recipes

### ntfy

Free and needs no account. Pick an unguessable topic name — anyone who knows
the topic can read it.

```toml
[health]
alert_url = "https://ntfy.sh/e-note-ion-a8f3c1d9"
```

### Slack or Discord

Create an incoming webhook and paste the URL. Both accept arbitrary JSON but
render it as raw text; for formatted messages, point `alert_url` at a small
relay (a Cloudflare Worker or Home Assistant webhook) that reshapes the payload.

### Home Assistant

Create an automation with a webhook trigger, then:

```toml
[health]
alert_url = "https://ha.example.com/api/webhook/e-note-ion-health"
```

The trigger payload is available as `{{ trigger.json.current }}` and
`{{ trigger.json.unhealthy }}`.

### iOS Shortcut (no server config)

If you would rather poll than configure an endpoint, this needs no changes to
`config.toml` beyond having a `health` webhook credential:

1. **Shortcuts → new shortcut → Get Contents of URL**
   - URL: `https://<your-host>/health?secret=<your-health-credential>`
   - Method: `GET`
2. **Get Dictionary Value** — key `status`
3. **If** — `status` *is not* `healthy`
4. **Show Notification** — "Vestaboard health: <status>"
5. Add a **Personal Automation** on a time trigger to run it hourly.

Note `/health` returns HTTP 503 when unhealthy, so a monitoring tool that only
checks status codes works without parsing the body at all.

### UptimeRobot (shows the alert on the board)

The recipes above push a health change somewhere else. This one brings it back
to the Vestaboard, using the `uptimerobot` contrib integration as the display
path — e-note-ion monitoring itself.

That 503 is enough to mark an UptimeRobot monitor down. The contrib integration
polls UptimeRobot every 5 minutes and displays any monitor that is down, so a
broken integration ends up on the board:

```
[R] OUTAGE
E-NOTE-ION
DOWN 25 MINUTES
```

Setup:

1. Enable the integration — add `uptimerobot` to `[scheduler] content_enabled`
   and set `[uptimerobot] api_key`. See
   [`content/contrib/uptimerobot.md`](../content/contrib/uptimerobot.md).
2. Create an HTTP(s) monitor in UptimeRobot pointing at
   `https://<your-host>/health?secret=<your-health-credential>`.
3. Give it a short friendly name. Row 2 of the display is the monitor name,
   truncated to the board width — `E-NOTE-ION` reads well on a Note, a long
   descriptive name does not.

The secret goes in the query string because UptimeRobot only supports custom
request headers on its Pro plan; `/health` accepts either. Two consequences
worth knowing before you set this up:

- **The credential is stored in UptimeRobot's monitor config** and appears in
  their request logs. Use the dedicated `health` webhook credential rather than
  sharing one with another integration — it is already scoped to the health
  endpoint alone, so it can be rotated without touching anything else.
- **`/health` has to be reachable from the internet.** See
  [`webhook-reverse-proxy.md`](webhook-reverse-proxy.md).

If neither is acceptable, `alert_url` above is outbound and needs no exposed
endpoint.

One limitation: UptimeRobot only sees the status code, so the board tells you
*that* something is unhealthy, not *which* integration. Use `GET /health` or
the push payload for that.

## Failure behaviour

The push runs on a daemon thread with a 5-second timeout and two retries. Every
failure is caught and logged; an unreachable alert endpoint never affects the
scheduler or the board. The secret is never logged, and URLs in error messages
are redacted.
