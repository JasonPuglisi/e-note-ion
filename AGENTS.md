# e-note-ion

A cron-based content scheduler for a Vestaboard split-flap display. Supports
both the **Note** (3 rows × 15 columns) and the **Flagship** (6 rows × 22
columns). Each character can show one of 64 values: A–Z, 0–9, punctuation,
colored squares, or ❤️ (Note) / ° (Flagship) at code 62. The display connects
over Wi-Fi and is controlled via a Read/Write API key.

## Skills

On-demand skills live under `.agents/skills/<name>/SKILL.md`. They follow the
[Agent Skills](https://agentskills.io) format (YAML frontmatter + markdown
body) and are vendor-neutral — any agent that reads this file can discover
them. Load a skill's body when the user's task matches its trigger; don't
preload all of them.

`.claude/skills/` holds committed symlinks to each of them, because Claude Code
only scans `.claude/skills/` and would otherwise never surface these. Keep the
real files under `.agents/skills/`; add a matching symlink whenever you add a
skill. (A global gitignore excludes `.claude/`, so the repo `.gitignore`
re-includes `skills/` while leaving `settings.local.json` ignored.)

| Skill | Load when |
|---|---|
| [`health-review`](.agents/skills/health-review/SKILL.md) | User asks for a "health check", "audit", or "project review"; before a minor/major release; after a sprint of feature work. |
| [`new-integration`](.agents/skills/new-integration/SKILL.md) | User wants to add a new integration (data source, webhook, content template). |

## Persona

Act as a senior software engineer and information security practitioner working
on this project collaboratively with the user.

**Software engineering:**
- Write idiomatic, well-typed Python following the project's conventions
- Prefer simple, minimal solutions; avoid over-engineering and premature
  abstraction
- Design new integrations (`integrations/`) to be consistent with existing
  patterns in structure, naming, and error handling — see the `new-integration`
  skill for the full walkthrough
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

**Stability (post-1.0):**
- Content JSON format, CLI flags, and config.toml keys are part of the public
  API — breaking changes require a major version bump. (The app reads no
  environment variables; all configuration lives in `config.toml`.)
- Deprecate before removing: add a deprecation warning for at least one minor
  release before removing any public-facing feature
- Internal APIs (integration module signatures, scheduler internals) may change
  freely across minor versions

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
scheduler.py                # Entry point — scheduler, queue, worker (argparse CLI)
public.py                   # Runtime public mode state (thread-safe, persisted to config.toml)
quiet.py                    # Software-side quiet mode state (thread-safe, persisted to config.toml)
homebridge.py               # Optional outbound push notifier for HomeBridge/Apple Home (fires on quiet/public transitions)
health.py                   # Integration health tracking (thread-safe, persisted to data/health.jsonl)
config.py                   # TOML config loader (load_config, get, get_optional, get_schedule_override, write_section_values / write_config_section — in-place persistence)
exceptions.py               # Custom exception types (IntegrationDataUnavailableError)
config.toml                 # Runtime config with API keys (git-ignored; copy from config.example.toml)
config.example.toml         # Config template committed to the repo
integrations/vestaboard.py  # Vestaboard API client (get_state, set_state, render)
integrations/scheduler.py   # Scheduler control webhook (quiet/wake/set_public actions)
integrations/http.py        # Shared HTTP helper with retry logic
integrations/weather.py     # Current weather via Open-Meteo (no API key required)
integrations/morning.py     # Morning weather visual grid (optional; falls back to sunrise)
integrations/calendar.py    # Today's calendar events (ICS feeds + iCloud CalDAV)
integrations/calendar_schedule.py # Calendar-driven gating for cron-triggered enqueues (vestaboard: keywords)
integrations/bart.py        # BART real-time departures integration
integrations/discogs.py     # Daily vinyl suggestion from Discogs collection
integrations/google.py      # Shared Google OAuth 2.0 device code flow (used by youtube)
integrations/trakt.py       # Trakt.tv calendar and now-playing (OAuth device flow)
integrations/diving.py      # Scuba diving conditions and days-since-last-dive (webhook)
integrations/plex.py        # Plex Media Server now-playing via webhook
integrations/color.py       # Dominant color extraction from album art (Oklab)
integrations/media.py       # Shared media display helpers
integrations/message.py     # Friend message webhook integration
integrations/moon.py        # Moon phase calculation
integrations/notion.py      # Notion webhook integration
integrations/parcel.py      # Parcel package delivery tracking
integrations/qbittorrent.py # qBittorrent seeding stats (Web API v2, local network)
integrations/tmdb.py        # TMDb metadata lookups (used by plex, trakt)
integrations/unraid.py      # Unraid server status (GraphQL API, local network)
integrations/uptimerobot.py # UptimeRobot service outage alerts (REST API, free tier)
integrations/ynab.py        # YNAB net worth tracker (REST API, personal access token)
integrations/youtube.py     # YouTube live streams from subscriptions (RSS + Data API v3)
.agents/skills/             # On-demand agent skills (vendor-neutral, agentskills.io format)
  health-review/SKILL.md    # Periodic project health audit walkthrough
  new-integration/SKILL.md  # Adding a new integration (code + content + tests + docs)
