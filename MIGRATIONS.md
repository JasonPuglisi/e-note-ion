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

### Everything else is unchanged

Content JSON, CLI flags, the webhook and `/state` API shapes, and every other
`config.toml` key carry over as-is. Contrib templates need no edits.
