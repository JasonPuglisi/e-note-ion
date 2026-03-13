# diving.json

Two templates:

- **conditions** — wave height, swell period, wind, and water temperature.
  Shows at 7:15am, 12:15pm, and 4:15pm for pre-dive planning. Color square
  reflects subjective dive condition quality.
- **last_dive** — days since your last dive. Shows daily at 10am when a dive
  date has been recorded via webhook or set manually.

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
# Wind is always displayed in knots regardless of this setting.
# units = "imperial"

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

**Build the Shortcut:**

1. Open Shortcuts → tap **+** → name it "Log Dive"
2. Add **Date** → set to **Current Date**
3. Add **Format Date** → Date: Current Date result from step 2, Format: **Custom**,
   Custom Format: `yyyy-MM-dd`, Time Zone: **Current** (rename output to `dived_on`
   when wiring the next step)
4. Add **Dictionary** → add one key: `dived_on` (Type: Text) → value: `dived_on`
   magic variable from step 3
5. Add **Get Contents of URL**:
   - URL: `https://<your-webhook-url>/webhook/diving` (replaced by import question)
   - Method: **POST**
   - Headers: add `X-Webhook-Secret` → value: the Text action below (replaced by import question)
   - Body: **JSON** → insert the Dictionary from step 4
   - No `Content-Type` header needed — Shortcuts sets it automatically for JSON bodies
6. Add a **Text** action for the secret (this is what becomes the import question value):
   paste a placeholder; this will be replaced on import
7. Tap the **ⓘ** button → **Import Questions** → add two questions:
   - Question: `Webhook URL`, Parameter: URL field of Get Contents of URL, Default: blank
   - Question: `Webhook secret`, Parameter: the Text action from step 6, Default: blank

> **Note on step ordering:** In Shortcuts, the Text action for the secret (step 6)
> must appear *before* the Get Contents of URL step (step 5) so it can be wired as
> the header value. Add it between steps 4 and 5 when building.

Share via iCloud link → import to your device and fill in the two import questions
with your webhook URL and the plaintext secret from the scheduler log.

## Condition scoring

The header color square reflects subjective dive condition quality based on
wave height and wind speed. The worst of the two determines the rating, with
a period modifier that bumps marginal conditions to poor when waves are short
and choppy (period / wave height < 2).

| Color | Wave height | Wind speed |
|---|---|---|
| 🟩 Green | ≤ 2 ft | ≤ 10 kt |
| 🟨 Yellow | ≤ 4 ft | ≤ 20 kt |
| 🟥 Red | > 4 ft | > 20 kt |

Thresholds are based on recreational dive operator consensus. Conditions that
are yellow by height and wind but have a period-to-height ratio below 2 are
shown as red (e.g. 3 ft waves at 5s period). Yellow is shown when key data
fields are unavailable.

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
