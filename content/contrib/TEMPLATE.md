# <name>.json

One-sentence description. Category (e.g. Logistics) — schedule summary.

## Schedule

**Cron:** `0 8 * * *` — fires daily at 08:00.
**Hold:** 600 s | **Timeout:** 1800 s | **Priority:** 5 (Logistics)

Slot context: fires in the `:00` slot alongside `weather` (pri 5, 600 s hold);
combined budget is 1200 s, well within the 1800 s ceiling.

To override schedule fields without editing this file:

```toml
[<name>.schedules.<template_name>]
cron = "0 9 * * *"
hold = 300
timeout = 900
priority = 6
disabled = true   # skip this template entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[<name>]
key_name = "value"
```

| Key | Required | Description |
|---|---|---|
| `key_name` | Yes/No | What it does |

## Keeping data current

### <Data type>

Authoritative source: <URL>

Instructions for verifying and updating any hardcoded lists (station codes,
destination names, API endpoint changes, etc.).