content/
  README.md                 # Content author reference: JSON format, priority, schedule coordination
  DESIGN.md                 # Visual/design conventions: layout, color, tone, character set
  contrib/                  # Bundled community content (disabled by default)
    TEMPLATE.md             # Sidecar doc template for new integrations
    weather.json / .md      # Current weather conditions
    calendar.json / .md     # Today's calendar events (ICS and iCloud CalDAV)
    bart.json / .md         # BART real-time departure board
    birthdays.json / .md    # Birthday reminders from CalDAV contacts
    discogs.json / .md      # Daily vinyl suggestion from Discogs collection
    diving.json / .md       # Scuba diving conditions and days-since-last-dive
    message.json / .md      # Friend messages via webhook
    morning_night.json / .md  # Morning weather visual + good night
    notion.json / .md       # Notion webhook notifications
    parcel.json / .md       # Upcoming package delivery from Parcel
    plex.json / .md         # Plex Media Server now-playing (webhook-only)
    qbittorrent.json / .md  # qBittorrent seeding stats
    scheduler.md            # Software-side quiet mode (webhook-only, no JSON)
    trakt.json / .md        # Trakt.tv calendar and now-playing
    unraid.json / .md       # Unraid server status
    uptimerobot.json / .md  # UptimeRobot service outage alerts (API polling)
    ynab.json / .md         # YNAB net worth tracker
    youtube.json / .md      # YouTube live streams from subscriptions
    message-wordlist.txt    # 500-word list for generating friend passphrases
    shortcuts/              # Apple Shortcuts templates (message, diving, quiet/public control)
  user/                     # Personal content (always loaded, git-ignored)
data/                       # Runtime state directory (Docker VOLUME, git-ignored)
  health.jsonl              # Persisted health events (JSONL, auto-managed, purged after 7 days)
tests/                      # Unit tests (pytest); see Tests below for layout
  core/                     # Unit tests for integrations with shared fixtures
  integrations/             # Live-API integration tests (deselected by default)
scripts/
  check-version-bump.sh     # Pre-commit hook: warn when .py/.json is staged without a version bump
  staged-integration-tests.sh # Pre-commit hook: run integration tests for staged integrations
docs/
  webhook-reverse-proxy.md  # Webhook TLS setup guide (Cloudflare Tunnel, reverse proxy)
  homebridge.md             # Apple Home / HomeBridge setup (GET /state, [homebridge] push, companion plugin)
.env.example                # Template for local integration test secrets (copy to .env, fill in, git-ignored)
Dockerfile                  # Single-stage image using ghcr.io/astral-sh/uv
MANIFEST.in                 # sdist file inclusion rules
.pre-commit-config.yaml     # Pre-commit hook definitions (ruff, bandit, pip-audit, pyright, local scripts)
.github/
  dependabot.yml            # Weekly uv + github-actions update config (grouped)
  workflows/
    ci.yml                  # Runs checks on every push and pull request to main
    auto-release.yml        # Creates a release on version bump; publishes to PyPI and GHCR
    release.yml             # Builds + pushes multi-arch Docker image to GHCR
SECURITY.md                 # Vulnerability disclosure policy and API key guidance
CONTRIBUTING.md             # Contribution guide
CODE_OF_CONDUCT.md          # Contributor Covenant
assets/
  icon.png                  # App icon (256×256) for Unraid CA
  social-preview.png        # GitHub repository social preview (1280×640)
  README.md                 # AI generation prompts for both images
