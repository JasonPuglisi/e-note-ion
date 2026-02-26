# plex.json

Plex Media Server integration — shows what you are currently watching on the
board using Plex webhooks. Displays "NOW PLAYING" when playback starts or
resumes, "PAUSED" when playback is paused, and clears when playback stops.

Unlike cron-scheduled templates, these templates are triggered entirely by
incoming Plex webhook events. No content is shown when Plex is idle.

## Requirements

Plex webhooks are a **Plex Pass** feature. Your Plex Media Server account
must have an active Plex Pass subscription to send webhooks.

## Configuration

No `[plex]` section is needed in `config.toml`. The webhook listener must
be enabled:

```toml
[webhook]
# port and bind are optional; defaults shown
port = 8080
bind = "127.0.0.1"
# secret is auto-generated on first start; copy it from the startup log
```

Enable the plex integration in `[scheduler]`:

```toml
[scheduler]
content_enabled = ["plex"]
```

To override hold, timeout, or priority for a template, add a section to
`config.toml`:

```toml
[plex.schedules.now_playing]
hold = 7200    # 2-hour ceiling instead of 4 hours
timeout = 60
priority = 9

[plex.schedules.paused]
hold = 3600
```

| Override key | Default | Description |
|---|---|---|
| `hold` | `14400` | Maximum seconds to show if no stop event arrives (safety ceiling) |
| `timeout` | `30` | Seconds the message can wait in the queue before being discarded |
| `priority` | `8` | Display priority (0–10) |

## Webhook setup

### 1. Find your webhook URL

The scheduler binds on `127.0.0.1:8080` by default. For Plex to reach it,
the URL must be accessible from your Plex Media Server host.

- **Same machine:** `http://localhost:8080/webhook/plex`
- **Docker on same host:** use the host's LAN IP (e.g. `http://192.168.1.x:8080/webhook/plex`)
- **Separate host:** use the scheduler host's LAN IP or a reverse proxy

### 2. Add the shared secret

Check the scheduler startup log for the auto-generated secret:

```
Webhook secret generated and saved to config.toml:
  <your-secret-here>
Copy this into your webhook sender (Plex, Shortcuts, etc.).
```

Plex does not support custom HTTP headers, so the secret cannot be sent
via `X-Webhook-Secret`. To work around this, embed the secret as a query
parameter and use a reverse proxy or local forwarder that injects it as a
header — or use an iOS Shortcut or home automation rule to forward Plex
events with the header added.

See [Webhook forwarding](#webhook-forwarding) below for practical options.

### 3. Configure Plex

1. Open Plex Web or the Plex desktop app
2. Go to **Settings → Webhooks** (requires Plex Pass)
3. Click **Add Webhook**
4. Enter the webhook URL from step 1
5. Save

Plex will immediately start sending events for any media played from your
server.

## Webhook forwarding

Because Plex cannot send a custom `X-Webhook-Secret` header, you need a
layer between Plex and the scheduler that injects the secret. Options:

### nginx reverse proxy

Add a location block that injects the header:

```nginx
location /webhook/plex {
    proxy_pass http://127.0.0.1:8080/webhook/plex;
    proxy_set_header X-Webhook-Secret "your-secret-here";
}
```

Point Plex at the nginx URL instead of the scheduler directly.

### iOS Shortcuts

Create a shortcut that:
1. Receives a URL scheme trigger (or is called by an automation)
2. Gets the Plex webhook payload from a variable
3. Makes a POST request to the scheduler URL with the `X-Webhook-Secret` header

### Home Assistant

Use a REST command or a Node-RED flow to receive the Plex event and
forward it to the scheduler with the secret header added.

## Supported events

| Plex event | Action |
|---|---|
| `media.play` | Show "NOW PLAYING" (indefinite hold) |
| `media.resume` | Show "NOW PLAYING" (indefinite hold) |
| `media.pause` | Show "PAUSED" (indefinite hold) |
| `media.stop` | Interrupt current hold (clears the board) |

Other events (e.g. `media.scrobble`, `media.rate`, `library.new`) are
silently discarded.

Only video media is displayed — music and photo events return no message.

## Display format

**Note (3×15):**

```
[O] NOW PLAYING
SHOW NAME
EPISODE TITLE
```

- Row 1: `[O]` (orange, Plex brand color) + mode label
- Row 2: Show name (for episodes) or movie title (for movies)
- Row 3: Episode title, with leading articles stripped (A, An, The)
  For movies, row 3 is blank.

Leading articles are stripped from episode titles only. Show names and
movie titles are preserved as-is: "THE BEAR" stays "THE BEAR", but the
episode title "The Beef" becomes "BEEF".

## Keeping data current

### Plex API

Plex does not publish a formal changelog for webhook payloads. Monitor:
- [Plex support articles on webhooks](https://support.plex.tv/articles/115002267687-webhooks/)
- Plex developer forums for breaking changes to `media.play` / `media.pause`
  payload shapes

The fields used by this integration (`event`, `Metadata.type`,
`Metadata.title`, `Metadata.grandparentTitle`, `Metadata.parentIndex`,
`Metadata.index`) have been stable across Plex versions. Verify after
major Plex Media Server updates.
