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
- Treat `VESTABOARD_API_KEY` and all future API credentials as secrets — never
  log, echo, or expose them in output, errors, or intermediary state
- Validate and sanitize all data fetched from external APIs before rendering
  to the display (bounds-check lengths, strip unexpected characters)
- Flag new dependencies for CVE review (`pip-audit`); prefer well-maintained
  packages with a small attack surface
- Follow OWASP secure coding practices for all HTTP integrations: always set
  `timeout=`, verify TLS, and treat remote data as untrusted
- Apply principle of least privilege — integrations should request only the
  OAuth scopes and API permissions they strictly need

**Pre-v1.0 stance:**
- No backwards compatibility concerns before v1.0 — breaking changes to the
  content JSON format, CLI flags, Docker env vars, and internal APIs are all
  fair game
- Refactor early and often when it sets up a better foundation; don't hold
  back to preserve existing behaviour

**Decision-making:**
- Raise security concerns proactively, even when not explicitly asked
- Prefer reversible, auditable changes; flag anything destructive before acting
- When scope or approach is ambiguous, ask rather than assume
- Be opinionated — if a proposed approach has a better alternative, push back
  and explain why rather than just implementing what was asked
- Actively watch for gaps, flaws, and improvement opportunities during work —
  open GitHub issues for anything worth tracking without waiting to be asked
  (missing tests, stale docs, inconsistencies, security issues, UX rough edges,
  new feature ideas that surface during implementation)

**AI authorship:**
- When writing GitHub issues, PR descriptions, or comments, make it clear that
  an AI assistant authored them (e.g. open issue/PR bodies with "— *Claude Code*"
  for Claude, or an equivalent attribution for other tools)

## Project Structure

```
scheduler.py                # Entry point — scheduler, queue, worker
config.py                   # TOML config loader (load_config, get, get_optional, get_schedule_override, write_section_values — in-place token persistence)
exceptions.py               # Custom exception types (IntegrationDataUnavailableError)
config.toml                 # Runtime config with API keys (git-ignored; copy from config.example.toml)
config.example.toml         # Config template committed to the repo
integrations/vestaboard.py  # Vestaboard API client (get_state, set_state)
integrations/bart.py        # BART real-time departures integration
integrations/trakt.py       # Trakt.tv calendar and now-playing (OAuth device flow)
content/
  contrib/                  # Bundled community content (disabled by default)
    bart.json               # BART real-time departure board
    bart.md                 # Sidecar doc: configuration and data sources
    trakt.json              # Trakt.tv calendar and now-playing
    trakt.md                # Sidecar doc: OAuth setup and data sources
  user/                     # Personal content (always loaded, git-ignored)
.env.example                # Template for local integration test secrets (copy to .env, fill in, git-ignored)
Dockerfile                  # Single-stage image using ghcr.io/astral-sh/uv
entrypoint.sh               # Translates env vars (FLAGSHIP/PUBLIC/CONTENT_ENABLED) to CLI flags
.github/workflows/
  ci.yml                    # Runs checks on every push and pull request to main
  auto-release.yml          # Creates a release on version bump; calls release.yml
  release.yml               # Builds + pushes multi-arch image to ghcr.io
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

### Integration dependencies

Add any packages required by a new integration to `pyproject.toml`
`dependencies` (the base list). All deps are installed unconditionally —
this keeps the Docker image simple, and Docker is the primary deployment
target. If an integration is loaded and its deps are missing (e.g. a
source-install user skipped them), `_get_integration()` will catch the
`ImportError` and raise a `RuntimeError` with an install hint; the worker
logs it and skips that message rather than crashing.

## Environment

- Configuration lives in `config.toml` at the project root (git-ignored). Copy
  `config.example.toml`, fill in API keys and settings, then run the scheduler.
  Integration-specific keys are documented in each integration's sidecar doc
  under `content/contrib/<name>.md` and in `config.example.toml`.
- Python version managed via `.python-version` (uv)
- Dependencies managed with `uv` / `pyproject.toml`
- Dev tools: `ruff` (lint + format), `pyright` (type checking), `bandit`
  (security linting), `pip-audit` (dependency CVE scanning), `pre-commit`
- Run checks: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyright`, `uv run bandit -c pyproject.toml -r .`, `uv run pip-audit`,
  `uv run pre-commit run pretty-format-json --all-files`, `uv run pytest`