```

## Related Projects

Two sibling repos depend on this one. Neither is a submodule and neither has its
own agent instructions — treat this section as the contract between them.

| Repo | What couples it to e-note-ion |
|---|---|
| [JasonPuglisi/unraid-templates](https://github.com/JasonPuglisi/unraid-templates) | `e-note-ion.xml` mirrors the `Dockerfile`: every `VOLUME`, `EXPOSE`, and mount path needs a matching `<Config>` entry, and the descriptions restate `config.toml` semantics. |
| [JasonPuglisi/homebridge-e-note-ion](https://github.com/JasonPuglisi/homebridge-e-note-ion) | Consumes the webhook API: `POST /webhook/scheduler` (`quiet` / `wake` / `public` / `private`), `GET /state`, and the `[homebridge]` push contract. Changing any of those shapes breaks the plugin silently. |

**When a change here touches one of them, say so in the PR and open a matching
issue there.** These drift quietly because CI in this repo cannot see either one:

- Anything in `Dockerfile` — a new `VOLUME`, a changed port, a new mount path —
  needs the Unraid XML updated. A volume declared here but absent there becomes an
  anonymous volume that Unraid discards on container update.
- Anything in the webhook/state surface — new actions, renamed JSON fields, changed
  auth, changed response shape — needs a corresponding plugin release. The plugin
  has no contract test against a running scheduler.
- `docs/homebridge.md` documents the plugin's setup; keep it current when the
  plugin's config schema changes.

Both are small and low-traffic, so they go stale without anyone noticing. The
`health-review` skill checks them as part of a full audit.

## How It Works

1. `load_content()` reads all JSON files from `content/` and registers each
   template as an APScheduler cron job (using `BackgroundScheduler`). Templates
   with `"webhook": true` and no `cron` are webhook-only — they are validated
   and logged but not scheduled; they fire only when the webhook server receives
   a matching event.
2. When a job fires, it calls `enqueue()`, which pushes a `QueuedMessage` into a
   `PriorityQueue`.
3. A single worker thread calls `pop_valid_message()` in a loop, which blocks
   until a message is available, discarding any that have exceeded their
   `timeout`. It then sends the message to the display and sleeps for `hold`
   seconds before processing the next one.
4. When an integration template with `refresh_interval` finishes its hold and
   the queue is empty, the worker enters **idle refresh**: it continues calling
   the integration at the same interval, keeping the display current until the
   next queued message arrives. This means a real-time display (e.g. BART
   departures) keeps updating passively when nothing else is competing.

The single-threaded worker ensures display messages never overlap — important
for a physical split-flap device whose flaps need time to settle.

For the full content JSON format spec, see `content/README.md`. For the
integration module conventions and webhook patterns, see the `new-integration`
skill.

## Environment

- Configuration lives in `config.toml` at the project root (git-ignored). Copy
  `config.example.toml`, fill in API keys and settings, then run the scheduler.
  Integration-specific keys are documented in each integration's sidecar doc
  under `content/contrib/<name>.md` and in `config.example.toml`.
- Python version managed via `.python-version` (uv)
- Dependencies managed with `uv` / `pyproject.toml`
- Dev tools: `ruff` (lint + format), `pyright` (type checking), `bandit`
  (security linting), `pip-audit` (dependency CVE scanning), `pytest-cov`
  (coverage reporting), `pre-commit`
- Run checks: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyright`, `uv run bandit -c pyproject.toml -r .`, `uv run pip-audit`,
  `uv run pre-commit run pretty-format-json --all-files`,
  `uv run pytest --cov --cov-report=term-missing` (source list lives in
  `[tool.coverage.run]`; passing `--cov=.` pulls the tests into the report and
  inflates the number)
- Install hooks (once after cloning): `uv run pre-commit install`
- Two local hooks live in `scripts/`: `check-version-bump.sh` fails the commit
  when a `.py` or `.json` file is staged without a `version` bump in
  `pyproject.toml` (bypass with `--no-verify` when genuinely not
  release-worthy), and `staged-integration-tests.sh` runs the matching live-API
  test for any staged `integrations/<name>.py`
- Tests live in `tests/`; use `pytest` with `unittest.mock` for HTTP calls

## Docker

Image: `ghcr.io/jasonpuglisi/e-note-ion` (multi-arch, auto-published on release).
Mount `config.toml` at `/app/config.toml` and optionally personal content at
`/app/content/user`. Display model, public mode, and enabled contrib content are
configured in `config.toml` under `[scheduler]` — see `README.md`.

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
- CI runs `uv run pytest --cov --cov-report=term --cov-report=xml`
  — tests must pass before merge; coverage is reported but not gated
- When working on existing code that lacks tests, add retroactive coverage as
  part of the same PR where feasible
