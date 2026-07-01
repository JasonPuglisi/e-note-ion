# qbittorrent.json

Seeding stats from qBittorrent Web UI. Hobbies — fires once daily showing
active seeding count and total seeded size.

## Schedule

### `status` — seeding count and total size

**Cron:** `45 10 * * *` — fires daily at 10:45.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 3 (Hobbies)

Private template. Fires in a solo `:45` slot with a 300 s hold budget — well
within the 1800 s ceiling.

To override schedule fields without editing this file:

```toml
[qbittorrent.schedules.status]
cron = "45 10,16 * * *"  # check twice daily
hold = 300
timeout = 3600
disabled = true           # skip entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[qbittorrent]
url = "http://192.168.1.50:8080"
username = "admin"
password = "your-password"
```

| Key | Required | Description |
|---|---|---|
| `url` | Yes | Web UI base URL — must be a **local network** address |
| `username` | Yes | Web UI username |
| `password` | Yes | Web UI password |
| `verify_tls` | No | Set to `false` to skip TLS cert verification (self-signed certs). Default: `true` |

### Security — local network only

The qBittorrent Web API transmits credentials in plaintext and is designed
for LAN access only. **Do not expose the Web UI to the internet.**

Use a local IP address or hostname (e.g. `http://192.168.1.50:8080`,
`http://tower:8080`). If e-note-ion runs in Docker, the container must have
network access to the qBittorrent host — use host network mode or configure
Docker networking to reach the LAN.

## Display format

```
[B] TORRENTS
4 SEEDING
1.2 TB
```

- `[B]` blue — qBittorrent brand color
- Seeding count: number of torrents in seeding state
- Total size of seeded content (not uploaded amount)
- Size uses TB for >= 1 TB, GB otherwise; one decimal place, `.0` dropped

When nothing is seeding, the template is silently skipped.

## API details

**Auth:** `POST /api/v2/auth/login` with form-encoded username/password.
Returns a session cookie used for subsequent requests. Success is signalled by
`200 Ok.` on qBittorrent ≤5.1 and by `204 No Content` (empty body) on 5.2+; the
cookie was also renamed `SID` → `QBT_SID_<port>` in 5.2. The integration accepts
both status forms and forwards the whole cookie jar, so it is version- and
port-agnostic.

**Data:** `GET /api/v2/torrents/info?filter=seeding` — returns per-torrent
objects. The integration sums the `size` field across all results.

Per-torrent stats (including `size`) persist across qBittorrent restarts.
Session-level stats (`/api/v2/transfer/info`) do not persist and are not used.

## Keeping data current

### API changes

Authoritative source: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-(qBittorrent-5.0)

The integration uses `/api/v2/auth/login` and `/api/v2/torrents/info` — stable
since qBittorrent 4.1, though 5.2 changed the login **success** response from
`200 Ok.` to `204` (empty body) and renamed the session cookie to
`QBT_SID_<port>`. Monitor the wiki for further deprecation notices.
