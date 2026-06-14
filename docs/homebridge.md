# HomeBridge / Apple Home setup

Control the Vestaboard's **Quiet** and **Public** modes from the Apple Home app
(and Siri, Control Center, and Home automations) via [HomeBridge](https://homebridge.io/).

This complements the iOS Shortcuts / Focus automation path documented in
[`content/contrib/scheduler.md`](../content/contrib/scheduler.md) — the same two
modes, exposed as native Home switches.

---

## What e-note-ion provides

The scheduler already exposes everything HomeBridge needs:

| Direction | Endpoint | Purpose |
|---|---|---|
| **Set** | `POST /webhook/scheduler` | `{"action": "quiet"｜"wake"｜"public"｜"private"}` — toggle a mode |
| **Read** | `GET /state` | Current `modes` (`quiet`/`public`) plus board content, for switch state sync |
| **Push** | `[homebridge]` config (optional) | e-note-ion notifies HomeBridge instantly on a mode change |

Both endpoints authenticate with named webhook credentials
(`X-Webhook-Secret` header or `?secret=` query param). The `scheduler` and
`state` credentials are auto-generated on first startup — check the log for the
plaintext secrets.

### `GET /state` response

```json
{
  "modes": { "quiet": false, "public": false },
  "source": "board",
  "grid": [[8, 0, 0], ...],
  "rendered": "HELLO          ",
  "timestamp": "2026-06-14T09:30:00-07:00"
}
```

- `modes` is always present — the primary payload for the HomeKit switches.
- `source` is `board` (last grid sent), `virtual` (quiet-mode buffer), or `empty`.
- `grid`/`rendered`/`timestamp` are `null` when no content is known.
- `?refresh=true` forces an authoritative fetch from the Vestaboard API (rate-limited).

### Optional push (`[homebridge]`)

When `[homebridge]` is configured, each **real** quiet/public transition fires:

```
POST <url>
X-Webhook-Secret: <secret>   (if configured)

{"characteristic": "quiet"｜"public", "value": true｜false}
```

The request runs on a background thread with a short timeout; a HomeBridge
outage is logged and never blocks toggling. Without push, a HomeBridge switch
still stays in sync by polling `GET /state` on its pull interval — set a slow
interval (e.g. 5 minutes) when push is enabled, as a self-healing safety net.

```toml
[homebridge]
url = "http://homebridge.local:51828/e-note-ion"
secret = "your-shared-secret"
```

---

## Recommended: the companion plugin (single "Vestaboard" device)

The companion plugin [`homebridge-e-note-ion`](https://github.com/JasonPuglisi/homebridge-e-note-ion)
(separate repo) publishes a **single "Vestaboard" accessory** exposing both the
**Quiet** and **Public** switches together, consuming `GET /state` (poll) and the
push notifications above (instant). This is the only way to group both toggles
under one Home device — generic HTTP plugins create one accessory per switch.

> **Note:** with two same-type switches on one accessory, Apple's Home app may
> still render two tiles, but they stay grouped under the one "Vestaboard"
> accessory (shared device info, moved between rooms as a unit).

---

## Alternative: off-the-shelf `homebridge-http-webhooks`

If you don't want the companion plugin, [`homebridge-http-webhooks`](https://www.npmjs.com/package/homebridge-http-webhooks)
works today — at the cost of **two separate switch tiles** (no single grouped
device). Define two switches, point their on/off URLs at `POST /webhook/scheduler`,
and enable push by setting `[homebridge].url` to the plugin's webhook listener so
state stays in sync. Consult the plugin's README for the exact `config.json`
shape, which changes between versions.

---

## Networking

HomeBridge usually runs on the same LAN (often the same host, e.g. Unraid), so
**plain HTTP to the webhook port is fine** — the secret never leaves your
network. TLS is only required for senders outside your LAN; see
[`docs/webhook-reverse-proxy.md`](webhook-reverse-proxy.md).
