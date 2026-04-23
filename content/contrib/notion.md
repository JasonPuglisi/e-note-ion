# notion.json

Notion integration — displays automation-triggered notifications on the board
via webhook. Each notification shows a `[W] NOTION` header row followed
by the message body. Social — webhook-only, queues normally.

Unlike cron-scheduled templates, this template is triggered entirely by
incoming webhook events from Notion automations. No content is shown when
idle.

## Schedule

**Webhook-only** (no cron). Fires when a Notion automation sends an HTTP
request to the webhook listener.
**Hold:** 120 s | **Timeout:** 120 s | **Priority:** 7 (Social)

Queues normally (`interrupt=False`) — does not cut an active hold short. At
priority 7 it shows after any priority-8+ content (Plex, BART, friend messages)
but ahead of lower-priority scheduled content. The short 120 s timeout is
intentional — Notion notifications are contextual to the moment they're
triggered; a stale notification is rarely useful.

## Requirements

No Notion API key is needed — Notion automations send outbound HTTP requests
directly to the webhook listener. You only need:

- The webhook listener enabled in `config.toml` (see below)
- A Notion automation with an **HTTP request** action

## Configuration

Enable the webhook listener and create a named credential:

```toml
[webhook]

[webhook.credentials.notion]
# Generate a hash from your chosen plaintext secret:
#   python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('your-secret'))"
secret_hash = "$argon2id$v=19$m=65536,t=3,p=4$..."
webhooks = ["notion"]
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

Add the `[webhook]` and `[webhook.credentials.notion]` blocks shown in
Configuration above. Choose any plaintext secret, hash it with the provided
command, and store the hash. Keep the plaintext secret — you will paste it into
the Notion automation header.

### 2. Build the webhook URL

```
http://<host-ip>:<host-port>/webhook/notion
```

Pass the plaintext secret via the `X-Webhook-Secret` header (preferred) or the
`?secret=` query parameter. Notion's HTTP request action supports custom
headers, so the header approach is recommended:

```
X-Webhook-Secret: <your-plaintext-secret>
```

### 3. Configure a Notion automation

1. Open a Notion database → **Automations** → **New automation**
2. Choose a trigger (e.g. "Status changed to Done", "Property edited")
3. Add an action: **Send HTTP request**
4. Set the request:
   - **Method**: POST
   - **URL**: your webhook URL from step 2
   - **Headers**: `Content-Type: application/json`, `X-Webhook-Secret: <secret>`
   - **Body** (JSON): the payload the action posts must match the shape
     described below. Add properties named `message` (title), `urgent`
     (checkbox), and `tag` (select) to the triggering database so their
     values are available to the request body.

## Payload schema

The endpoint accepts Notion's **native page-properties payload** — the same
nested shape that Notion's own API uses when describing page properties. The
handler reads three properties by name under `data.properties`:

```json
{
  "data": {
    "properties": {
      "message": { "title": [{ "plain_text": "..." }] },
      "urgent":  { "checkbox": false },
      "tag":     { "select": { "name": "reminders" } }
    }
  }
}
```

This shape is chosen because the primary caller is a Notion database
automation's **Send HTTP request** action, which posts the triggering page's
properties in Notion's native nested format. No body composition is needed
when configuring an automation from a database that has `message` (title),
`urgent` (checkbox), and `tag` (select) properties.

| Property path | Type | Required | Default | Description |
|---|---|---|---|---|
| `data.properties.message.title[].plain_text` | rich-text array (segments concatenated) | Yes | — | Body text. Newlines (`\n`) produce multiple display lines. Color tag syntax (`[R]`, `[G]`, etc.) is not interpreted — brackets are stripped and the letter is kept. |
| `data.properties.urgent.checkbox` | boolean | No | `false` | If `true`, attempt to interrupt the current hold immediately. |
| `data.properties.tag.select.name` | string | No | `"notion"` | Deduplication key. A new notification replaces any queued message with the same tag. Set to `""` to disable superseding. |

The `tag.select.name` value is automatically namespaced — a caller-supplied
`"reminders"` becomes `"notion.reminders"` internally, preventing collisions
with other integrations.

### Hand-crafted senders

Non-Notion callers (iOS Shortcuts, other SaaS webhooks, programmatic scripts)
must wrap values in the same nested shape. The handler silently responds
`200 "Discarded"` — with no display side-effect — if `data.properties`
is missing or `message.title[]` is empty.

### Example payloads

Minimal:
```json
{
  "data": {
    "properties": {
      "message": { "title": [{ "plain_text": "Task completed: Q1 planning" }] }
    }
  }
}
```

With newlines:
```json
{
  "data": {
    "properties": {
      "message": { "title": [{ "plain_text": "Task completed\nQ1 planning doc" }] }
    }
  }
}
```

Urgent with a custom dedup tag:
```json
{
  "data": {
    "properties": {
      "message": { "title": [{ "plain_text": "Deploy failed in production" }] },
      "urgent":  { "checkbox": true },
      "tag":     { "select": { "name": "deploy-alerts" } }
    }
  }
}
```

## Display format

**Note (3×15):**

```
[W] NOTION
TASK COMPLETED
Q1 PLANNING DOC
```

**Flagship (6×22):**

```
[W] NOTION
TASK COMPLETED
Q1 PLANNING DOC
```

- Row 1: `[W] NOTION` (static header)
- Rows 2+: message body, word-wrapped to board width; excess rows are dropped

## Keeping data current

### Notion automation HTTP request action

Notion's automation HTTP action has been stable since launch. No hardcoded
data in this integration. If Notion changes the outbound request format or
adds authentication requirements, verify against:

- [Notion help: Automation actions](https://www.notion.so/help/automation-actions)