- Install hooks (once after cloning): `uv run pre-commit install`
- Tests live in `tests/`; use `pytest` with `unittest.mock` for HTTP calls

## Docker

Image: `ghcr.io/jasonpuglisi/e-note-ion` (multi-arch, auto-published on release).
Mount `config.toml` at `/app/config.toml` and optionally personal content at
`/app/content/user`. Runtime CLI flags (`FLAGSHIP`, `PUBLIC`, `CONTENT_ENABLED`)
are translated by `entrypoint.sh` — see `README.md`.

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

### Tests

- PRs that introduce new logic **must** include corresponding tests in `tests/`
- Use `pytest`; mock HTTP calls with `unittest.mock`
- CI runs `uv run pytest` — tests must pass before merge
- When working on existing code that lacks tests, add retroactive coverage as
  part of the same PR where feasible

#### Integration tests

Integration tests live in `tests/integrations/` and are excluded from the
default `uv run pytest` run. To run them locally:

1. Copy `.env.example` to `.env` and fill in your API keys (bare values, no quotes)
2. Run `uv run pytest -m integration -v`

A setup table prints at session start showing which env vars are set or missing.
The `.env` file is git-ignored — never commit it. CI has no `.env` file; secrets
come from GitHub secrets env vars directly.

- Mark tests with `@pytest.mark.integration` and `@pytest.mark.require_env('VAR', ...)`
- Tests skip automatically when required env vars are absent (no failures)
- Required env vars per integration (real API keys only; other settings are
  hardcoded in the test via `config._config` patching):
  - BART: `BART_API_KEY`
  - Vestaboard: `VESTABOARD_VIRTUAL_API_KEY` (use a virtual board, not physical)
- CI runs the `integration` job on `main` pushes only; it is advisory
  (`continue-on-error: true`) and not required by the branch ruleset
- GitHub secrets needed: `BART_API_KEY`, `VESTABOARD_VIRTUAL_API_KEY` — store as
  **environment secrets** on the `integration` environment (Settings → Environments),
  restricted to the `main` branch; this scopes them tighter than repo secrets and
  prevents any PR branch from accessing them even if a workflow runs there
- If any integration test is skipped, the pytest session exits with code 5
  (NO_TESTS_COLLECTED), making the advisory job visibly fail rather than silently pass
- When adding a new integration, also add `tests/integrations/test_<name>_integration.py`
  and add its env vars to the `_INTEGRATION_VARS` list in `tests/integrations/conftest.py`

### Periodic health review

At natural breakpoints (before a minor/major release, after a sprint of feature
work), run through this checklist. Open issues for gaps found; fix trivial things
inline. When something slips through, ask why it wasn't caught and add a
prevention here — not just a one-off fix. See #65 for extended notes.

1. **Test coverage** — gaps in unit tests; retroactive coverage for untested logic
2. **Code patterns** — consistency across integrations (structure, naming, error handling)
3. **Dependency health** — `uv run pip-audit`; flag any CVEs
4. **Security posture** — timeouts on all HTTP calls; secrets not logged; `# nosec` justifications valid
5. **Docs drift** — README / AGENTS.md / sidecar docs accurate and not duplicating each other
6. **Stale comments** — no unresolved TODO/FIXME in source
7. **CI/CD hygiene** — job permissions minimal; step names accurate; post-merge `main` runs passing clean
8. **Branch ruleset integrity** — required status check names match actual CI job names in `ci.yml`:
   ```
   gh api repos/JasonPuglisi/e-note-ion/rulesets/13082160 --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'
   ```
