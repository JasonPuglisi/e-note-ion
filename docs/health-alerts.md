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

## Failure behaviour

The push runs on a daemon thread with a 5-second timeout and two retries. Every
failure is caught and logged; an unreachable alert endpoint never affects the
scheduler or the board. The secret is never logged, and URLs in error messages
are redacted.
