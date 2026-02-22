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
assets/
  icon.png                  # App icon (256×256) for Unraid CA
  social-preview.png        # GitHub repository social preview (1280×640)
  README.md                 # AI generation prompts for both images
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
`{variable}` placeholders are replaced at random from `variables` options; a
standalone `{variable}` entry expands to all lines of the chosen option. Lines
are word-wrapped to fit `model.cols`; excess rows are silently dropped.

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

Sample content is bundled in the image at `/app/content` and runs
automatically. A host path can be mounted over `/app/content` to use custom
templates instead (optional). Example Unraid path:
`/mnt/user/appdata/e-note-ion/content`.

The Unraid Community Applications template is at `unraid/e-note-ion.xml`.

## Development Workflow

Never commit directly to `main`. Always work on a named branch and open a PR.

Branch naming:
- `feat/short-description` — new features or enhancements
- `fix/short-description` — bug fixes
- `chore/short-description` — maintenance, deps, tooling, docs

PR labels (apply one or more):
- `enhancement` — new features or enhancements (`feat/`)
- `bug` — bug fixes (`fix/`)
- `chore` — maintenance, tooling, deps, docs (`chore/`)
- `security` — security fixes or improvements
- `dependencies` — dependency updates

Steps:
1. `git checkout -b feat/description`
2. Make changes; run the full check suite
3. If release-worthy (see below), bump `version` in `pyproject.toml`
4. Commit with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
5. Stop and ask the user to sign the commit before pushing
6. `git push -u origin feat/description`
7. `gh pr create --label <label>`
8. After merge: `git checkout main && git pull && git branch -d feat/description`
9. Keep `README.md` up to date with any user-facing changes
10. For any TODOs identified during work, create a GitHub issue and assign to the appropriate milestone

## Release Strategy

Only create a GitHub release (and bump `version` in `pyproject.toml`) when the
PR contains **release-worthy** changes:

| Release-worthy | Not release-worthy |
|---|---|
| Source code changes (`.py` files) | CI/CD workflow changes |
| Runtime dependency changes | Dev-only dependency changes |
| `Dockerfile` or `entrypoint.sh` changes | Docs-only changes |
| Security fixes | Repo config / tooling changes |

Semver rules when bumping:
- **Patch** (`0.x.y+1`): bug fixes, dependency updates, security fixes
- **Minor** (`0.x+1.0`): new features, non-breaking additions
- **Major** (`x+1.0.0`): breaking changes to content JSON, CLI, or Docker env vars

## Maintenance

Dependencies and pinned versions should be kept current:

- **Security alerts**: check open CodeQL and Dependabot alerts at the start of
  each session and address any before other work
  ```
  gh api repos/JasonPuglisi/e-note-ion/code-scanning/alerts --jq '.[] | select(.state=="open") | {rule: .rule.id, severity: .rule.severity, path: .most_recent_instance.location.path}'
  gh api repos/JasonPuglisi/e-note-ion/dependabot/alerts --jq '.[] | select(.state=="open") | {pkg: .security_vulnerability.package.name, severity: .security_advisory.severity, summary: .security_advisory.summary}'
  ```
- **Dependabot PRs** (automated, weekly): review and merge PRs for pip
  dependencies and GitHub Actions SHA/version bumps; these are the primary
  update mechanism for both
- **Pre-commit hooks**: run `uv run pre-commit autoupdate` monthly to update
  hook versions in `.pre-commit-config.yaml`, then commit the changes
- **Full check suite**: run before every release to confirm everything passes

GitHub Actions are pinned to full commit SHAs with a `# vX.Y.Z` comment.
Dependabot reads the comment to identify the version and will open PRs to bump
both the SHA and comment when new releases are available.

## To Do

Open issues are tracked on GitHub: https://github.com/JasonPuglisi/e-note-ion/issues

Milestones:
- **v1.0 — Public Release**: CA submission (#10), misfire handling (#11)
- **Content & Integrations**: public/private content strategy (#12), default content and API integrations (#13)

When identifying new TODOs during development, create a GitHub issue rather
than adding prose here. Reference the issue number in commit messages and PRs.

## Code Conventions

- 2-space indentation
- Single quotes throughout
- Type hints on all function signatures
- Target 80 columns; up to 120 is acceptable when breaking would be awkward;
  past 120 only as a last resort
- All `requests` calls must include `timeout=`
- Suppress bandit findings with `# nosec BXXX` (include rule ID); never
  suppress blindly
