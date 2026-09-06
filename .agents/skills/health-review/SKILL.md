---
name: health-review
description: Run the periodic project health audit for e-note-ion. Comprehensive checklist covering test coverage, code patterns, dependency CVEs, security posture, docs drift, stale comments, CI/CD hygiene, branch ruleset integrity, integration test status, GitHub-managed workflows, and issue hygiene. Invoke when the user asks for a "health check", "audit", "project review", at natural breakpoints (before a minor/major release, after a sprint of feature work), or whenever broad cross-cutting drift seems likely.
license: MIT
compatibility: Requires uv, git, the gh CLI authenticated against JasonPuglisi/e-note-ion, and network access.
metadata:
  last-verified: "2026-09-06"
  health: healthy
---

# Periodic Health Review

## Description

Comprehensive, repeatable audit of project health across code, tests, dependencies, docs, CI/CD, and the issue tracker. Designed to surface drift before it compounds.

## When to Use

- User asks for a "health check", "audit", "project review", or similar
- Before a minor or major release (any time `version` is about to bump)
- After a sprint of feature work (multiple PRs merged in close succession)
- When you suspect cross-cutting drift (docs feel stale, "that hasn't been touched in a while" feeling)

## Process

1. **Run quick local checks first** (cheap, no agents needed):
   - `uv run pip-audit` — flag any open CVEs
   - `gh run list --workflow CI --branch main --limit 8` — confirm recent runs green
   - `gh api repos/JasonPuglisi/e-note-ion/code-scanning/alerts ...` and `.../dependabot/alerts` — open alerts
   - `gh issue list --state open --json number,labels,milestone | jq` — count untriaged issues
   - `gh api repos/JasonPuglisi/e-note-ion/rulesets/13082160 --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'` — check ruleset
   - `grep -rn "TODO\|FIXME\|XXX\|HACK" integrations/ *.py` — stale comments
   - `uv run bandit -c pyproject.toml -r . 2>&1 | grep -E "Total lines skipped|Test in comment"` — **`Total lines skipped (#nosec)` must be `0`**, and there must be zero "Test in comment" warnings (see Checklist 2)
2. **Spawn parallel subagents** for the breadth-heavy checks (test coverage, code-pattern consistency, docs drift, issue hygiene). Use `Explore` for read-only scans; use `general-purpose` when the agent needs Bash. Brief each agent with a tight scope and a word cap.
3. **VERIFY every agent claim against the source before filing or fixing.** Subagents produce confident-sounding false positives — typically misreading conventions, missing recent changes, or applying generic intuition. A health-review pass that mostly trusts agents will file noise issues.
4. **Apply the issue-volume convention** (see Constraints) to decide between inline fixes, individual issues, and a tracking issue.
5. **Fix inline what's small** (one-line cleanups, missing docs entries, unassigned issues). File issues for substantive work. Use AskUserQuestion when the call is genuinely ambiguous, not for things you can grep.
6. **Report back** with: clean ✅ findings, filed/fixed items, false positives caught (worth flagging — shapes how to interpret these reviews), and any meta improvements to the checklist itself.

## Checklist

