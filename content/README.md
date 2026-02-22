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
python e-note-ion.py --content-enabled bart        # enable one file
python e-note-ion.py --content-enabled bart,other  # enable multiple
python e-note-ion.py --content-enabled '*'         # enable all
```

To contribute content, open a pull request adding a `.json` file and a
companion `.md` doc (see template in `CLAUDE.md`).

### Files

| File | Description |
|---|---|
| [`bart.json`](contrib/bart.md) | BART real-time departure board |

## `user/`

Your personal content. Files placed here are always loaded automatically —
no opt-in needed. This directory is git-ignored so personal schedules are
never committed to the project repo.

To version your personal content, create a private git repository containing
your files, clone it on your server, and volume-mount it at
`/app/content/user` (Docker) or point it at this directory directly.
