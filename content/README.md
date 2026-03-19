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

This filter only affects **cron-scheduled** templates. Webhook-only
integrations (`plex`, `message`, `notion`) route incoming webhook events
directly through the webhook listener — their `.json` files never need to be
in `content_enabled` for webhooks to work.

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
| `cron` | Standard 5-field cron expression. **Use named days for `day_of_week`** (e.g. `mon-fri`, not `1-5`): APScheduler numbers weekdays from Monday (0=Mon … 6=Sun), opposite to Unix cron (0=Sun). Numeric values will silently fire on the wrong days. |
| `hold` | Seconds the message stays on display before the next update |
| `timeout` | Seconds the message can wait in the queue before being discarded |
| `priority` | Integer 0–10; higher number runs first when multiple messages are queued simultaneously |
| `private` | If `true`, excluded when public mode is enabled (`[scheduler] public = true` in config.toml) |
| `truncation` | `hard` cuts mid-word (default); `word` stops at a word boundary; `ellipsis` fills to the column limit then appends `...`; `wrap_ellipsis` word-wraps across available rows then appends `...` if text still overflows |

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

By default the worker calls `get_variables()` on the integration module. To
call a different function, add `"integration_fn": "<function_name>"` to the
template — useful when one integration exposes multiple data sources (e.g.
Trakt's `get_variables_calendar` and `get_variables_watching`).

### Content categories

Every template belongs to one of six categories. The category determines the
appropriate priority range and informs scheduling decisions:

| Category | Priority | Philosophy |
|---|---|---|
| **Personal** | 9 | Uniquely yours — pet feeding times, self-birthday. Brief (≤ 600 s hold), time-specific, highest priority. These are personal-tier inserts that always show first and are excluded from shared slot budget calculations. |
| **Social** | 6–8 | Human interactions — friend messages (8), other people's birthdays (6). Urgency varies: a direct message warrants a higher priority than informational birthday data. |
| **Logistics** | 5–7 | Actionable daily context — weather (5), calendar events (5), transit departures (8). Higher within the range when timing matters (missing a train > missing the weather update). |
| **Hobbies** | 4–5 | Lifestyle and leisure context — diving conditions, daily vinyl suggestion. Enriching but not urgent; appropriate during waking hours only. |
| **Entertainment** | 4–8 | Wide range reflects the mode: Plex now-playing (8, active playback) vs. Trakt next-up (4, background planning context). Real-time state = high; background context = low. |
| **Ambient** | 3–4 | Mood and atmosphere — good morning/night visuals. Gracefully skipped under contention; no loss if they don't show on a busy day. |

**Existing template mapping:**

| Template | Category | Priority |
|---|---|---|
| `birthdays.self` | Personal | 9 |
| `message.notification` | Social | 8 |
| `notion.notification` | Social | 7 |
| `birthdays.today` | Social | 6 |
| `bart.departures` | Logistics | 8 |
| `weather.conditions` | Logistics | 5 |
| `calendar.today` | Logistics | 5 |
| `diving.conditions` | Hobbies | 5 |
| `discogs.morning_spin` | Hobbies | 5 |
| `diving.last_dive` | Hobbies | 3 |
| `plex.now_playing`, `plex.paused`, `plex.stopped` | Entertainment | 8 |
| `trakt.watching` | Entertainment | 7 |
| `trakt.calendar`, `trakt.next_up` | Entertainment | 4 |
| `morning_night.good_morning`, `morning_night.good_night` | Ambient | 4 |

When adding a new template, identify its category first — that gives you the
priority range to start from before tuning for contention.

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
- **Start at your category's baseline and adjust.** Only raise priority when you observe or anticipate contention.
- **Pair high priority with a short `timeout`.** A high-priority message that is stale is worse than none. Set `timeout` to roughly the window of relevance.
- **Pair low priority with a short `timeout` too.** Background content with a long `timeout` will show up hours late. Either set `timeout` to match the display window, or accept that it may be discarded.
- **Avoid priority inflation.** If everything is 8–10, nothing is. Reserve the top of the scale for the one or two templates where timing genuinely matters.
- **Use `config.toml` overrides to tune without touching JSON.** See [Schedule overrides](#schedule-overrides) below.

### Schedule overrides

Override schedule fields or visibility for any named template directly in
`config.toml`, without editing the content file:

```toml
[bart.schedules.departures]
cron = "*/5 6-9 * * mon-fri"  # extend window to start at 6am
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

### Time-of-day zones

The display serves different purposes at different times of day. Match your
template's schedule to the zone where it's most useful:

| Zone | Hours | Character | Owner |
|---|---|---|---|
| Morning start | 07:00–08:00 | Gentle context-setting | Weather (logistics), good morning visual (ambient) |
| Commute window | 07:00–09:00 weekdays | BART owns this | BART dominates; other content fills gaps between refreshes |
| Morning anchor | 08:00 | Personal ritual | Personal-tier content (pri 9) wins the queue; weather and Trakt calendar follow |
| Morning context | 08:30 | Routine check-in | Calendar events, daily vinyl suggestion |
| Late morning | 09:00–noon | Quiet period | Birthdays, diving last-dive; weather continues hourly |
| Midday | noon–16:00 | Background ambient | Trakt next-up (noon), diving conditions (12:15) |
| Afternoon | 16:00–20:00 | Transition | Weather, Trakt calendar refresh (16:00), diving conditions (16:15) |
| Evening anchor | 20:00 | Personal ritual | Personal-tier content (pri 9) wins the queue; weather and Trakt next-up follow |
| Wind-down | 21:00–21:45 | Closing mood | Good night visual; weather |
| Quiet hours | ~22:00+ | Board silent (software quiet mode) | Crons continue firing; content rendered to virtual state; board wakes with latest content |

Nothing is useful before 07:00 — quiet mode keeps the board dark overnight.
Crons that fire during quiet hours continue rendering to virtual state, so
the board always wakes with contextually relevant content. Avoid scheduling
new integrations in overnight windows unless there's a specific reason
(e.g. an overnight job that needs to be ready by 07:00).

### Schedule map

| Slot | Template | Category | Pri | Hold | Timeout | Notes |
|---|---|---|---|---|---|---|
| `7:00` daily | `morning_night.good_morning` | Ambient | 4 | 300 s | 3600 s | Queues behind weather; Sep 21 variant (pri 4) overrides |
| `:00` every hour | `weather.conditions` | Logistics | 5 | 600 s | 1800 s | Fires first in every `:00` slot |
| `:15` at 07/12/16 | `diving.conditions` | Hobbies | 5 | 600 s | 1800 s | Refresh 1800 s; dedicated `:15` slot avoids `:00` weather |
| `8:00` daily | `birthdays.self` | Personal | 9 | 3600 s | 7200 s | No-op on non-birthday days; dominates the display for 1 h on birthday — intentional |
| `:00` at 08/16 | `trakt.calendar` | Entertainment | 4 | 1200 s | 1800 s | Private; queues behind weather |
| `:30` every hour | `calendar.today` | Logistics | 5 | 300 s | 1800 s | |
| `8:30` daily | `discogs.morning_spin` | Hobbies | 5 | 600 s | 3600 s | Queues behind calendar at `:30` |
| `9:00` daily | `birthdays.today` | Social | 6 | 600 s | 3600 s | Queues behind weather; no-op when no birthdays |
| `:15` at 09/15 | `parcel.deliveries` | Logistics | 5 | 600 s | 1800 s | Private; no-op when no active deliveries |
| `10:00` daily | `diving.last_dive` | Hobbies | 3 | 300 s | 3600 s | Skipped when no dive date recorded |
| `10:45` daily | `qbittorrent.status` | Hobbies | 3 | 300 s | 3600 s | Private; solo `:45` slot; skipped when nothing seeding |
| `11:45` daily | `unraid.status` | Hobbies | 3 | 300 s | 3600 s | Private; solo `:45` slot |
| `:00` at 12/20 | `trakt.next_up` | Entertainment | 4 | 1200 s | 1800 s | Private; noon = plan for evening, 20:00 = settle in |
| `21:00` daily | `morning_night.good_night` | Ambient | 4 | 300 s | 3600 s | Moon phase visual; queues behind weather |
| `*/5` 07–09 Mon–Fri | `bart.departures` | Logistics | 8 | 290 s | 60 s | Refresh 60 s; dominates weekday mornings |
| `*/3` 07–23 daily | `trakt.watching` | Entertainment | 7 | 180 s | 120 s | Refresh 30 s; no-op when not watching |
| webhook only | `plex` (3 templates) | Entertainment | 8 | indef / 60 s | 30 s | Private; interrupts on state change |
| webhook only | `message.notification` | Social | 8 | 120 s | 600 s | Queues normally; shows at next hold break |
| webhook only | `notion.notification` | Social | 7 | 120 s | 120 s | |

**Personal-tier note:** Priority-9 templates (e.g. `birthdays.self`, or
personal pet feeding reminders in `content/user/`) are excluded from the slot
hold budget — they are brief first-position inserts that show quickly and step
aside for the rest of the slot. The `:00` budget (weather + trakt) is 1800 s
after accounting for this exclusion.

### Hold budget

When two templates share a slot, their **combined holds** determine how long
that slot occupies the display. Keep the total under **1800 s (30 min)** to
avoid bleeding into the next slot. This budget applies to non-personal
templates (priority < 9); personal-tier inserts show first and are not counted.

Current worst-case budgets:

- `:00` at 08:00 and 16:00: weather 600 s + trakt.calendar 1200 s = **1800 s** ✓
- `:00` at 12:00 and 20:00: weather 600 s + trakt.next_up 1200 s = **1800 s** ✓
- `:00` all other hours: weather 600 s = **600 s** ✓
- `9:00` daily: weather 600 s + birthdays.today 600 s = **1200 s** ✓
- `8:30` daily: calendar 300 s + discogs 600 s = **900 s** ✓
- `10:00` daily: weather 600 s + diving.last_dive 300 s = **900 s** ✓
- `21:00` daily: weather 600 s + good_night 300 s = **900 s** ✓

The slot budget linter in `tests/test_schedule_lint.py` enforces this
automatically on every `pytest` run.

### Slot conventions

| Minute mark | Convention |
|---|---|
| `:00` | Weather owns this. One additional template at most (combined hold ≤ 1800 s). |
| `:15` | Dedicated slot for a single mid-priority template (diving's home — good pattern to follow). |
| `:30` | Calendar owns this. One additional **daily-only** template is fine; avoid adding a second always-on template. |
| `:45` / `:55` | Clean dedicated slots for brief personal content that needs a clear moment just before the hour. |
| `*/N` sub-hourly | Real-time data only. Always pair with `refresh_interval` and `timeout ≤ 120 s`. |

### Picking a slot for new templates

**Hourly or daily templates** should fire at `:00` or `:30` — pick whichever
has the smaller hold budget. Avoid adding a third always-on template to a slot
that already has two.

**Sub-hourly templates** (e.g. every 5 min) are for real-time data only.
Always set `timeout ≤ 120 s` so stale messages are discarded rather than
accumulating in the queue.

**New slots** (`:15`, `:45`) are available if neither existing slot has room.
Check that an upstream hold doesn't bleed into your chosen minute, and
reference the hold budget table above.

### Pairing `timeout` with `hold`

`timeout` is how long a message can wait in the queue before being discarded.
Set it long enough to survive the hold of whatever it queues behind:

- A priority-5 template firing at `:00` queues behind `weather` (600 s hold).
  Set `timeout >= 600` — otherwise the message expires before `weather` finishes.
- A priority-4 template firing alongside a priority-5 template needs `timeout`
  long enough to outlast both the higher-priority hold *and* queue drain time.
  `timeout = 1800` is a safe floor for low-priority hourly content.

Short `timeout` values (≤ 600 s) are appropriate only for high-priority,
time-sensitive content (e.g. BART departures) where a stale message is worse
than no message.

### Webhook conventions

Webhook templates fire outside the cron schedule and can arrive at any time.
They compete with scheduled content via priority and the interrupt mechanism.

**Interrupt model:**
- Use `interrupt=True` for state changes that need immediate visibility — Plex
  play, pause, and stop events should preempt whatever is currently on the
  display.
- Leave `interrupt=False` (the default) for notifications that can queue
  normally — friend messages are more politely shown at the next hold break
  than by flapping the display mid-scene.

**Indefinite holds:**
- Use `indefinite=True` for continuous-state messages (active Plex playback)
  that should stay on screen until a stop or resume event arrives. Always
  set a `hold` safety ceiling (e.g. 14400 s / 4 h) in case the stop event
  is never received.
- An indefinite Plex hold suppresses all queued cron content until playback
  pauses or stops — this is intentional; Plex content IS the relevant content
  during media playback.

**Known interaction — Plex and friend messages:** Friend messages (`message`)
queue normally with `interrupt=False` and a 600 s timeout. During active Plex
playback, they will show at the next natural pause. If no pause occurs within
10 min the message expires — this is a deliberate trade-off that avoids
disrupting media playback.

**Software quiet mode and the interrupt model:** When software quiet mode is
active (see [`scheduler`](contrib/scheduler.md)), the worker renders content
to virtual state instead of sending it to the board. Cron jobs, webhooks, and
idle refresh all continue running — only the final API call is suppressed.
On wake, the latest virtual state is sent immediately. This avoids the stale-
state issues of hardware quiet hours: the board always wakes with the most
recent content.

**Hardware quiet hours and the interrupt model:** If using the Vestaboard's
built-in quiet hours (instead of or alongside software quiet mode), the board
rejects writes with a locked error — the scheduler clears its hold state and
re-enqueues the message, so from the scheduler's perspective the board is idle.
Two consequences:

- **Webhooks that queue (no board-displacement check):** messages pile up in
  the queue and drain in priority order once quiet hours end. State may be
  briefly stale until the queue catches up.
- **Webhooks with a board-displacement check** (currently only `plex`): state-
  change events (pause, stop) are silently discarded while the board appears
  idle. After quiet hours end the display may show a stale state until the next
  event arrives. Avoid testing Plex state transitions during hardware quiet
  hours — the behaviour is misleading and is not a bug in the integration.

For most setups, software quiet mode is preferred — disable the Vestaboard's
hardware quiet hours and control the display entirely via iOS Shortcuts
automations tied to your sleep schedule.

### Priority reminder

Priority controls queue order, not fire time. Two templates with the same
cron expression fire at the same instant — priority decides which shows first.
Don't inflate priority to "win" — see the content categories and priority
guidelines above.

## Contrib integrations

| File | Description |
|---|---|
| [`bart.json`](contrib/bart.md) | BART real-time departure board |
| [`birthdays.json`](contrib/birthdays.md) | Upcoming birthdays from iCloud Contacts; prominent self-birthday display |
| [`calendar.json`](contrib/calendar.md) | Today's calendar events (ICS and iCloud CalDAV) |
| [`discogs.json`](contrib/discogs.md) | Daily vinyl suggestion from your Discogs collection |
| [`diving.json`](contrib/diving.md) | Scuba diving conditions via NOAA NDBC buoy data or Open-Meteo |
| [`message.json`](contrib/message.md) | Friend messages via iOS Shortcuts → webhook |
| [`morning_night.json`](contrib/morning_night.md) | Good morning and good night messages with moon phase visual |
| [`notion.json`](contrib/notion.md) | Notion automation notifications via webhook |
| [`parcel.json`](contrib/parcel.md) | Upcoming package delivery from Parcel |
| [`plex.json`](contrib/plex.md) | Plex Media Server now-playing via webhook |
| [`qbittorrent.json`](contrib/qbittorrent.md) | Seeding stats from qBittorrent Web UI |
| (no JSON) [`scheduler`](contrib/scheduler.md) | Software-side quiet mode — webhook-only, no display content |
| [`trakt.json`](contrib/trakt.md) | Trakt.tv upcoming calendar and now-playing |
| [`unraid.json`](contrib/unraid.md) | Unraid server status — array capacity and uptime |
| [`weather.json`](contrib/weather.md) | Current weather conditions via Open-Meteo |
