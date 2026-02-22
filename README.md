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

The image ships with bundled sample content that runs automatically — no
content files needed to get started. To use your own templates, mount a
directory of JSON files over `/app/content`:

```bash
docker run -d \
  --name e-note-ion \
  --restart unless-stopped \
  -e VESTABOARD_KEY=your_api_key_here \
  -v /path/to/your/content:/app/content \
  ghcr.io/jasonpuglisi/e-note-ion:latest
```

To target a Flagship board or enable public mode, add the corresponding
environment variables:

```bash
  -e FLAGSHIP=true \   # target a Flagship (6×22) instead of a Note (3×15)
  -e PUBLIC=true \     # only show templates marked public: true
```

### Unraid

An [Unraid Community Applications](https://unraid.net/community/apps) template
is included at `unraid/e-note-ion.xml`. Add it manually via the CA template
URL:

```
https://raw.githubusercontent.com/JasonPuglisi/e-note-ion/main/unraid/e-note-ion.xml
```

The template exposes `VESTABOARD_KEY`, `FLAGSHIP`, and `PUBLIC` as UI fields.
The content path is optional — leave it blank to use the bundled sample
content, or set it to a host directory containing your own JSON files.

## Running directly

**Requirements:** Python 3.14+, [uv](https://github.com/astral-sh/uv)

```bash
uv sync
export VESTABOARD_KEY=your_api_key_here
python e-note-ion.py
```

```bash
python e-note-ion.py             # Note (3×15), all templates
python e-note-ion.py --flagship  # Flagship (6×22)
python e-note-ion.py --public    # public templates only
```

`--flagship` and `--public` can be combined.

## Content files

Content is defined as JSON files in the `content/` directory. Each file can
contain multiple named templates, each with its own schedule and display
settings. Files are watched at runtime — adding, editing, or removing a file
takes effect within a few seconds without restarting.

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

### Variables

`{variable}` placeholders are replaced with a randomly chosen option from the
corresponding `variables` entry. A format entry that is exactly `{variable}`
expands into all lines of the chosen option; an inline `{variable}` within
other text is replaced by the first line of the option.

When a template has multiple `{ "format": [...] }` entries, one is chosen at
random each time the template fires.

### Wrapping and truncation

After variable expansion, lines are automatically word-wrapped to fit the
board width. Words are packed greedily; a word wider than the full row is
hard-truncated. If the result has more rows than the board height, the excess
is silently dropped. Content from dynamic sources (e.g. API responses) doesn't
need to be pre-fitted to the board dimensions.

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
```

All checks are also enforced as pre-commit hooks.
