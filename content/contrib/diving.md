# diving.json

Two templates. Hobbies — fires three times daily (conditions) and once daily
(last dive).

- **conditions** — wave height, swell period, wind, and water temperature.
  Shows at 7:15am, 12:15pm, and 4:15pm for pre-dive planning. Color square
  reflects subjective dive condition quality.
- **last_dive** — days since your last dive. Shows daily at 10am when a dive
  date has been recorded via webhook or set manually.

## Schedule

### `conditions`

**Cron:** `15 7,12,16 * * *` — fires at 07:15, 12:15, and 16:15 daily.
**Hold:** 600 s | **Timeout:** 1800 s | **Priority:** 5 (Hobbies)
**Refresh interval:** 1800 s (re-fetches ocean data every 30 min during hold)

Fires in the dedicated `:15` slot, which has no other scheduled occupants.
This avoids the `:00` weather slot entirely. Combined budget at `:15` is
600 s — the full slot budget is available.

### `last_dive`

**Cron:** `0 10 * * *` — fires daily at 10:00.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 3 (Hobbies)

Fires in the `10:00` slot alongside `weather` (pri 5, 600 s) — combined
budget 900 s. At priority 3 it queues behind weather, showing after weather's
10-minute hold. Skipped when no dive date has been recorded.

To override schedule fields without editing this file:

```toml
[diving.schedules.conditions]
cron = "15 8,13,17 * * *"  # shift to later windows
hold = 300
disabled = true             # skip entirely

[diving.schedules.last_dive]
priority = 4
disabled = true
```

## Configuration

Add the following to your `config.toml`:

```toml
[diving]
# NDBC buoy station ID (recommended for US coastal sites — real measured data).
# Find your nearest station at https://www.ndbc.noaa.gov/
# Example: 46014 = Point Arena, CA
ndbc_station_id = "46014"

# Open-Meteo fallback: use these instead of ndbc_station_id for non-US sites
# or locations without a nearby buoy.
# WARNING: Open-Meteo's own docs warn that accuracy near complex coastlines
# is limited (8 km grid resolution). Use NDBC where possible.
# latitude = 36.6
# longitude = -121.9

# Unit system: "imperial" (ft, °F, default) or "metric" (m, °C).
# units = "imperial"

# Wind display unit: "knots" (default), "mph", or "kmh". Condition scoring
# always uses knots internally — only the displayed wind value changes.
# wind_units = "knots"

# Date of the most recent dive — written by the webhook, or set manually.
# Format: YYYY-MM-DD. The last_dive template shows once this is present.
# last_dived_on = "2026-01-01"
```

| Key | Required | Description |
|---|---|---|
| `ndbc_station_id` | Yes (or lat/lon) | NDBC buoy station ID |
| `latitude` | Yes (if no NDBC) | Decimal latitude for Open-Meteo fallback |
| `longitude` | Yes (if no NDBC) | Decimal longitude for Open-Meteo fallback |
| `units` | No | `"imperial"` (default) or `"metric"` |
| `wind_units` | No | `"knots"` (default), `"mph"`, or `"kmh"` — display only |
| `last_dived_on` | No | Most recent dive date (`YYYY-MM-DD`); written by webhook |

## Webhook setup

The `last_dive` template is populated via `POST /webhook/diving`.
A named credential (`diving`) is auto-generated on first scheduler
startup — check the log for the plaintext secret to use in your iOS Shortcut.

**Payload:**
```json
{"dived_on": "2026-03-12"}
```

The webhook only stores the date; the display fires on the daily 10am cron.
To record a dive immediately without waiting, set `last_dived_on` manually in
`config.toml`.

## iOS Shortcut

A one-tap Shortcut that sends today's date to the webhook. No prompts — tap it
when you surface.

A pre-built template is available at
`content/contrib/shortcuts/Log Vestaboard Dive.shortcut`. Import it and fill in
the two import questions. To build manually:

**Build the Shortcut:**

1. Open Shortcuts → tap **+** → name it "Log Vestaboard Dive"
2. Add **Date** → set to **Current Date**
3. Add **Format Date** → Date: Current Date result from step 2, Format: **Custom**,
   Custom Format: `yyyy-MM-dd`, Locale: default (rename output to `dived_on`
   when wiring the next step)
4. Add a **Text** action for the secret (this becomes an import question):
   paste a placeholder; renamed to `secret` when wiring the next step
5. Add **Get Contents of URL**:
   - URL: `https://<your-webhook-url>/webhook/diving` (replaced by import question)
   - Method: **POST**
   - Headers: add `X-Webhook-Secret` → `secret` magic variable from step 4
   - Body: **JSON** → add key `dived_on` (Type: Text) → `dived_on` magic variable
     from step 3
   - No `Content-Type` header needed — Shortcuts sets it automatically for JSON bodies
6. Add **List** → 5 items:
   `Logged! Time to decompress. 🤿`, `Surface interval started. 🫧`,
   `Dive recorded! The flaps are flipping. 🌊`, `Logged to the board! 🐠`,
   `Splashdown recorded. 💧`
7. Add **Get Item from List** → Input: List from step 6, Item: **Random Item**;
   rename output to `confirmation` when wiring the next step
8. Add **Show Alert** → Message: `confirmation` magic variable from step 7;
   untick Show Cancel Button; leave title blank
9. Tap the **ⓘ** button → **Import Questions** → add two questions:
   - Question: `Webhook URL`, Parameter: URL field of Get Contents of URL, Default: blank
   - Question: `Webhook secret`, Parameter: the Text action from step 4, Default: blank

Share via iCloud link → import to your device and fill in the two import questions
with your webhook URL and the plaintext secret from the scheduler log.

## Condition scoring

The header color square reflects subjective dive condition quality based on
wave height and wind speed. The worst of the two determines the rating, with
a period modifier that bumps marginal conditions to poor when waves are short
and choppy (period / wave height < 1.5).

| Color | Wave height | Wind speed |
|---|---|---|
| 🟩 Green | ≤ 3 ft | ≤ 12 kt |
| 🟨 Yellow | ≤ 5 ft | ≤ 20 kt |
| 🟥 Red | > 5 ft | > 20 kt |

Thresholds are based on recreational dive operator consensus: 3 ft is the
beginner-friendly upper bound, 5 ft is the typical op-cancel zone, and 20 kt
matches the NWS Small Craft Advisory lower bound. Conditions that are yellow
by height and wind but have a period-to-height ratio below 1.5 are shown as
red (e.g. 3 ft waves at 4s period). Yellow is shown when key data fields are
unavailable.

## Keeping data current

### NDBC stations

NOAA adds and decommissions buoys over time. Verify your station is still
active and reporting:

- Station list: https://www.ndbc.noaa.gov/to_station.shtml
- Station page (replace ID): https://www.ndbc.noaa.gov/station_page.php?station=46014
- Realtime data file: https://www.ndbc.noaa.gov/data/realtime2/46014.txt

If a station stops reporting, check the NDBC station page for status. Nearby
stations or the Open-Meteo fallback can substitute while a buoy is offline.

### Open-Meteo Marine API

Open-Meteo updates their model data continuously. No action required unless
the API endpoint or variable names change:

- Marine API docs: https://open-meteo.com/en/docs/marine-weather-api
- Forecast API docs: https://open-meteo.com/en/docs
