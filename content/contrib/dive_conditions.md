# dive_conditions.json

Scuba diving conditions — wave height, swell period, wind, and water
temperature. Shows at 7:15am, 12:15pm, and 4:15pm for pre-dive planning.
A color square in the header indicates subjective dive condition quality.

## Configuration

Add the following to your `config.toml`:

```toml
[dive_conditions]
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
```

| Key | Required | Description |
|---|---|---|
| `ndbc_station_id` | Yes (or lat/lon) | NDBC buoy station ID |
| `latitude` | Yes (if no NDBC) | Decimal latitude for Open-Meteo fallback |
| `longitude` | Yes (if no NDBC) | Decimal longitude for Open-Meteo fallback |
| `units` | No | `"imperial"` (default) or `"metric"` |

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
