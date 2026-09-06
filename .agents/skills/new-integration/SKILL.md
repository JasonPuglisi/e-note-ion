---
name: new-integration
description: Add a new integration to the e-note-ion content scheduler. Covers integration module conventions (config import alias, vestaboard/media aliases, HTTP via shared helper, error handling), webhook integration shape (handle_webhook signature, WebhookMessage, supersede_tag, autogen credentials), scheduler.py registration (_KNOWN_INTEGRATIONS, _WEBHOOK_AUTOGEN), content JSON template, sidecar doc requirement, and the five-location env-var sync. Invoke when the task is "add an integration for X", "wire up Y as a content source", "support Z webhook events", or similar.
license: MIT
compatibility: Requires uv, git, and the gh CLI. Live-API verification needs a populated .env.
metadata:
  last-verified: "2026-09-06"
  health: healthy
---

# Add a New Integration

## Description

Walkthrough for adding a new integration module to e-note-ion — covers code conventions, scheduler registration, content templates, sidecar docs, env-var sync, and tests. Sized to be the single reference for "I want to add integration X" tasks.

## When to Use

- User wants to add a new integration (data source: API, webhook, RSS, calendar, etc.)
- User wants to convert an existing webhook-only template into a polled integration (or vice versa)
- User asks about integration patterns / conventions when proposing a new integration

## Process

1. **Decide the trigger model** — polled (cron-scheduled), webhook-only, or both. See "Decision: polled vs webhook" in Reference.
2. **Create `integrations/<name>.py`** following the module conventions below.
3. **Register the integration** in `scheduler.py` (`_KNOWN_INTEGRATIONS`; also `_WEBHOOK_AUTOGEN` if webhook with autogen credentials).
4. **Create `content/contrib/<name>.json`** with one or more templates. See `content/README.md` for full JSON spec.
5. **Create `content/contrib/<name>.md`** sidecar doc. Use `content/contrib/TEMPLATE.md` as the starting point. Add a row to the contrib table in `content/README.md`. If cron-scheduled, add a row to the schedule map in the same file.
6. **Add unit tests** at `tests/test_<name>.py` or `tests/core/test_<name>.py`. Mock all HTTP with `unittest.mock`.
7. **Add integration test** at `tests/integrations/test_<name>_integration.py` if the integration has external API calls. Mark with `@pytest.mark.integration` and `@pytest.mark.require_env('VAR', ...)`.
8. **Sync env vars across the five locations** (see Reference).
9. **Add a `[<name>]` block to `config.example.toml`** documenting required and optional config keys.
10. **Run the full check suite** before committing (see Reference).
11. **Update `AGENTS.md`** Project Structure listing with the new module + content files.

## Module conventions

- **2-space indent, single quotes, type hints on every signature.** Target 80 cols, 120 max.
- **Logger:** `logger = logging.getLogger(__name__)` at module top.
- **Imports inside functions, with underscore aliases**, for everything that touches runtime state:
  ```python
  import config as _config_mod  # not at module top — keeps integrations importable in tests
  import integrations.vestaboard as _vb
  import integrations.media as _media
  import scheduler as _sched  # only inside webhook handlers / enqueue paths
  ```
  Module-level `import config` blocks `pytest` from collecting tests when `config.toml` is absent.
- **HTTP**:
  - Use `integrations.http.fetch_with_retry` and `user_agent()` for shared retry / UA logic. Direct `requests.*` calls are acceptable for OAuth token endpoints and uncommon HTTP methods (PROPFIND, REPORT) — always pass `timeout=`.
  - Treat all remote data as untrusted. Bounds-check lengths, strip unexpected characters before rendering to the display.
- **Error handling**:
  - Raise `IntegrationDataUnavailableError(msg, expected=True/False)` from `exceptions.py` for transient unavailability. The worker catches and logs without crashing the scheduler.
  - Background-thread exceptions must be caught and logged — never silently swallowed.
- **Bandit suppressions:** every `# nosec` includes the rule ID AND a justification (`# nosec B311 — visual scatter grid, not a security context`). No bare `# nosec`.
- **Secrets:** never log API keys; never echo into errors. Token-clearing writes (e.g. on auth failure) need a `# nosec B105` justification noting the empty strings are intentional.

## Polled integration shape

```python
def get_variables() -> dict[str, list[list[str]]]:
  """Fetch data and return template variables.

  Variable values: dict mapping variable name → list of options → list of lines.
  Raise IntegrationDataUnavailableError(expected=True) when the API has nothing
  to show (e.g. no upcoming events) — worker logs at DEBUG.
  Raise IntegrationDataUnavailableError(expected=False) for outages — worker
  logs at WARNING.
  """
```

A single integration may expose multiple data sources via additional functions (`get_variables_<flavor>`); reference them in JSON templates with `"integration_fn": "get_variables_<flavor>"`.

## Webhook integration shape

```python
def handle_webhook(payload: dict[str, Any], credential_name: str | None = None) -> WebhookMessage | None:
  """Parse the payload; return WebhookMessage to enqueue, or None to discard.

  All handlers must accept credential_name as a keyword argument (used for
  multi-tenant credential routing — see message.py for an example).
  """
```

