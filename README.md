# E•NOTE•ION

![E•NOTE•ION](assets/social-preview.png)

[![CI](https://github.com/JasonPuglisi/e-note-ion/actions/workflows/ci.yml/badge.svg)](https://github.com/JasonPuglisi/e-note-ion/actions/workflows/ci.yml)

A self-hosted, code-first content scheduler for Vestaboard split-flap
displays. Define your board content as version-controlled JSON — cron
schedules, templated messages, live data integrations, and a priority queue —
with no web UI or cloud dependency required. Supports both the
[Note](https://shop.vestaboard.com/products/note) (3×15) and the Flagship
(6×22).

> This project is primarily agent-developed using [Claude](https://claude.ai),
> with human design, decision-making, guidance, and review. See
> [Philosophy](#philosophy) for more on the approach.

## Who this is for

E•NOTE•ION is built for developers and power users who want to treat their
board like infrastructure: content in files, schedules in cron, secrets in env
vars, deploys in Docker.

If you'd prefer a friendlier experience — a web UI, drag-and-drop scheduling,
and a polished setup flow — check out
[FiestaBoard](https://github.com/Fiestaboard/FiestaBoard), which nails that
use case beautifully.

## See also

The Vestaboard community has built a lot of great tooling:

| Project | What it does well |
|---|---|
| [FiestaBoard](https://github.com/Fiestaboard/FiestaBoard) | Full-featured self-hosted app with a web UI and a rich scheduling experience |
| [Vestaboard+](https://www.vestaboard.com/vestaboard-plus) | Official cloud subscription with Zapier/IFTTT integration and a curated app marketplace |
| [jparise/vesta](https://github.com/jparise/vesta) | Clean Python library for the Vestaboard API — great if you want to build your own tooling |
| [natekspencer/hacs-vestaboard](https://github.com/natekspencer/hacs-vestaboard) | Home Assistant integration for triggering board updates from automations |
| [Zapier](https://zapier.com) / [IFTTT](https://ifttt.com) | No-code workflow triggers via Vestaboard+ — lowest barrier to entry |
| MCP servers | Emerging tools for LLM-driven board updates from Claude and other agents |

## Running with Docker (recommended)

Pre-built multi-arch images (`linux/amd64`, `linux/arm64`) are published to
the GitHub Container Registry on each release.

First copy `config.example.toml` to `config.toml` and fill in your API keys
and settings (see [Configuration](#configuration) below). Then run:

```bash
docker run -d \
  --name e-note-ion \
  --restart unless-stopped \
  -v /path/to/config.toml:/app/config.toml:ro \
  -e CONTENT_ENABLED=bart \
  ghcr.io/jasonpuglisi/e-note-ion:latest
```

To mount personal content, add a volume pointing at `/app/content/user`:

```bash
  -v /path/to/your/content:/app/content/user \
```

Other environment variables:

```bash
  -e CONTENT_ENABLED=bart       # enable specific contrib files by stem
  -e CONTENT_ENABLED='*'        # enable all bundled contrib content
  -e FLAGSHIP=true              # target a Flagship (6×22) instead of a Note (3×15)
  -e PUBLIC=true                # only show templates marked public: true
```

Contrib integrations require their own API keys and configuration — see
[`content/README.md`](content/README.md) for details.

### Unraid

> **Note:** e-note-ion is not yet listed in the Community Applications store
> (tracked at [#10](https://github.com/JasonPuglisi/e-note-ion/issues/10)).

An Unraid Docker template is included at `unraid/e-note-ion.xml`. To install:

1. Download
   [`unraid/e-note-ion.xml`](https://raw.githubusercontent.com/JasonPuglisi/e-note-ion/main/unraid/e-note-ion.xml)
   and place it in `/boot/config/plugins/dockerMan/templates-user/` on your
   Unraid server
2. In the Unraid web UI, go to **Docker** → **Add Container** and select
   **e-note-ion** from the Template dropdown
3. Fill in the required fields (Vestaboard API Key, etc.) and click **Apply**

The template exposes all environment variables as UI fields and an optional
path for personal content.

## Configuration

Copy `config.example.toml` to `config.toml` and fill in your values:

```bash
cp config.example.toml config.toml
# edit config.toml — add your Vestaboard API key and any integration settings
```

`config.toml` is git-ignored and contains secrets — never commit it.

## Running directly

**Requirements:** Python 3.14+, [uv](https://github.com/astral-sh/uv)

```bash
uv sync
cp config.example.toml config.toml  # fill in your API key
python scheduler.py
```

```bash
python scheduler.py                          # Note (3×15), user content only
python scheduler.py --content-enabled bart   # also enable contrib/bart.json
python scheduler.py --content-enabled '*'    # enable all contrib content
python scheduler.py --flagship               # Flagship (6×22)
python scheduler.py --public                 # public templates only
```

Flags can be combined.

## Content files

Content is defined as JSON files in two directories:

- **`content/contrib/`** — bundled community-contributed content, disabled by
  default. Enable files by stem using `--content-enabled` / `CONTENT_ENABLED`.
  See [`content/README.md`](content/README.md) for available integrations.
- **`content/user/`** — personal content, always loaded. Git-ignored; mount
  your own directory here or symlink to a private repo for versioning.

Each file can contain multiple named templates, each with its own schedule and
display settings.

```json
{
  "templates": {
    "my_message": {
      "schedule": {
        "cron": "0 8 * * *",
        "hold": 600,
        "timeout": 600
      },
      "priority": 5,
      "public": true,
      "truncation": "word",
      "templates": [
        { "format": ["GOOD MORNING", "{quip}"] }
      ]
    }
  },
  "variables": {
    "quip": [
      ["HAVE A", "GREAT DAY"],
      ["YOU GOT", "THIS"]
    ]
  }
}
```

| Field | Description |
|---|---|
| `cron` | Standard 5-field cron expression |
| `hold` | Seconds the message stays on display before the next update |
| `timeout` | Seconds the message can wait in the queue before being discarded |
| `priority` | Integer 0–10; higher number runs first when multiple messages are queued |
| `public` | If `false`, excluded when running with `--public` |
| `truncation` | `hard` cuts mid-word (default); `word` stops at a word boundary; `ellipsis` adds `...` |

### Variables

`{variable}` placeholders are replaced with a randomly chosen option from the
corresponding `variables` entry. A format entry that is exactly `{variable}`
expands into all lines of the chosen option; an inline `{variable}` within
other text is replaced by the first line of the option.

When a template has multiple `{ "format": [...] }` entries, one is chosen at
random each time the template fires.

### Color squares

Color squares can be embedded in format strings using short tags: `[R]` `[O]`
`[Y]` `[G]` `[B]` `[V]` `[W]` `[K]` (red, orange, yellow, green, blue,
violet, white, black). Each tag renders as a single colored square on the
display.

### Wrapping and truncation

After variable expansion, lines are automatically word-wrapped to fit the
board width. If the result has more rows than the board height, the excess is
silently dropped. Content from dynamic sources (e.g. API responses) doesn't
need to be pre-fitted to the board dimensions.

### Integration templates

Templates can pull live data from an integration by adding
`"integration": "<name>"`. The worker calls the integration at job time to
fetch current variable values:

```json
{
  "templates": {
    "my_integration_template": {
      "schedule": { "cron": "*/5 * * * *", "hold": 60, "timeout": 60 },
      "priority": 8,
      "integration": "my_integration",
      "templates": [
        { "format": ["{line_1}", "{line_2}"] }
      ]
    }
  }
}
```

See [`content/README.md`](content/README.md) for available integrations and
their configuration.

## Philosophy

**Content as code.** Board messages live in JSON files alongside your other
dotfiles and configs. They're version-controlled, diff-able, and deployable
the same way as everything else. There's no database to back up, no UI state
to sync, and no vendor lock-in — just files, cron, and a single Python
process.

**An AI development experiment.** E•NOTE•ION is also an ongoing exploration of
agentic software development. Most of the implementation is written by Claude,
with a human setting direction, reviewing plans, and making architectural
calls. The goal isn't to remove the human — it's to see how far thoughtful
human–AI collaboration can go on a real project with real constraints.

## Development

```bash
uv sync
uv run pre-commit install
```

Run the full check suite before committing:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run bandit -c pyproject.toml -r .
uv run pip-audit
uv run pre-commit run pretty-format-json --all-files
```

All checks are also enforced as pre-commit hooks.

### Integration tests

Integration tests hit the real APIs and are excluded from the default `pytest`
run. To run them locally:

```bash
cp .env.example .env
# fill in your API keys — bare values, no surrounding quotes
uv run pytest -m integration -v
```

Required keys:

| Key | Where to get it |
|---|---|
| `VESTABOARD_VIRTUAL_API_KEY` | [web.vestaboard.com](https://web.vestaboard.com) → Developer → Virtual Boards |
| `BART_API_KEY` | [api.bart.gov/api/register.aspx](https://api.bart.gov/api/register.aspx) |

`.env` is git-ignored — never commit it.
