# unraid.json

Unraid server status — array capacity and uptime. Hobbies — fires once daily.

## Schedule

### `status` — array capacity and uptime

**Cron:** `45 11 * * *` — fires daily at 11:45.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 3 (Hobbies)

Private template. Fires in a solo `:45` slot with a 300 s hold budget — well
within the 1800 s ceiling.

To override schedule fields without editing this file:

```toml
[unraid.schedules.status]
cron = "45 10,16 * * *"  # check twice daily
hold = 300
timeout = 3600
disabled = true           # skip entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[unraid]
url = "http://192.168.1.10"
api_key = "your-unraid-api-key"
```

| Key | Required | Description |
|---|---|---|
| `url` | Yes | Server base URL — must be a **local network** address |
| `api_key` | Yes | API key from Settings → Management Access → API Keys |

### Security — local network only

The Unraid API exposes full server control and should only be accessed over
the local network. **Do not expose the Unraid API to the internet.**

Use a local IP address or hostname (e.g. `http://192.168.1.10`,
`http://tower`). If e-note-ion runs in Docker, the container must have
network access to the Unraid host — use host network mode or configure
Docker networking to reach the LAN.

### Unraid version requirement

The GraphQL API requires **Unraid 7.2 or later**. Earlier versions do not
expose the `/graphql` endpoint.

## Display format

```
[O] UNRAID
14 / 20 TB
UP 3M 2D 8H
```

- `[O]` orange — Unraid brand color
- Array used / total capacity
- Uptime: `#M #D #H` (months, days, hours); no weeks, no minutes, no
  zero-padding; zero-value components are skipped
- Size uses TB for >= 1 TB, GB otherwise; one decimal place, `.0` dropped

When the array is stopped or degraded, the capacity line shows `[R] STOPPED`
or `[R] DEGRADED` instead of usage numbers.

## API details

**Auth:** `x-api-key` header with the API key.

**Data:** Single GraphQL POST to `{url}/graphql`:

```graphql
{
  info { os { uptime } }
  array {
    state
    capacity { disks { used, total } }
  }
}
```

`uptime` is in seconds. `used` and `total` are in bytes. Months are
approximated as 30 days.

## Keeping data current

### API changes

Authoritative source: https://docs.unraid.net/API/

The Unraid API is under active development. Monitor the docs and the
[unraid/api](https://github.com/unraid/api) repository for schema changes.
The integration queries `info.os.uptime` and `array.capacity.disks` — verify
these fields exist after major Unraid updates.
