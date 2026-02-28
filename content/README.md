# Content

This directory contains the JSON files that define what gets displayed on the
board. Restart the scheduler to pick up changes.

## Directories

- **`contrib/`** — bundled community-contributed content, disabled by default.
  To contribute content, open a pull request adding a `.json` file and a
  companion `.md` doc (see template in `content/contrib/TEMPLATE.md`).

- **`user/`** — your personal content. Files here are loaded automatically
  unless `content_enabled` is set (see below). This directory is git-ignored
  so personal schedules are never committed to the project repo. To version
  your personal content, create a private git repository and volume-mount it
  at `/app/content/user` (Docker) or symlink it to this directory directly.

## Content filter (`content_enabled`)

The `[scheduler].content_enabled` key in `config.toml` controls which files
are loaded from both directories:

```toml
# Absent (default): all user content loads, no contrib content loads
content_enabled = ["*"]              # all user + all contrib
content_enabled = ["bart", "trakt"]  # only these stems from either directory
content_enabled = ["my_quotes"]      # only my_quotes.json from content/user/
```

When the key is absent, user files always load and no contrib files load.
When the key is set, the filter applies to both `user/` and `contrib/`.

## Content file format

Each JSON file can contain multiple named templates, each with its own
schedule and display settings.

```json
{
  "templates": {
    "my_message": {
      "schedule": {
        "cron": "0 8 * * *",
        "hold": 600,
        "timeout": 600
      },
      "priority": 5,
      "private": false,
      "truncation": "word",
      "templates": [
        { "format": ["GOOD MORNING", "{quip}"] }
      ]
    }
  },
  "variables": {
    "quip": [
      ["HAVE A", "GREAT DAY"],
      ["YOU GOT", "THIS"]
    ]
  }
}
```

| Field | Description |
|---|---|
| `cron` | Standard 5-field cron expression |
| `hold` | Seconds the message stays on display before the next update |
| `timeout` | Seconds the message can wait in the queue before being discarded |
| `priority` | Integer 0–10; higher number runs first when multiple messages are queued simultaneously |
| `private` | If `true`, excluded when public mode is enabled (`[scheduler] public = true` in config.toml) |
| `truncation` | `hard` cuts mid-word (default); `word` stops at a word boundary; `ellipsis` adds `...` |

### Variables

`{variable}` placeholders are replaced with a randomly chosen option from the
corresponding `variables` entry. A format entry that is exactly `{variable}`
expands into all lines of the chosen option; an inline `{variable}` within
other text is replaced by the first line of the option.

When a template has multiple `{ "format": [...] }` entries, one is chosen at
random each time the template fires.

### Color squares

Color squares can be embedded in format strings using short tags: `[R]` `[O]`
`[Y]` `[G]` `[B]` `[V]` `[W]` `[K]` (red, orange, yellow, green, blue,
violet, white, black). Each tag renders as a single colored square on the
display.

### Wrapping and truncation

After variable expansion, lines are automatically word-wrapped to fit the
board width. If the result has more rows than the board height, the excess is
silently dropped. Content from dynamic sources (e.g. API responses) doesn't
need to be pre-fitted to the board dimensions.

### Integration templates

Templates can pull live data from an integration by adding
`"integration": "<name>"`. The worker calls the integration at job time to
fetch current variable values:

```json
{
  "templates": {
    "my_integration_template": {
      "schedule": { "cron": "*/5 * * * *", "hold": 60, "timeout": 60 },
      "priority": 8,
      "integration": "my_integration",
      "templates": [
        { "format": ["{line_1}", "{line_2}"] }
      ]
    }
  }
}
```

### Priority guidelines

Priority determines which message is shown first when multiple jobs fire at
or near the same time and the worker is busy. It does not affect *when* a job
runs — only which queued message the display shows first.

