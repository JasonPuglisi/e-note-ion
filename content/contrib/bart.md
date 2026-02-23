# bart.json

BART real-time departure board. Shows upcoming departures from a configured
originating station across 1–2 lines, with line colors as Vestaboard color
squares. Line colors are derived dynamically from the BART routes API on
first call and cached for the process lifetime. Runs every 5 minutes on
weekday mornings (07:00–09:00).

## Configuration

Add the following to your `config.toml`:

```toml
[bart]
api_key = "your-bart-api-key"
station = "MLPT"
line1_dest = "DALY"
# line2_dest = "BERY"  # optional second departure line
```

| Key | Required | Description |
|---|---|---|
| `api_key` | Yes | Free API key — register at https://api.bart.gov/api/register.aspx |
| `station` | Yes | Originating station abbreviation code (e.g. `MLPT` for Milpitas) |
| `line1_dest` | Yes | Destination station abbreviation code for the first departure line (e.g. `DALY` for Daly City) |
| `line2_dest` | No | Destination abbreviation code for an optional second line |

All station values use abbreviation codes only (e.g. `MLPT`, `DALY`, `BERY`).
`line1_dest` / `line2_dest` are matched case-insensitively against the
`abbreviation` field in the BART ETD API response.

## Keeping data current

### Station codes

Authoritative source: https://api.bart.gov/docs/overview/abbrev.aspx

Station abbreviation codes in `config.toml` must match the codes used by the
BART API. When BART opens new stations, update accordingly.

### Terminal destinations

`line1_dest` / `line2_dest` values must match the `abbreviation` field in BART
ETD API responses. To see current abbreviations for departures from a station,
call the API directly (requires an API key):

```
https://api.bart.gov/api/etd.aspx?cmd=etd&orig=MLPT&key=<key>&json=y
```

For current lines and termini without an API key, check the schedule PDFs:
https://www.bart.gov/schedules/pdfs

When BART changes line termini, update the destination codes in `config.toml`.
No code changes are needed — line colors are derived dynamically from the routes
API and will update automatically when the integration restarts.
