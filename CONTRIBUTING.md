# Contributing to e-note-ion

Thanks for your interest. This is a personal project with a small scope, so
contributions are welcome but selective.

## Filing issues

Before opening an issue, check for duplicates in the [issue tracker][issues].

Include:
- What you expected vs. what happened (for bugs)
- Your board model (Note or Flagship), Python version, and deployment method
- Relevant config (redact any API keys)

Out-of-scope requests (e.g. support for other display hardware) will be closed
with an explanation.

## Pull requests

Before writing code, open an issue so we can discuss whether the change fits
the project. PRs without a linked issue may be closed.

Requirements for all PRs:
- Tests for any new logic (`uv run pytest` must pass)
- `uv run ruff check .` and `uv run ruff format --check .` clean
- `uv run pyright` clean
- No new secrets logged or exposed; all HTTP calls include `timeout=`

See [AGENTS.md](AGENTS.md) for code conventions (indentation, type hints,
security practices, integration patterns).

[issues]: https://github.com/JasonPuglisi/e-note-ion/issues
