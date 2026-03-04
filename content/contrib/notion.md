# notion.json

Notion integration — displays automation-triggered notifications on the board
via webhook. Each notification shows a `[W] FROM NOTION` header row followed
by the message body.

Unlike cron-scheduled templates, this template is triggered entirely by
incoming webhook events from Notion automations. No content is shown when
idle.

## Requirements

No Notion API key is needed — Notion automations send outbound HTTP requests
directly to the webhook listener. You only need:

- The webhook listener enabled in `config.toml` (see below)
- A Notion automation with an **HTTP request** action

## Configuration

No `[notion]` section is needed in `config.toml`. Enable the webhook listener
and the notion content:

```toml
[scheduler]
content_enabled = ["notion"]

[webhook]
# secret is auto-generated on first start and saved to config.toml — check the log.
```

To override hold, timeout, or priority, add a section to `config.toml`:

```toml
[notion.schedules.notification]
hold = 60
timeout = 60
priority = 8
```

| Override key | Default | Description |
|---|---|---|
| `hold` | `120` | Seconds to show the notification |
| `timeout` | `120` | Seconds the message can wait in the queue before being discarded |
| `priority` | `7` | Display priority (0–10) |

## Webhook setup

### 1. Enable the webhook listener

Add the `[webhook]` block shown in Configuration above. On first start, a
shared secret is auto-generated, printed to the log, and saved to
`config.toml` — it persists across restarts:

```
Webhook secret generated and saved to config.toml:
  <your-secret-here>
Copy this into your webhook sender (Plex, Shortcuts, etc.).
```

### 2. Build the webhook URL

```
http://<host-ip>:<host-port>/webhook/notion
```

Pass the secret via the `X-Webhook-Secret` header (preferred) or the
`?secret=` query parameter. Notion's HTTP request action supports custom
headers, so the header approach is recommended:

```
X-Webhook-Secret: <your-secret-here>
```

### 3. Configure a Notion automation

1. Open a Notion database → **Automations** → **New automation**
2. Choose a trigger (e.g. "Status changed to Done", "Property edited")
3. Add an action: **Send HTTP request**
4. Set the request:
   - **Method**: POST
   - **URL**: your webhook URL from step 2
   - **Headers**: `Content-Type: application/json`, `X-Webhook-Secret: <secret>`
   - **Body** (JSON): see payload schema below

## Payload schema

```json
{
  "message": "required — body text displayed on the board",
  "urgent": false,
  "tag": "notion"
}
```

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `message` | string | Yes | — | Body text. Newlines (`\n`) produce multiple display lines. |
| `urgent` | boolean | No | `false` | If `true`, interrupt the current hold immediately. |
| `tag` | string | No | `"notion"` | Deduplication key. A new notification replaces any queued message with the same tag. Set to `""` to disable superseding. |

The `tag` value is automatically namespaced — a caller-supplied `"reminders"`
becomes `"notion.reminders"` internally, preventing collisions with other
integrations.

### Example payloads

Minimal:
```json
{ "message": "Task completed: Q1 planning" }
```

With newlines:
```json
{ "message": "Task completed\nQ1 planning doc" }
```

Urgent with a custom dedup tag:
```json
{
  "message": "Deploy failed in production",
  "urgent": true,
  "tag": "deploy-alerts"
}
```

## Display format

**Note (3×15):**

```
[W] FROM NOTION
TASK COMPLETED
Q1 PLANNING DOC
```

**Flagship (6×22):**

```
[W] FROM NOTION
TASK COMPLETED
Q1 PLANNING DOC
```

- Row 1: `[W] FROM NOTION` (static header)
- Rows 2+: message body, word-wrapped to board width; excess rows are dropped

## Keeping data current

### Notion automation HTTP request action

Notion's automation HTTP action has been stable since launch. No hardcoded
data in this integration. If Notion changes the outbound request format or
adds authentication requirements, verify against:

- [Notion help: Automation actions](https://www.notion.so/help/automation-actions)
