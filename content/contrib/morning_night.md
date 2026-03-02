# morning_night.json

Good morning and good night messages. Good morning fires at 7:00 AM with
rotating greetings. Good night fires at 9:00 PM with the current moon phase
displayed as a visual grid. September 21st gets a special Earth, Wind & Fire
themed morning message.

## Configuration

No configuration required. The moon phase is calculated locally using a
pure-math formula — no API key or network access needed.

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
