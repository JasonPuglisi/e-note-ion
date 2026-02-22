# bart.json

BART real-time departure board. Shows upcoming departures from a configured
originating station across 1–2 lines, with line colors as Vestaboard color
squares. Line colors are derived dynamically from the BART routes API on
first call and cached for the process lifetime. Runs every 5 minutes on
weekday mornings (07:00–09:00).

## Configuration

| Variable | Required | Description |
|---|---|---|
| `BART_API_KEY` | Yes | Free API key — register at https://api.bart.gov/api/register.aspx |
| `BART_STATION` | Yes | Originating station abbreviation code (e.g. `MLPT` for Milpitas) |
| `BART_LINE_1_DEST` | Yes | Destination station abbreviation code for the first departure line (e.g. `DALY` for Daly City) |
| `BART_LINE_2_DEST` | No | Destination abbreviation code for an optional second line |

All station values use abbreviation codes only (e.g. `MLPT`, `DALY`, `BERY`).
`BART_LINE_x_DEST` is matched case-insensitively against the `abbreviation`
field in the BART ETD API response.

## Keeping data current

### Station codes

Authoritative source: https://api.bart.gov/docs/overview/abbrev.aspx

The `BART_STATION` dropdown in `unraid/e-note-ion.xml` uses
`CODE - Display Name` format. When BART opens new stations, add entries to
the pipe-separated `Default=` list in that file.

### Terminal destinations

`BART_LINE_x_DEST` values must match the `abbreviation` field in BART ETD API
responses. To see current abbreviations for departures from a station, call
the API directly (requires an API key):

```
https://api.bart.gov/api/etd.aspx?cmd=etd&orig=MLPT&key=<key>&json=y
```

For current lines and termini without an API key, check the schedule PDFs:
https://www.bart.gov/schedules/pdfs

When BART changes line termini, update the destination dropdown in
`unraid/e-note-ion.xml` to reflect the new abbreviation codes. No code
changes are needed — line colors are derived dynamically from the routes API
and will update automatically when the integration restarts.
