# E•NOTE•ION

![E•NOTE•ION](assets/social-preview.png)

[![CI](https://github.com/JasonPuglisi/e-note-ion/actions/workflows/ci.yml/badge.svg)](https://github.com/JasonPuglisi/e-note-ion/actions/workflows/ci.yml)

A cron-based content scheduler for Vestaboard split-flap displays. Supports
both the [Note](https://shop.vestaboard.com/products/note) (3×15) and the
Flagship (6×22). Schedules and sends templated messages on a per-template cron
schedule, with support for priority queuing, hold durations, and public/private
content modes.

> This project is primarily agent-developed using [Claude](https://claude.ai),
> with human design, decision-making, guidance, and review.

## Running with Docker (recommended)

Pre-built multi-arch images (`linux/amd64`, `linux/arm64`) are published to
the GitHub Container Registry on each release.

The image ships with bundled contrib content (disabled by default). Enable
it by name, or mount your own content directory:

```bash
docker run -d \
  --name e-note-ion \
  --restart unless-stopped \
  -e VESTABOARD_KEY=your_api_key_here \
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

An [Unraid Community Applications](https://unraid.net/community/apps) template
is included at `unraid/e-note-ion.xml`. Add it manually via the CA template
URL:

```
https://raw.githubusercontent.com/JasonPuglisi/e-note-ion/main/unraid/e-note-ion.xml
```

The template exposes all environment variables as UI fields and an optional
path for personal content.

## Running directly

**Requirements:** Python 3.14+, [uv](https://github.com/astral-sh/uv)

```bash
uv sync
export VESTABOARD_KEY=your_api_key_here
python e-note-ion.py
```

```bash
python e-note-ion.py                          # Note (3×15), user content only
python e-note-ion.py --content-enabled bart   # also enable contrib/bart.json
python e-note-ion.py --content-enabled '*'    # enable all contrib content
python e-note-ion.py --flagship               # Flagship (6×22)
python e-note-ion.py --public                 # public templates only
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
