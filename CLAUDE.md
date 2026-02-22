# e-note-ion

A cron-based content scheduler for a Vestaboard split-flap display. Supports
both the **Note** (3 rows × 15 columns) and the **Flagship** (6 rows × 22
columns). Each character can show one of 64 values: A–Z, 0–9, punctuation,
colored squares, or ❤️ (Note) / ° (Flagship) at code 62. The display connects
over Wi-Fi and is controlled via a Read/Write API key.

## Project Structure

```
e-note-ion.py               # Entry point — scheduler, queue, worker
integrations/vestaboard.py  # Vestaboard API client (get_state, set_state)
content/                    # JSON files defining scheduled display content
  aria.json                 # Example content file
Dockerfile                  # Single-stage image using ghcr.io/astral-sh/uv
entrypoint.sh               # Translates FLAGSHIP/PUBLIC env vars to CLI flags
.github/workflows/
  ci.yml                    # Runs checks on every push and pull request to main
  release.yml               # Builds + pushes multi-arch image to ghcr.io on release
SECURITY.md                 # Vulnerability disclosure policy and API key guidance
unraid/
  e-note-ion.xml            # Unraid Community Applications template
```

## How It Works

1. `load_content()` reads all JSON files from `content/` and registers each
   template as an APScheduler cron job (using `BackgroundScheduler`).
2. When a job fires, it calls `enqueue()`, which pushes a `QueuedMessage` into a
   `PriorityQueue`.
3. A single worker thread calls `pop_valid_message()` in a loop, which blocks
   until a message is available, discarding any that have exceeded their
   `timeout`. It then sends the message to the display and sleeps for `hold`
   seconds before processing the next one.

The single-threaded worker ensures display messages never overlap — important
for a physical split-flap device whose flaps need time to settle.

## Content JSON Format

```json
{
  "templates": {
    "<template_name>": {
      "schedule": {
        "cron": "0 8 * * *",  // standard 5-field cron expression
        "hold": 600,  // seconds to show before pulling next
        "timeout": 600  // seconds message can wait before discarding
      },
      "priority": 5,           // integer 0–10; higher number = higher priority
      "public": true,          // if false, skipped when running with --public
      "templates": [
        { "format": ["LINE ONE", "LINE {variable}"] }
      ]
    }
  },
  "variables": {
    "<variable_name>": [
      ["VALUE A LINE 1", "VALUE A LINE 2"],
      ["VALUE B LINE 1", ...]
    ]
  }
}
```

Each content file belongs to a named person/context (e.g. `aria.json`).

The `public` field controls visibility mode. When the program is run with
`--public`, only templates with `"public": true` are scheduled — useful when
the display is in a shared or guest-visible space. Templates with
`"public": false` are personal/private and only run in the default mode.

Variables are lists of options substituted into `{variable}` placeholders in
template format strings. Each option is itself a list of strings (one per
line). A format entry that is exactly `{variable}` expands into all lines of
the chosen option; an inline `{variable}` within other text is replaced by the
first line. Options are chosen at random. When a template has multiple
`{ "format": [...] }` entries, one is also chosen at random.

After variable expansion, lines are automatically word-wrapped to fit
`model.cols`. Words are packed greedily; a word that alone exceeds the column
width is hard-truncated. If wrapping produces more lines than `model.rows`,
the excess is silently dropped. This means content from dynamic sources (e.g.
API responses) doesn't need to be pre-fitted to the board dimensions.

## Priority Queue Behaviour

- `PriorityQueue` is a min-heap; `QueuedMessage.__lt__` inverts priority so
  higher numeric priority is popped first.
- Ties are broken by `seq` (insertion order) — earlier enqueued wins.
- If a higher-priority message arrives while a lower-priority one is holding the
  display, it will be run next once `hold` expires.
- If a message waits longer than its `timeout` (e.g. blocked by a long-holding
  higher-priority message), it is silently discarded.

## Runtime Arguments

```
python e-note-ion.py             # run all templates (Note, 3×15)
python e-note-ion.py --flagship  # target a Flagship board (6×22)
python e-note-ion.py --public    # run only templates with public: true
```

`--flagship` and `--public` can be combined.

## Environment

- `VESTABOARD_KEY` — Vestaboard Read/Write API key (required)
- Python version managed via `.python-version` (uv)
- Dependencies managed with `uv` / `pyproject.toml`
- Dev tools: `ruff` (lint + format), `pyright` (type checking), `bandit`
  (security linting), `pip-audit` (dependency CVE scanning), `pre-commit`
- Run checks: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyright`, `uv run bandit -c pyproject.toml -r .`, `uv run pip-audit`
- Install hooks (once after cloning): `uv run pre-commit install`

## Docker

The image is built on `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` and
published to `ghcr.io/jasonpuglisi/e-note-ion` via GitHub Actions on each
release. Multi-arch: `linux/amd64` and `linux/arm64`.

Runtime configuration via environment variables (used by `entrypoint.sh`):

| Variable        | Values        | Default | Effect                            |
|-----------------|---------------|---------|-----------------------------------|
| `VESTABOARD_KEY`| string        | —       | API key (required)                |
| `FLAGSHIP`      | `true`/`false`| `false` | Targets Flagship (6×22) board     |
| `PUBLIC`        | `true`/`false`| `false` | Restricts to public templates     |

Content files are mounted at `/app/content`. Example Unraid path:
`/mnt/user/appdata/e-note-ion/content`.

The Unraid Community Applications template is at `unraid/e-note-ion.xml`.

## Development Workflow

Never commit directly to `main`. Always work on a named branch and open a PR.

Branch naming:
- `feat/short-description` — new features or enhancements
- `fix/short-description` — bug fixes
- `chore/short-description` — maintenance, deps, tooling, docs

Steps:
1. `git checkout -b feat/description`
2. Make changes; run the full check suite
3. Bump `version` in `pyproject.toml` following the rules below
4. Commit with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
5. `git push -u origin feat/description`
6. `gh pr create`

## Versioning

Increment `version` in `pyproject.toml` with every PR using semver:

- **Patch** (`0.x.y` → `0.x.y+1`): bug fixes, dependency updates, docs,
  tooling changes
- **Minor** (`0.x.y` → `0.x+1.0`): new features, non-breaking additions
- **Major** (`x.y.z` → `x+1.0.0`): breaking changes to content JSON format,
  CLI interface, or Docker environment variables

## Documentation

Keep `README.md` up to date whenever making changes. It is the user-facing
reference and should accurately reflect the current setup, usage, and
configuration options at all times.

## Code Conventions

- 2-space indentation
- Single quotes throughout
- Type hints on all function signatures
- Target 80 columns; up to 120 is acceptable when breaking would be awkward;
  past 120 only as a last resort
- All `requests` calls must include `timeout=`
- Suppress bandit findings with `# nosec BXXX` (include rule ID); never
  suppress blindly
