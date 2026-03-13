# morning_night.json

Good morning and good night messages. Good morning fires at 7:00 AM with a
weather-based color visual. Good night fires at 9:00 PM with the current moon
phase displayed as a visual grid. September 21st gets a special Earth, Wind &
Fire themed morning message. Ambient — fires once daily at morning and night.

## Schedule

### `good_morning`

**Cron:** `0 7 * * *` — fires daily at 07:00.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 4 (Ambient)

Fires in the `7:00` slot alongside `weather` (pri 5, 600 s) — combined budget
900 s. Weather shows first (priority 5 > 4); `good_morning` follows for 5 min.
The 3600 s timeout means `good_morning` survives even on BART weekday mornings
where the commute window (07:00–09:00) fills the queue.

### `good_morning_september`

**Cron:** `0 7 21 9 *` — fires at 07:00 on September 21st only.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 4 (Ambient)

Same slot as `good_morning`; at the same priority, the scheduler processes
whichever was registered first. Use a `config.toml` override to give it
priority 5 if you want it to appear before weather on that day.

### `good_night`

**Cron:** `0 21 * * *` — fires daily at 21:00.
**Hold:** 300 s | **Timeout:** 3600 s | **Priority:** 4 (Ambient)

Fires in the `21:00` slot alongside `weather` (pri 5, 600 s) — combined
budget 900 s. Weather shows first; the moon phase visual follows. Aim for
`good_night` to be the last thing visible before quiet hours (default 21:45).

**Align cron to your quiet hours.** The defaults (`0 7` and `0 21`) can be
moved in `config.toml` without editing this file:

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
