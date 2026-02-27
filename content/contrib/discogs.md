# discogs.json

Daily vinyl suggestion from your Discogs collection. Picks a random record
each morning at 8am and displays the album title and artist.

## Configuration

Add the following to your `config.toml`:

```toml
[discogs]
token = "your-discogs-personal-access-token"
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