Use `WebhookMessage` from `scheduler.py`. Common patterns:
- `interrupt=True` for time-sensitive state changes (Plex pause/resume) that should preempt current display.
- `indefinite=True` for playback-state messages that should hold until a stop event arrives. The `hold` field acts as a safety ceiling.
- `interrupt_only=True` for stop/end events that clear the display without enqueueing new content.
- `supersede_tag='<integration>'` so the next event replaces stale queued messages from the same integration before they reach the board.

If the integration auto-creates its credential on first startup, also add it to `_WEBHOOK_AUTOGEN` in `scheduler.py` with the credential name. Otherwise the user has to create the credential manually.

## scheduler.py registration

```python
# scheduler.py
_KNOWN_INTEGRATIONS: frozenset[str] = frozenset({
  'bart', 'calendar', ..., '<name>',  # ADD HERE
})

# Only if webhook + autogen credentials:
_WEBHOOK_AUTOGEN: dict[str, str] = {
  'plex': 'plex',
  ..., '<name>': '<name>',  # ADD HERE
}
```

## Content JSON quick-reference

See `content/README.md` for the full spec. Minimum viable contrib JSON:

```json
{
  "templates": {
    "<template_name>": {
      "schedule": { "cron": "0 8 * * *", "hold": 600, "timeout": 600 },
      "priority": 5,
      "private": false,
      "truncation": "ellipsis",
      "integration": "<name>",
      "templates": [
        { "format": ["[G] HEADER", "{var_one}", "{var_two}"] }
      ]
    }
  }
}
```

For webhook-only templates: omit `cron`, set `"webhook": true`.

### Credential validation at startup

If the integration has a credential, give it a `preflight()` that makes one
cheap authenticated call and passes the response through
`integrations.http.raise_for_credentials(resp, '<name>')`. The scheduler calls
`preflight()` for every loaded integration at startup and records a
`CredentialError` as a health error, so an expired key shows on `/health`
immediately rather than whenever the cron next fires — up to a day later.

Only 401/403 count. Do not raise `CredentialError` for a 500, a timeout, or an
empty result: `/health` has to keep meaning "your credential is bad" rather
than "something was briefly flaky". An expected-empty result in particular
(`IntegrationDataUnavailableError(expected=True)`, e.g. "all monitors up") is
the healthy state and must not fail preflight.

Skip the check when the integration is optional and unconfigured — see
`integrations/tmdb.py`.

## Constraints

- **Don't add new integrations that depend on heavyweight packages** without prior discussion. Every dep increases attack surface and Docker image size.
- **Don't introduce breaking changes** to the content JSON spec, CLI flags, Docker env vars, or `config.toml` keys without a major version bump.
- **Don't use module-level `import config`** — keeps integration importable in tests without a loaded config.
- **Don't add data-source defaults that hit a real API in tests** — tests must work offline with mocked HTTP.
- **Don't skip the sidecar doc.** Every contrib JSON needs a matching `<name>.md` (CI enforces this).

## Reference

### Decision: polled vs webhook

| Trigger | Use polled (`cron`) | Use webhook |
|---|---|---|
| Data changes on a schedule (weather, calendar, Trakt calendar) | ✓ | |
| External system pushes events (Plex, iOS Shortcut, Notion) | | ✓ |
| Want real-time updates and the source supports webhooks | | ✓ |
| Source is read-only and only available via API | ✓ | |
| Both apply (Trakt watching: poll + webhook would be redundant) | poll only | |

### Five-location env-var sync

When adding new integration env vars, update **all five**:
1. `tests/integrations/conftest.py` — add to `_INTEGRATION_VARS`
2. `.github/workflows/ci.yml` — add to the integration job's `env:` block (`secrets.X` for sensitive, `vars.X` for non-sensitive)
3. `.env.example`
4. `README.md` integration-test required-keys table
5. `AGENTS.md` integration-tests env vars list + the GitHub secrets/vars sub-list

### GitHub environment secrets/vars

Store as **environment** secrets/vars on the `integration` GitHub Actions environment (Settings → Environments), restricted to the `main` branch. Repo-wide secrets are over-scoped.

### Pre-commit hook for staged integrations

`scripts/staged-integration-tests.sh` runs `tests/integrations/test_<name>_integration.py` automatically when the matching `integrations/<name>.py` is staged. Without `.env`, the hook prints a hint and passes (graceful degradation). With `.env` set up, real API calls catch contract / output-format mismatches before merge.

### Full check suite

Run before every commit:
```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run bandit -c pyproject.toml -r .
uv run pip-audit
uv run pre-commit run pretty-format-json --all-files
uv run pytest --cov=integrations --cov=. --cov-report=term-missing
```

### Examples to copy from

- Polled API integration: `integrations/bart.py` (compact, well-tested)
- Polled with TMDb canonicalization: `integrations/trakt.py`
- Webhook with state machine: `integrations/plex.py`
- Webhook with multi-tenant credentials: `integrations/message.py`
- OAuth device-code flow (shared): `integrations/google.py` + `integrations/youtube.py`
