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

### 1. Expose the webhook port

In `config.toml`, enable the webhook listener and set the bind address so
Plex (which may be in a separate container) can reach it:

```toml
[webhook]
bind = "0.0.0.0"
# port defaults to 8080 — the container-internal port
```

**Unraid:** the Unraid template includes a pre-configured webhook port mapping
(container `8080` → host `32800`). In the e-note-ion Docker settings, confirm
the **Webhook port** mapping is active. Port `32800` avoids conflicts with
common services that use `8080`. You can change the host port to any free port.

### 2. Get the shared secret

Start (or restart) the scheduler. The first run auto-generates a secret and
prints it once:

```
Webhook secret generated and saved to config.toml:
  <your-secret-here>
Copy this into your webhook sender (Plex, Shortcuts, etc.).
```

The secret is also saved to `config.toml` under `[webhook]` so it persists
across restarts.

### 3. Build the webhook URL

Plex cannot send custom HTTP headers, so pass the secret as a query parameter
instead:

```
http://<unraid-ip>:32800/webhook/plex?secret=<your-secret-here>
```

Both forms are accepted — the query parameter is equivalent to the
`X-Webhook-Secret` header. If both are present, the header takes precedence.

**Unraid example** (replace with your server's LAN IP and generated secret):
```
http://192.168.1.100:32800/webhook/plex?secret=abc123xyz
```

### 4. Configure Plex

1. Open Plex Web → **Settings → Webhooks** (requires Plex Pass)
2. Click **Add Webhook**
3. Enter the full URL including `?secret=...` from the previous step
4. Save

Plex begins sending events immediately for any media played from your server.

### Using a reverse proxy (optional)

If you have nginx Proxy Manager, SWAG, or another reverse proxy, you can
inject the secret as a header instead and keep it out of the URL:

```nginx
location /webhook/plex {
    proxy_pass http://127.0.0.1:32800/webhook/plex;
    proxy_set_header X-Webhook-Secret "your-secret-here";
}
```

Point Plex at the proxy URL (no `?secret=` needed).

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