- When a change affects output format or data shape, grep all test directories
  (`tests/`, `tests/core/`, `tests/integrations/`) for assertions on the old
  format before committing — not just the files being directly edited

#### Integration tests

Integration tests live in `tests/integrations/` and are excluded from the
default `uv run pytest` run. To run them locally:

1. Copy `.env.example` to `.env` and fill in your API keys (bare values, no quotes)
2. Run `uv run pytest -m integration -v`

A setup table prints at session start showing which env vars are set or missing.
The `.env` file is git-ignored — never commit it. CI has no `.env` file; secrets
come from GitHub secrets env vars directly.

When `integrations/<name>.py` is staged, the `staged-integration-tests`
pre-commit hook automatically runs `tests/integrations/test_<name>_integration.py`.
Without `.env`, the hook prints a hint and passes (graceful degradation); with
`.env` set up, real API calls catch contract / output-format mismatches before
they reach `main`. CI's integration job is advisory (`continue-on-error: true`),
so this local hook is the primary guard against regressions reaching `main`
through the integration test path.

- Mark tests with `@pytest.mark.integration` and `@pytest.mark.require_env('VAR', ...)`
- Tests skip automatically when required env vars are absent (no failures)
- Required env vars per integration (real API keys only; other settings are
  hardcoded in the test via `config._config` patching):
  - Vestaboard: `VESTABOARD_VIRTUAL_API_KEY` (use a virtual board, not physical)
  - Calendar (ICS mode): `CALENDAR_URL`
  - Calendar (CalDAV mode): `CALENDAR_CALDAV_URL`, `CALENDAR_USERNAME`, `CALENDAR_PASSWORD`
  - Calendar (CardDAV/birthdays mode): `CALENDAR_CARDDAV_URL`
  - BART: `BART_API_KEY`
  - Trakt: `TRAKT_CLIENT_ID`, `TRAKT_CLIENT_SECRET`, `TRAKT_ACCESS_TOKEN`
  - TMDb: `TMDB_API_READ_ACCESS_TOKEN`
  - Discogs: `DISCOGS_TOKEN`
  - Diving: `DIVING_NDBC_STATION`, `DIVING_LAT`, `DIVING_LON`
  - Parcel: `PARCEL_API_KEY`
  - UptimeRobot: `UPTIMEROBOT_API_KEY`
  - YNAB: `YNAB_API_KEY` (required), `YNAB_BUDGET_ID` (optional — auto-detected for single-budget accounts)
  - YouTube: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`
- CI runs the `integration` job on `main` pushes only; it is advisory
  (`continue-on-error: true`) and not required by the branch ruleset
- GitHub secrets/vars needed — store as **environment secrets** (or vars) on the
  `integration` environment (Settings → Environments), restricted to the `main`
  branch; this scopes them tighter than repo secrets and prevents any PR branch
  from accessing them even if a workflow runs there:
  - Secrets: `VESTABOARD_VIRTUAL_API_KEY`, `CALENDAR_URL`, `CALENDAR_CALDAV_URL`, `CALENDAR_USERNAME`, `CALENDAR_PASSWORD`, `CALENDAR_CARDDAV_URL`, `BART_API_KEY`, `TRAKT_CLIENT_SECRET`, `TRAKT_ACCESS_TOKEN`, `TMDB_API_READ_ACCESS_TOKEN`, `DISCOGS_TOKEN`, `PARCEL_API_KEY`, `UPTIMEROBOT_API_KEY`, `YNAB_API_KEY`, `YNAB_BUDGET_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`
  - Variables: `TRAKT_CLIENT_ID` (non-sensitive); `DIVING_NDBC_STATION`, `DIVING_LAT`, `DIVING_LON` (public NOAA station and coordinates); `GOOGLE_CLIENT_ID` (non-sensitive)
- If any integration test is skipped, the pytest session exits with code 5
  (NO_TESTS_COLLECTED), making the advisory job visibly fail rather than silently pass
- When adding a new integration, also add `tests/integrations/test_<name>_integration.py`,
  add its env vars to the `_INTEGRATION_VARS` list in `tests/integrations/conftest.py`,
  and wire them into the `env:` block of the integration job in `.github/workflows/ci.yml`
  (use `${{ secrets.VAR }}` for sensitive values, `${{ vars.VAR }}` for non-sensitive ones).
  Also update env vars in all five synced locations: `conftest.py`, `ci.yml`,
  `.env.example`, `README.md` integration test table, and the env var lists in
  this file (AGENTS.md)

### Periodic health review

Triggered by user request, before a minor/major release, or after a sprint of
feature work. The full checklist + process lives in the `health-review` skill
(`.agents/skills/health-review/SKILL.md`) — load it when running an audit. Past
reviews have surfaced drift in code patterns, docs, dependency CVEs, and the
issue tracker; the skill captures the prevention rules added in response.

### Planning before implementation

All non-trivial work follows a plan-then-execute cycle:

1. **Create or identify a GitHub issue.** Assign to JasonPuglisi with a
   milestone. Read all existing comments before proceeding — blockers, prior
   decisions, and context live there. Prefer GitHub's native **issue
   dependencies** (Add dependency via the issue UI / `gh api`) over free-text
   "Blocked by #N" — they update automatically and surface in the UI.
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
3. For new integrations or API-dependent changes, verify locally against the
   real API before committing — unit test mocks cannot catch API contract
   mismatches. Use `config.toml` with real credentials and confirm the
   integration returns the expected output.
4. **Bundle opportunistic cleanups while already in-flight.** If you're already
   making changes in a PR, take the opportunity to:
   - Run `uv lock --upgrade` and pull in any clean dep updates (regardless of
     whether a CVE forces it — see Maintenance § Dependency posture below)
   - Address any small drift you notice in the files you're already touching
   - Roll any blocking CVE fix into the same PR rather than spinning up a
     separate small PR for it
   The aim is fewer PRs that each carry meaningful change, not one-line PRs
   that fragment the history.
5. If release-worthy (see Release Strategy below), bump `version` in
   `pyproject.toml` **in the same commit as the source change** — never a
   follow-up PR. Rule of thumb: if any `.py` or `.json` file is staged, check
   whether a bump is needed. Always stage `uv.lock` alongside `pyproject.toml`
   to avoid pre-commit stash conflicts.
6. Commit with `Co-Authored-By: Claude <model> <noreply@anthropic.com>`
   (use your current model name, e.g. `Opus 4.7` or `Sonnet 4.6`;
   commits are auto-signed via `commit.gpgsign = true` in global git config)
7. Verify signing succeeded: `git log -1 --show-signature` must show a valid signature before pushing
8. `git push -u origin feat/description`
9. `gh pr create --label <label> --assignee JasonPuglisi`
10. Enable auto-merge: `gh pr merge --squash --delete-branch --auto`
11. Wait for merge: `gh pr checks <number> --watch`; once all pass and the PR merges, proceed
12. After merge: `git checkout main && git pull && git branch -d feat/description`;
    get merge SHA via `git rev-parse HEAD`; run `gh run list --branch main --commit <sha>`
    and watch all in-progress runs to completion (`gh run watch <id>`). This step is
    non-negotiable — run it even when PR checks looked clean.
    Required jobs must pass: `check` and `docker` from `ci.yml`, plus CodeQL's
    "Analyze (actions)", "Analyze (python)", and "CodeQL" checks (GitHub's built-in
    code scanning — runs automatically, not defined in `ci.yml`).
    Advisory integration job may fail (missing secrets or API issue) — note but not a blocker.
13. Keep `README.md` and `AGENTS.md` (and any affected skill files) up to date
    as part of the same PR — new env vars, CLI flags, content format fields,
    project structure changes, and workflow changes should all be reflected
    before merge. When adding or renaming a template in `content/contrib/`,
    update both the template mapping table and the schedule map in
    `content/README.md` (enforced by CI via `test_schedule_lint.py`)
14. For any TODOs identified during work, create a GitHub issue assigned to
    JasonPuglisi with an appropriate milestone; reference the issue number in
    commit messages and PRs

## Release Strategy

Only create a GitHub release (and bump `version` in `pyproject.toml`) when the
PR contains **release-worthy** changes:

| Release-worthy | Not release-worthy |
|---|---|
| Source code changes (`.py` files) | CI/CD workflow changes |
| Content JSON changes (`content/`) | Tooling config JSON changes |
| Runtime dependency changes | Dev-only dependency changes |
| `Dockerfile` changes | Docs-only changes |
| Security fixes | Repo config / tooling changes |
|  | Test-only changes (`tests/`) |
|  | Skill / agent doc changes (`.agents/skills/`) |

Semver rules (strict post-1.0):
- **Patch** (`x.y.z+1`): bug fixes, dependency updates, security fixes
- **Minor** (`x.y+1.0`): new features, non-breaking additions
- **Major** (`x+1.0.0`): breaking changes to content JSON, CLI, config.toml
  keys, or Docker env vars

### Dependency updates and releases

Runtime dependency changes (including transitive deps that change in
`uv.lock`) are release-worthy for a concrete reason: the Docker image is the
only consumer that needs a release to pick them up. The image is built with
`uv sync --frozen --no-dev`, baking in the locked **runtime closure**, and is
rebuilt only when a release is cut (a `version` bump). Without a bump, Docker
users stay on whatever was locked at the last release — including missed
dependency CVE fixes. (PyPI installs resolve the `>=` floors fresh, so they
already get newest-compatible deps without a release; a release is only needed
on PyPI to publish tightened floors.)

Rules:
- **Runtime-closure change** — a runtime dep in `[project.dependencies]` or
  any of its transitive deps in `uv.lock` → **bump patch and release.** Always
  release for a dependency **security** fix in the runtime closure.
- **Dev-only change** — anything under `[dependency-groups] dev` and its
  dev-only transitives (ruff, pyright, pytest, bandit, pip-audit, etc.) →
  **no release.** `--no-dev` keeps these out of the image and they don't affect
  PyPI consumers. (A pip CVE surfaced via the pip-audit dev tool, for example,
  never ships in the runtime image.)
- **Dependabot grouped PRs** mix both. If the PR's `uv.lock` diff touches the
  runtime closure, bump patch on merge; if it's purely dev, merge without a
  bump. Routine runtime bumps may be batched into a periodic roll-up patch
  release rather than one release per PR — but never sit on a security fix.

Two types of milestones are used:

- **Batch milestones** (e.g. "Batch 1", "Batch 2") — numbered work queues
  grouping issues by priority and theme. Close each batch when done and
  create the next. Version numbers are determined by semver based on the
  actual changes in each PR, not by which batch they belong to.
- **Release milestones** (e.g. "Release v2.0.0") — accumulate breaking
  changes that cannot ship until a major version bump is justified. Do not
  merge issues from a release milestone until there are enough breaking
  changes to warrant the bump. When ready, close all issues in the release
  milestone as part of a single major-version PR.

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
- **Dependency posture**: be **opportunistic about upgrading**, regardless of
  whether a CVE forces it. When already touching `pyproject.toml` or `uv.lock`
  in any PR, run `uv lock --upgrade` and bundle the upgrades. Classification
  (runtime vs dev) does not gate the upgrade decision — newer is preferred,
  full stop. Verify with `uv run pytest` and `uv run pip-audit` before
  committing.
- **Dependabot PRs** (automated, weekly): review and merge PRs for `uv`
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
doc, add a row to the table in `content/README.md`. The full integration-authoring
walkthrough lives in the `new-integration` skill.

## Content Design

When writing or reviewing content JSON, integration format strings, or
template output, consult and follow both content docs:

- `content/README.md` — content author reference: JSON format, priority
  guidelines (0–10 scale with tier definitions), `timeout` pairing rules,
  schedule overrides, schedule coordination guidelines (cron slot conventions,
  timeout/hold pairing rules), and the contrib integrations table
- `content/DESIGN.md` — visual/design conventions: layout, color use,
  tone, character set, time formatting, and the pre-ship checklist

## Code Conventions

- 2-space indentation
- Single quotes throughout
- Type hints on all function signatures
- Target 80 columns; up to 120 is acceptable when breaking would be awkward;
  past 120 only as a last resort
- All `requests` calls must include `timeout=`
- Suppress bandit findings with `# nosec BXXX  # justification` — always the
  bandit rule ID (`B311`, not ruff's `S311`) AND a short reason, with the reason
  behind a second `#`. Bandit parses everything between `nosec` and the next
  `#` as test IDs: prose there floods the output with "not a test name"
  warnings, and an unrecognised ID silently degrades the line to a *blanket*
  suppression. Never suppress blindly.
- Integration modules must import `config` inside functions (not at module
  level) using the alias `import config as _config_mod` — consistent with
  `_public_mod` / `_quiet_mod` in `scheduler.py`. This keeps integrations
  importable in tests without a loaded config file.
- Integration modules consistently alias shared modules: `import
  integrations.vestaboard as _vb`, `import integrations.media as _media`,
  `import scheduler as _sched`. Underscore-prefix the alias.
- Runtime state modules (`public.py`, `quiet.py`) follow a symmetric API:
  `set_<name>(bool)` / `is_<name>()` with state persisted as a flat key under
  `[scheduler]` in `config.toml`. New runtime state modules should follow the
  same pattern.
</content>
</invoke>