# discogs.json

Daily vinyl suggestion from your Discogs collection. Picks a random record
each morning at 8:30am and displays the album title and artist. Hobbies —
fires once daily at 08:30.

## Schedule

**Cron:** `30 8 * * *` — fires daily at 08:30.
**Hold:** 600 s | **Timeout:** 3600 s | **Priority:** 5 (Hobbies)

Fires in the `:30` slot alongside `calendar.today` (pri 5, 300 s hold) —
combined budget 900 s, well within the 1800 s ceiling. Both are priority 5;
`calendar` shows first (it fires every `:30`; `discogs` only at 8:30, so the
scheduler processes `calendar` first). The 3600 s timeout gives `discogs`
plenty of room to queue behind `calendar` and any earlier-slot spillover.

To override schedule fields without editing this file:

```toml
[discogs.schedules.morning_spin]
cron = "30 9 * * *"  # push to 9:30am if 8:30 is too early
hold = 300
disabled = true       # skip entirely
```

## Configuration

Add the following to your `config.toml`:

```toml
[discogs]
api_key = "your-discogs-personal-access-token"
```

| Key | Required | Description |
|---|---|---|
| `token` | Yes | Personal access token (read-only). Generate at https://www.discogs.com/settings/developers |
| `folder_id` | No | Collection folder ID (default: `0` = all releases) |

Your Discogs username is resolved automatically from the token via
`GET /oauth/identity` on first call and cached for the process lifetime —
no username config key required.

The integration makes at most three API calls per fire (once daily): one
identity lookup on first run (then cached), one to read the total collection
size, and one to fetch the randomly selected record. Selection is uniformly
random — every record in your collection has equal probability regardless of
collection size.

## Keeping data current

### API

Discogs API documentation: https://www.discogs.com/developers/

The integration uses the collection releases endpoint
(`GET /users/{username}/collection/folders/{folder_id}/releases`). If the
API endpoint or response structure changes, update `_API_BASE` and the
field access in `_format_artist`/`_format_album` in
`integrations/discogs.py`.