| Level | Range | Use case |
|---|---|---|
| Background | 0–2 | Ambient/decorative content. Pair with a short `timeout` so stale messages drop silently rather than showing late. |
| Default | 3–5 | Normal scheduled content — daily quotes, weather, calendar items. Most templates should live here. |
| Elevated | 6–7 | Time-sensitive but not urgent — transit departures, reminders. Gets priority position in the queue but does not interrupt an active hold. |
| High | 8–9 | Alerts and time-critical events where the user should see it promptly — countdowns, imminent departures, notifications. **After the global `min_hold` floor, a High message waiting in the queue will interrupt the current display if it has a lower-priority hold in progress.** |
| Maximum | 10 | Reserved for a single "always wins" template. Avoid using broadly — once multiple templates share the same level, tie-breaking falls back to scheduling order. |

Rules of thumb:
- **Start at 5 and adjust.** Only raise priority when you observe or anticipate contention.
- **Pair high priority with a short `timeout`.** A high-priority message that is stale is worse than none. Set `timeout` to roughly the window of relevance.
- **Pair low priority with a short `timeout` too.** Background content with a long `timeout` will show up hours late. Either set `timeout` to match the display window, or accept that it may be discarded.
- **Avoid priority inflation.** If everything is 8–10, nothing is. Reserve the top of the scale for the one or two templates where timing genuinely matters.
- **Use `config.toml` overrides to tune without touching JSON.** See [Schedule overrides](#schedule-overrides) below.

### Schedule overrides

Override schedule fields or visibility for any named template directly in
`config.toml`, without editing the content file:

```toml
[bart.schedules.departures]
cron = "*/5 6-9 * * 1-5"  # extend window to start at 6am
hold = 180
timeout = 90
priority = 9
disabled = true            # skip this template entirely
private = true             # hide in public mode
```

The section name is `[<file-stem>.schedules.<template-name>]`. All fields are
optional; unspecified fields use the template's JSON default. `disabled = true`
takes precedence over all other fields — the template is skipped entirely.
`private = true` marks a template as hidden in public mode even if the JSON
does not.

## Schedule coordination

Templates that fire on a shared schedule compete for the display queue.
Follow these guidelines when setting `cron`, `hold`, and `timeout` for a new
template to keep everything playing nicely together.

### Existing hourly slots

| Slot | What fires | Notes |
|---|---|---|
| `:00` every hour | `weather` (priority 5, hold 600s) | Plus `trakt.calendar` every 4h (priority 4) and `aria` at 8am/8pm (priority 9) |
| `:30` every hour | `calendar` (priority 5, hold 300s) | Plus `discogs` at 8:30am (priority 5) |

**New templates that fire hourly should pick `:00` or `:30` and check what
else fires there.** Avoid adding a third integration to a slot that already
has two.

### Pairing `timeout` with `hold`

`timeout` is how long a message can wait in the queue before being discarded.
Set it long enough to survive the hold of whatever it queues behind:

- A priority-5 template firing at `:00` queues behind `weather` (600s hold).
  Set `timeout >= 600` — otherwise the message expires before `weather` finishes.
- A priority-4 template firing alongside a priority-5 template needs
  `timeout` long enough to outlast both the higher-priority hold *and* any
  queue drain time. `timeout = 1800` is a safe floor for low-priority hourly
  content.

Short `timeout` values (≤ 600s) are appropriate only for high-priority,
time-sensitive content (e.g. BART departures) where a stale message is worse
than no message.

### Priority reminder

Priority controls queue order, not fire time. Two templates with the same
cron expression fire at the same instant — priority decides which shows first.
Don't inflate priority to "win" — see the priority guidelines above.

## Contrib integrations

| File | Description |
|---|---|
| [`bart.json`](contrib/bart.md) | BART real-time departure board |
| [`discogs.json`](contrib/discogs.md) | Daily vinyl suggestion from your Discogs collection |
| [`calendar.json`](contrib/calendar.md) | Today's calendar events (ICS and iCloud CalDAV) |
| [`plex.json`](contrib/plex.md) | Plex Media Server now-playing via webhook |
| [`trakt.json`](contrib/trakt.md) | Trakt.tv upcoming calendar and now-playing |
| [`weather.json`](contrib/weather.md) | Current weather conditions via Open-Meteo |
