# e-note-ion

A cron-based content scheduler for a Vestaboard split-flap display. Supports
both the **Note** (3 rows × 15 columns) and the **Flagship** (6 rows × 22
columns). Each character can show one of 64 values: A–Z, 0–9, punctuation,
colored squares, or ❤️ (Note) / ° (Flagship) at code 62. The display connects
over Wi-Fi and is controlled via a Read/Write API key.

## Persona

Act as a senior software engineer and information security practitioner working
on this project collaboratively with the user.

**Software engineering:**
- Write idiomatic, well-typed Python following the project's conventions
- Prefer simple, minimal solutions; avoid over-engineering and premature
  abstraction
- Design new integrations (`integrations/`) to be consistent with existing
  patterns in structure, naming, and error handling
- Keep the scheduler, queue, and worker logic reliable — exceptions in
  background threads must be caught and logged, never silently swallowed

**Security:**
- Treat `VESTABOARD_KEY` and all future API credentials as secrets — never
  log, echo, or expose them in output, errors, or intermediary state
- Validate and sanitize all data fetched from external APIs before rendering
  to the display (bounds-check lengths, strip unexpected characters)
- Flag new dependencies for CVE review (`pip-audit`); prefer well-maintained
  packages with a small attack surface
- Follow OWASP secure coding practices for all HTTP integrations: always set
  `timeout=`, verify TLS, and treat remote data as untrusted
- Apply principle of least privilege — integrations should request only the
  OAuth scopes and API permissions they strictly need

**Decision-making:**
- Raise security concerns proactively, even when not explicitly asked
- Prefer reversible, auditable changes; flag anything destructive before acting
- When scope or approach is ambiguous, ask rather than assume

## Project Structure

```
e-note-ion.py               # Entry point — scheduler, queue, worker
integrations/vestaboard.py  # Vestaboard API client (get_state, set_state)
integrations/bart.py        # BART real-time departures integration
content/
  contrib/                  # Bundled community content (disabled by default)
    bart.json               # BART real-time departure board
    bart.md                 # Sidecar doc: configuration and data sources
  user/                     # Personal content (always loaded, git-ignored)
Dockerfile                  # Single-stage image using ghcr.io/astral-sh/uv
entrypoint.sh               # Translates env vars (FLAGSHIP/PUBLIC/CONTENT_ENABLED) to CLI flags
.github/workflows/
  ci.yml                    # Runs checks on every push and pull request to main
  auto-release.yml          # Creates a GitHub release on version bump, then calls release.yml
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
      "truncation": "word",    // optional: "hard" (default), "word", "ellipsis"
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

Each content file is named for its context (e.g. `bart.json`).
`{variable}` placeholders are replaced at random from `variables` options; a
standalone `{variable}` entry expands to all lines of the chosen option. Lines
are word-wrapped to fit `model.cols`; excess rows are silently dropped.

### Integration templates

Templates can include `"integration": "<name>"` instead of (or alongside) a
static `variables` dict. When the job fires, the worker calls
`integrations.<name>.get_variables()`, which returns the same
`dict[str, list[list[str]]]` structure as static variables. This allows
dynamic data (e.g. real-time API responses) to populate `{variable}`
placeholders in the format. The `variables` key is optional when an
integration is present.

Color squares can be embedded in format strings and integration output using
short tags: `[R]` `[O]` `[Y]` `[G]` `[B]` `[V]` `[W]` `[K]` (red, orange,
yellow, green, blue, violet, white, black). Each tag encodes to the
corresponding Vestaboard color square code (63–70).

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
python e-note-ion.py                          # Note (3×15), user content only
python e-note-ion.py --content-enabled bart   # also enable contrib/bart.json
python e-note-ion.py --content-enabled '*'    # enable all contrib content
python e-note-ion.py --flagship               # target a Flagship board (6×22)
python e-note-ion.py --public                 # run only public: true templates
```

Flags can be combined.

## Environment

- `VESTABOARD_KEY` — Vestaboard Read/Write API key (required)
- Integration-specific env vars are documented in each integration's sidecar
  doc under `content/contrib/<name>.md`
- Python version managed via `.python-version` (uv)
- Dependencies managed with `uv` / `pyproject.toml`
- Dev tools: `ruff` (lint + format), `pyright` (type checking), `bandit`
  (security linting), `pip-audit` (dependency CVE scanning), `pre-commit`
- Run checks: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyright`, `uv run bandit -c pyproject.toml -r .`, `uv run pip-audit`,
  `uv run pre-commit run pretty-format-json --all-files`
- Install hooks (once after cloning): `uv run pre-commit install`

## Docker

The image is built on `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` and
published to `ghcr.io/jasonpuglisi/e-note-ion` via GitHub Actions on each
release. Multi-arch: `linux/amd64` and `linux/arm64`.

Runtime env vars (via `entrypoint.sh`): `VESTABOARD_KEY` (required),
`FLAGSHIP=true` (Flagship 6×22), `PUBLIC=true` (public templates only),
`CONTENT_ENABLED` (comma-separated contrib stems, or `*` for all).

Sample content is bundled at `/app/content`; optionally override by mounting
a host path there. Unraid CA template: `unraid/e-note-ion.xml`.

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

When the user asks for a new feature or fix, create a GitHub issue first to
track the work, then implement it. Always assign the issue to JasonPuglisi and
set an appropriate milestone.

Steps:
1. `git checkout -b feat/description`
2. Make changes; run the full check suite
3. If release-worthy (see below), bump `version` in `pyproject.toml`
4. Commit with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
   (commits are auto-signed via `commit.gpgsign = true` in global git config)
5. Verify signing succeeded: `git log -1 --show-signature` must show a valid signature before pushing
6. `git push -u origin feat/description`
7. `gh pr create --label <label> --assignee JasonPuglisi`
8. Enable auto-merge: `gh pr merge --squash --delete-branch --auto`
9. Wait for merge: `gh pr checks <number> --watch`; once all pass and the PR merges, proceed
10. After merge: `git checkout main && git pull && git branch -d feat/description`
11. Keep `README.md` and `CLAUDE.md` up to date as part of the same PR —
    new env vars, CLI flags, content format fields, project structure changes,
    and workflow changes should all be reflected before merge
12. For any TODOs identified during work, create a GitHub issue assigned to JasonPuglisi with an appropriate milestone; reference the issue number in commit messages and PRs

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

### Keeping integration data current

Some integrations embed static lists (station codes, terminal destinations,
etc.) that can go stale. Each contrib integration has a sidecar
`content/contrib/<name>.md` with authoritative data sources and update
instructions — check there when data may need refreshing.

### Contrib integration doc template

Every `content/contrib/<name>.json` must have a companion
`content/contrib/<name>.md` with the following sections:

```markdown
# <name>.json

One-sentence description. Schedule summary.

## Configuration

| Variable | Required | Description |
|---|---|---|
| `VAR_NAME` | Yes/No | What it does |

## Keeping data current

### <Data type>

Authoritative source: <URL>

Instructions for verifying and updating any hardcoded lists (station codes,
destination names, API endpoint changes, etc.).
```

After adding a new integration doc, add a row to the table in
`content/README.md`.

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
