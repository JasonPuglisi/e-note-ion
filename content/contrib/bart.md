# bart.json

BART real-time departure board. Shows upcoming departures from a configured
originating station across 1–2 lines, with line colors as Vestaboard color
squares. Runs every 5 minutes on weekday mornings (07:00–09:00).

## Configuration

| Variable | Required | Description |
|---|---|---|
| `BART_API_KEY` | Yes | Free API key — register at https://api.bart.gov/api/register.aspx |
| `BART_STATION` | Yes | Originating station code (e.g. `MLPT` for Milpitas) |
| `BART_LINE_1_DEST` | Yes | Destination name to match for the first departure line |
| `BART_LINE_2_DEST` | No | Destination name for an optional second line |

`BART_STATION` accepts either a raw code (`MLPT`) or the Unraid dropdown
format (`MLPT - Milpitas`) — only the code is used. `BART_LINE_x_DEST` values
are matched as substrings against the destination names returned by the BART
ETD API, so partial matches work (e.g. `Richmond` matches `Richmond`).

## Keeping data current

### Station codes

Authoritative source: https://api.bart.gov/docs/overview/abbrev.aspx

The `BART_STATION` dropdown in `unraid/e-note-ion.xml` uses
`CODE - Display Name` format. When BART opens new stations, add entries to
the pipe-separated `Default=` list in that file.

### Terminal destinations

`BART_LINE_x_DEST` values must match the `destination` field in BART ETD API
responses, which uses the terminal station name. To see current destinations
for a station, call the API directly (requires an API key):

```
https://api.bart.gov/api/etd.aspx?cmd=etd&orig=MLPT&key=<key>&json=y
```

For current lines and termini without an API key, check the schedule PDFs:
https://www.bart.gov/schedules/pdfs

When BART changes line termini, update the destination dropdown in
`unraid/e-note-ion.xml` and verify the new names match what the API returns.