1. **Test coverage** — gaps in unit tests; retroactive coverage for untested logic. Check `uv run pytest --cov --cov-report=term-missing` for actual numbers (report-only, no threshold). Do not add `--cov=.` — it pulls the test files into the report and inflates the total.
2. **Code patterns** — consistency across integrations: import aliases (`_vb`, `_media`, `_sched`, `_config_mod`), error handling, `requests` calls always have `timeout=`.

   `# nosec` needs more than a grep. Bandit parses everything between `nosec` and
   the next `#` as test IDs, so `# nosec S311 — reason` (ruff's code, plus prose)
   resolves to no valid ID — and an empty ID set means *suppress every rule on
   this line*. A grep for "has a rule ID" passes on exactly the lines that are
   silently broken. Read bandit's own metrics instead: `Total lines skipped
   (#nosec)` must be `0`, and any "Test in comment: X is not a test name"
   warning is a malformed suppression. Correct form is
   `# nosec BXXX  # justification`.
3. **Dependency health** — `uv run pip-audit`; flag any CVEs. Be opportunistic: if any deps can be upgraded cleanly, run `uv lock --upgrade` regardless of whether a CVE forces it.
4. **Security posture** — timeouts on all HTTP calls; secrets not logged; `# nosec` justifications still valid; no new credentials introduced without webhook autogen wiring.
5. **Docs drift** — README / AGENTS.md / sidecar docs accurate and not duplicating each other; env var lists synced across the five locations (conftest.py, ci.yml, .env.example, README.md, AGENTS.md).
6. **Stale comments** — no unresolved TODO/FIXME in source; no superseded inline notes.
7. **CI/CD hygiene** — job permissions minimal; step names accurate; post-merge `main` runs passing clean.
   - **7b. GitHub-managed workflows.** Beyond CI, check `Dependency Graph`, `Auto Release`, `CodeQL`, and any "automatic dependency submission" workflow on recent main runs. These don't appear in `.github/workflows/` but failures here are easy to miss because they're not part of "the runs we care about".
8. **Branch ruleset integrity** — required status check names match actual CI job names in `ci.yml`:
   ```
   gh api repos/JasonPuglisi/e-note-ion/rulesets/13082160 --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'
   ```
9. **Integration test hygiene** — advisory CI job passing on `main`; GitHub environment secrets/vars match the env var lists; new integrations have `test_<name>_integration.py` and env vars in `tests/integrations/conftest.py`.
   - `TRAKT_ACCESS_TOKEN` expires every ~90 days — copy from `config.toml` if Trakt integration tests start failing with auth errors.
   - `GOOGLE_REFRESH_TOKEN`: Production OAuth mode → no expiry. Testing mode → 7-day expiry (Google constraint).
   - **Run them if `.env` is populated** (`uv run pytest -m integration -v`), and
     **read the warnings summary, not just pass/fail**. Live calls surface things
     mocks structurally cannot: upstream deprecations, changed response shapes,
     new rate limits. A run that passes with three `DeprecationWarning`s is a
     finding, not a green light.
10. **Documented commands vs. what CI actually runs** — diff every command string
    in `README.md` and `AGENTS.md` against the steps in `.github/workflows/ci.yml`.
    Drift here is invisible locally: the docs can be wrong for months while CI stays
    green, and anyone following the docs gets a different answer than the pipeline.
11. **Dead config keys** — cross-check `config.example.toml` in both directions
    against what the code reads:
    ```
    grep -oE '^#? *[a-z_]+ *=' config.example.toml | tr -d '#= ' | sort -u
    grep -rhoE "get(_optional)?(_bool)?\('[a-z_.]+', *'[a-z_]+'" . --include='*.py' | grep -v '\.venv'
    ```
    A key documented as configurable that no code reads is worse than an
    undocumented one — users set it and nothing happens.
12. **Stale workaround comments** — every comment citing an upstream issue or PR
    as the reason for a workaround. Check whether it closed or merged
    (`gh api repos/<owner>/<repo>/issues/<n> --jq '.state'`). Workarounds outlive
    their cause silently.
13. **Audit the skills themselves** — this file and its siblings under
    `.agents/skills/`. Confirm frontmatter is spec-valid (`name` matches the
    directory, `description` under 1024 chars, body under 500 lines), the
    `.claude/skills/` symlinks still resolve, and the content still matches how
    the project actually works. Bump `metadata.last-verified` only for skills you
    genuinely re-read this pass.
14. **Sibling repo drift** — `../unraid-templates` and
    `../homebridge-e-note-ion` (see Related Projects in AGENTS.md). CI here
    cannot see either, so nothing catches these automatically:
    - Every `VOLUME` / `EXPOSE` / mount path in `Dockerfile` has a matching
      `<Config>` entry in `e-note-ion.xml`:
      ```
      grep -oE 'Target="[^"]*"' ../unraid-templates/e-note-ion.xml
      grep -nE '^(VOLUME|EXPOSE)' Dockerfile
      ```
    - The plugin's client still matches the webhook/state surface it calls
      (`POST /webhook/scheduler` actions, `GET /state` response shape,
      `[homebridge]` push contract).
    - Both repos' own dependency and CI health: `npm audit`, `npm outdated`,
      actions SHA-pinned, Dependabot configured. They are small enough that a
      quick pass costs little and they go stale unnoticed.
15. **Issue hygiene**:
    - Every open issue has at least one label and a milestone; no untriaged issues.
    - Blocking relationships explicit. Prefer GitHub's native **issue dependencies** ("Add dependency" via the issue UI / `gh api`) over free-text "Blocked by #N" — they update automatically and surface in the UI.
    - **Stale blocker cleanup**: when a blocker closes, audit any open issues that referenced it. Confirm they're now actionable; add a "blocker resolved — now actionable" comment or update the body. (Free-text "Blocked by #N" notes left around after the blocker closes are easy to miss.)
    - Tracking issues have sub-issues linked (`gh api repos/JasonPuglisi/e-note-ion/issues/<n>/sub_issues`).
    - Stale or superseded issues closed with a note.
    - Milestone assignments reflect current priorities (move issues between Next/Later as needed).

## Constraints

- **Read-only investigation; verify before fixing.** Don't act on agent claims without grepping the source first.
- **Verify your own removals by re-running the tool, not by reading its warnings.**
  The reviewer is a source of false positives too. Bandit emits "nosec
  encountered (BXXX), but no failed test" per *node*, so it fires on lines whose
  suppression is genuinely load-bearing; deleting on that signal alone
  reintroduces real findings. Before removing any suppression, guard, or
  workaround: remove it, re-run the tool, and confirm nothing comes back.
- **Grep the bare symbol before any rename, not the qualified path.** A search
  for `scheduler.vestaboard` returns nothing while seven
  `patch.object(_mod.vestaboard, ...)` call sites exist. Search the identifier
  alone, then narrow.
- **`metadata.last-verified` is an output, not a stamp.** Bump it only on skills
  you actually re-read during the pass. Never bulk-update it, and never set it
  when the audit was cut short — leave the old date and note what was skipped.
  (Ported from the Verification skill in the Notion instructions database, which
  applies the same rule to its `Verified` / `Health` properties.)
- **Issue-volume convention**:
  - **≤3 findings** → file individual issues (or fix inline if each is one line).
  - **>3 findings** → open one tracking issue with sub-issues, link from each PR. Avoids tracker clutter.
- **Don't file issues for one-line cleanups** — fix inline in the same PR that surfaces them.
- **Don't propose closing issues without strong evidence.** Stale ≠ superseded.
- **Don't widen scope.** Health review surfaces and triages; it doesn't fix everything in one pass.

## Reference

### Quick commands

```bash
# Open security alerts (CodeQL + Dependabot)
gh api 'repos/JasonPuglisi/e-note-ion/code-scanning/alerts?state=open' --jq '.[] | {rule: .rule.id, severity: .rule.severity, path: .most_recent_instance.location.path}'
gh api repos/JasonPuglisi/e-note-ion/dependabot/alerts --jq '.[] | select(.state=="open") | {pkg: .security_vulnerability.package.name, severity: .security_advisory.severity}'

# Untriaged issues (no label or no milestone)
gh issue list --state open --limit 100 --json number,title,labels,milestone | jq '[.[] | select((.labels|length)==0 or .milestone == null)] | length'

# Required status checks vs actual CI job names
gh api repos/JasonPuglisi/e-note-ion/rulesets/13082160 --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[].context'
grep -E "^\s+name:" .github/workflows/ci.yml
```

### Five-location env-var sync

When checking env vars, all five must agree:
1. `tests/integrations/conftest.py` (`_INTEGRATION_VARS`)
2. `.github/workflows/ci.yml` (env block)
3. `.env.example`
4. `README.md` (integration test required-keys table)
5. `AGENTS.md` (integration tests section + GitHub secrets list)

### Subagent prompts that work

- For broad cross-cutting reviews: spawn 4 agents in parallel (test coverage, code patterns, docs drift, issue hygiene). Cap each at ~300 words.
- Always tell agents: "don't propose fixes, just enumerate findings."
- Always tell agents: "use file:line references" — makes verification trivial.
- Tradeoff: `Explore` agent is faster but doesn't have Bash; reach for `general-purpose` when filesystem traversal or `gh` calls are needed.

### Cadence

Roughly: any time before a minor/major release, after a sprint of feature work, or when explicitly requested. Codifying a calendar cadence doesn't fit a hobby project's irregular activity — let triggers drive it.
