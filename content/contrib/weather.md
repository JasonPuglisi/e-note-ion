# weather.json

Current weather conditions for a configured city. Shows city name, weather
condition with a color indicator, and temperature with daily high/low. Runs
hourly.

## Configuration

Add the following to your `config.toml`:

```toml
[weather]
city = "San Francisco"
units = "imperial"
```

| Key | Required | Description |
|---|---|---|
| `city` | Yes | City name; geocoded via the Open-Meteo geocoding API on first call |
| `units` | No | `"imperial"` (°F, mph, default) or `"metric"` (°C, km/h) |

The city name is resolved to coordinates on first call and cached for the
process lifetime. The canonical city name from the API response is always used
in templates — typos and capitalisation differences in `config.toml` are
corrected automatically.

No API key is required. Open-Meteo is free for non-commercial use.

## Keeping data current

### API endpoint

Open-Meteo is a free, open-source weather API. If the endpoint changes,
update `_GEOCODING_URL` and `_FORECAST_URL` in `integrations/weather.py`.
API documentation: https://open-meteo.com/en/docs

### WMO weather codes

Weather conditions are derived from WMO weather interpretation codes returned
by the Open-Meteo API. The code-to-condition mapping lives in `_WMO_CONDITIONS`
in `integrations/weather.py`. If new codes are introduced, add them to that
dict. Reference: https://open-meteo.com/en/docs#weathervariables
