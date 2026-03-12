Closes #

## Changes

<!-- Brief description of what this PR does. -->

## Checklist

- [ ] Linked issue exists and was discussed before coding
- [ ] Tests added or updated; `uv run pytest` passes
- [ ] `uv run ruff check .` and `uv run ruff format --check .` clean
- [ ] `uv run pyright` clean
- [ ] `uv run bandit -c pyproject.toml -r .` clean
- [ ] No secrets logged or exposed; all HTTP calls include `timeout=`
- [ ] `README.md` / `AGENTS.md` / sidecar docs updated if needed
- [ ] Version bumped in `pyproject.toml` (and `uv.lock` staged) if release-worthy
