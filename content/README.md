# Content

This directory contains the JSON files that define what gets displayed on the
board. Files are watched at runtime — add, edit, or remove a file and it takes
effect within a few seconds without restarting.

See the root [README](../README.md) for the full content file format.

## `contrib/`

Bundled community-contributed content. These files ship with the project and
are available to all users, but are **disabled by default**. Enable individual
files by name using `--content-enabled` (or the `CONTENT_ENABLED` env var):

```bash
python e-note-ion.py --content-enabled aria         # enable one file
python e-note-ion.py --content-enabled aria,bart    # enable multiple
python e-note-ion.py --content-enabled '*'          # enable all
```

To contribute content, open a pull request adding a JSON file here.

### Files

#### `aria.json`

Sample content for Aria, a cat. Schedules breakfast and dinner reminders at
08:00 and 20:00, with an hourly default message, each paired with a random
cat-themed quip.

#### `bart.json`

BART real-time departure board. Shows upcoming departures from a configured
originating station across 1–2 lines, with line colors as Vestaboard color
squares. Runs every 5 minutes on weekday mornings (06:00–10:00).

**Required env vars:** `BART_API_KEY`, `BART_STATION`, `BART_LINE_1_DEST`
**Optional env vars:** `BART_LINE_2_DEST`

Free API key: https://api.bart.gov/api/register.aspx

##### Keeping BART data current

The Unraid template (`unraid/e-note-ion.xml`) contains hardcoded dropdowns for
station codes and terminal destinations. These need updating when BART opens
new stations or changes line termini.

**Station codes** — authoritative source:
https://api.bart.gov/docs/overview/abbrev.aspx
The `BART_STATION` dropdown uses `CODE - Display Name` format. `bart.py`
parses only the code (splits on first space), so labels are for display only.

**Terminal destinations** — the `BART_LINE_x_DEST` values are substring-
matched against destination names returned by the BART ETD API. To see current
destinations for a station, call the API directly (requires an API key):
```
https://api.bart.gov/api/etd.aspx?cmd=etd&orig=MLPT&key=<key>&json=y
```
For current lines and termini without an API key, check the schedule PDFs:
https://www.bart.gov/schedules/pdfs

When updating, change both the `Default=` dropdown list in
`unraid/e-note-ion.xml` and verify that the destination strings match what
the ETD API actually returns (the `destination` field in each `etd` entry).

## `user/`

Your personal content. Files placed here are always loaded automatically —
no opt-in needed. This directory is git-ignored so personal schedules are
never committed to the project repo.

To version your personal content, create a private git repository containing
your files, clone it on your server, and volume-mount it at
`/app/content/user` (Docker) or point it at this directory directly.
