# Migrations

Steps for moving between major versions. Minor and patch releases never need
anything here — if a release is not listed, upgrading is a straight swap.

Newest first.

## 1.x → 2.0

2.0 removes two config formats that 1.x auto-migrated on startup. The migration
code is gone, so these need a one-time manual edit. Both are quick.

**`[scheduler.quiet]` → a flat key.** Replace:

```toml
[scheduler.quiet]
active = true
```

with `quiet = true` under `[scheduler]`, and delete the old section.

**How you'll notice:** the scheduler refuses to start, with
`Error: [scheduler] quiet must be true or false in config.toml, found {'active': True}`.
That is deliberate. The old shape read as *enabled* whatever `active` was set
to, so a config saying `active = false` would have silently turned quiet mode
on — starting anyway would have been worse than not starting.

**Flat message credentials → the nested namespace.** Rename any
`[webhook.credentials.<name>]` section whose `webhooks = ["message"]`:

| Old | New |
|---|---|
| `[webhook.credentials.message-admin]` | `[webhook.credentials.message.admin]` |
| `[webhook.credentials.alice]` | `[webhook.credentials.message.friend.alice]` |

Keep the `secret_hash`; drop the now-redundant `webhooks` line. Re-running
friend registration recreates them correctly if you would rather not edit by
hand. Credentials for every *other* integration (`plex`, `notion`, and so on)
are unchanged and stay flat.

**How you'll notice:** nothing at startup — a stale section is simply ignored.
Friends' passphrases stop working and their messages get a `401`. This is the
one change worth doing *before* you upgrade rather than after.

### Credential keys renamed

Two integrations used a different key name for the same thing. Everything is
`api_key` now:

| Section | Old key | New key |
|---|---|---|
| `[discogs]` | `token` | `api_key` |
| `[tmdb]` | `api_read_access_token` | `api_key` |

The values are unchanged — just rename the key. Every other integration already
used `api_key` and needs no edit.

**How you'll notice:** the two fail differently, and only one is loud.

- **Discogs** raises `Missing required config key [discogs].api_key in
  config.toml` when its template fires, which the scheduler records as an
  integration error and surfaces on `/health`.
- **TMDb** is *optional*, so `is_configured()` simply returns false and it goes
  dormant with no error at all. The visible symptom is Plex and Trakt cards
  showing raw Plex titles instead of canonical ones — easy to miss.

> The integration-test environment variables (`DISCOGS_TOKEN`,
> `TMDB_API_READ_ACCESS_TOKEN`) keep their names. They are not `config.toml`
> keys, and renaming them would break CI until the corresponding GitHub
> environment secrets were renamed by hand.

### OAuth state moved to its own subsection

`[google]` and `[trakt]` mixed credentials you supply with tokens the app
writes for itself. The machine-managed three now live in a subsection:

```toml
[trakt]
client_id = "..."        # yours, unchanged
client_secret = "..."    # yours, unchanged
calendar_days = 7        # yours, unchanged

[trakt.auth]             # written by the auth flow — do not edit
access_token = "..."
refresh_token = "..."
expires_at = 1234567890
```

Same shape for `[google]`. Move `access_token`, `refresh_token` and
`expires_at` into a `[<section>.auth]` block; leave everything else where it is.

**Put the `.auth` block last in its section.** TOML section headers are
positional, so any plain `[trakt]` key written *below* `[trakt.auth]` becomes
part of the subsection and stops being read. This is the one edit here with a
sharp edge.

If you would rather not hand-edit, deleting the three keys works too: the app
re-runs its auth flow and writes the subsection itself.

**How you'll notice:** the integration logs its auth-pending message and prints
fresh authorisation instructions on the next run, as though it had never been
authorised. Nothing is lost — approving again repopulates it.

### Everything else is unchanged

Content JSON, CLI flags, the webhook and `/state` API shapes, and every other
`config.toml` key carry over as-is. Contrib templates need no edits.