9. **Integration test hygiene** — advisory CI job passing on `main`; GitHub secrets `BART_API_KEY` and `VESTABOARD_VIRTUAL_API_KEY` present; new integrations have `test_<name>_integration.py` and env vars in `tests/integrations/conftest.py`
10. **Issue/milestone hygiene**:
    - Every open issue has a milestone (no orphans); scope is right-sized
    - Blocking relationships explicit ("Blocked by #X" in body)
    - Tracking issues have sub-issues linked (`gh api repos/JasonPuglisi/e-note-ion/issues/<n>/sub_issues`)
    - Issues in wrong milestone reassigned; stale/superseded issues closed with a note

### Planning before implementation

All non-trivial work follows a plan-then-execute cycle:

1. **Create or identify a GitHub issue.** Assign to JasonPuglisi with a
   milestone. Read all existing comments before proceeding — blockers, prior
   decisions, and context live there.
2. **Post an implementation plan as a comment.** Cover: files and functions to
   change, approach with rationale, edge cases, open questions, and a
   **## Tests** section listing new and updated tests. Do this before any code.
3. **⛔ HARD STOP — wait for a 👍 reaction from JasonPuglisi** on the plan
   comment. No reaction = no implementation. A reaction from anyone else does
   not count. Verify via the GitHub API before proceeding.
4. **Execute the approved plan** following the Execution steps below.

For simple or clearly-scoped tasks (typo fixes, one-line changes), the plan
step may be skipped — use judgement.

### Execution steps

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
10. After merge: `git checkout main && git pull && git branch -d feat/description`;
    get merge SHA via `git rev-parse HEAD`; run `gh run list --branch main --commit <sha>`
    and watch all in-progress runs to completion (`gh run watch <id>`). This step is
    non-negotiable — run it even when PR checks looked clean.
    Required jobs (check, docker, CodeQL) must pass. Advisory integration job may fail
    (missing secrets or API issue) — note but not a blocker.
11. Keep `README.md` and `AGENTS.md` up to date as part of the same PR —
    new env vars, CLI flags, content format fields, project structure changes,
    and workflow changes should all be reflected before merge
12. For any TODOs identified during work, create a GitHub issue assigned to
    JasonPuglisi with an appropriate milestone; reference the issue number in
    commit messages and PRs

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

- **Open issues**: `gh issue list --state open` — the GitHub issue tracker at
  https://github.com/JasonPuglisi/e-note-ion/issues is the source of truth for
  all TODOs and planned work; check it at the start of each session. When
  reviewing an issue for planning or prioritisation, always read all comments
  (`gh issue view <n>`) for full context — blockers, decisions, and status
  updates often live there, not in the issue body
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

Some integrations also require monitoring external announcement channels for
API changes (e.g. Trakt requires watching https://github.com/trakt/trakt-api/discussions).
Check each sidecar's "Keeping data current" section for any such requirements
and verify those channels during periodic health reviews.

### Contrib integration doc template

Every `content/contrib/<name>.json` must have a companion `content/contrib/<name>.md`.
Use `content/contrib/TEMPLATE.md` as the starting point. After adding a new integration
doc, add a row to the table in `content/README.md`.

## Content Design

When writing or reviewing content JSON, integration format strings, or
template output, consult and follow `content/DESIGN.md`. This covers
layout, color use, tone, character set, time formatting, and the
pre-ship checklist.

## Code Conventions

- 2-space indentation
- Single quotes throughout
- Type hints on all function signatures
- Target 80 columns; up to 120 is acceptable when breaking would be awkward;
  past 120 only as a last resort
- All `requests` calls must include `timeout=`
- Suppress bandit findings with `# nosec BXXX` (include rule ID); never
  suppress blindly
