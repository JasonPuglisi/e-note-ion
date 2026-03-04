# morning_night.json

Good morning and good night messages. Good morning fires at 7:00 AM with a
weather-based color visual. Good night fires at 9:00 PM with the current moon
phase displayed as a visual grid. September 21st gets a special Earth, Wind &
Fire themed morning message.

## Configuration

No configuration required for the moon phase (calculated locally, no API key
needed). The morning visual uses weather data when available:

```toml
[weather]
city = "San Francisco, CA"
units = "imperial"
```

If `[weather]` is not configured, or if the weather fetch fails, the morning
visual falls back to a default sunrise grid.

Align the cron expressions to your board's hardware-configured quiet hours.
The defaults (`0 7 * * *` and `0 21 * * *`) can be overridden per-template
in `config.toml` without editing this file:

```toml
[morning_night.schedules.good_morning]
cron = "15 6 * * *"

[morning_night.schedules.good_morning_september]
cron = "15 6 21 9 *"

[morning_night.schedules.good_night]
cron = "30 21 * * *"
```

## Morning weather visual

The good morning template displays a 7×3 color grid on the left, with `GOOD`
and `MORNING` anchored to the right on rows 1 and 2 — mirroring the good night
layout. The visual adapts to current conditions:

| Condition | Visual |
|---|---|
| Clear / mostly clear | Orange/yellow sunrise arc |
| Partly cloudy | Sunrise arc with white cloud patches |
| Overcast / fog | White cloud fill |
| Light drizzle / rain | Sparse blue rain-drop columns |
| Moderate–heavy rain | Denser blue rain columns |
| Snow | White dot scatter on black |
| Thunderstorm | Red fill with dark gaps |

Weather data is fetched via the Open-Meteo API (same source as `weather.json`)
and shares the weather integration's process-level forecast cache — at most one
API call between them regardless of order.

## Moon phase visual

The good night template displays a 3×5 grid of white `[W]` and black `[K]`
squares approximating the current moon shape. Waxing phases are right-lit,
waning phases are left-lit (northern hemisphere convention). New Moon uses a
hollow outline to remain visible against the dark board background.

The moon grid occupies the left 5 columns across all 3 rows; `GOOD` and
`NIGHT` anchor to the right on rows 1 and 2.

## Keeping data current

### Moon phase algorithm

The phase calculation uses a fixed reference epoch (new moon on
2000-01-06 18:14 UTC) and the mean synodic period (29.53059 days).
No external data sources to update.
