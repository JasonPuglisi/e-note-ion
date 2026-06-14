# scheduler

Webhook-only scheduler control for the Vestaboard display. Supports two
runtime toggles — quiet mode and public mode — both persisted to `config.toml`
so they survive restarts (including Docker container recreates).

**Quiet mode:** When active, the worker renders content normally but stores the
result as virtual state instead of sending it to the board. On wake, the
virtual state is sent immediately so the board shows contextually relevant
content without waiting for the next cron cycle. Idle refresh continues during
quiet mode, keeping the virtual state current (e.g. real-time BART departures
update in the background).

**Public mode:** When active, the worker skips templates marked `private = true`,
hiding personal content when the display is in a guest-visible space. Templates
are always loaded and scheduled regardless of public mode — the filter applies
at display time, so toggling public mode takes effect immediately without
restarting.

## Schedule

**Webhook-only** (no cron, no display content). The `handle_webhook` returns
`None` — it modifies scheduler behaviour, not the display queue.

## How it works

### Quiet mode

1. **Quiet** (`{"action": "quiet"}`): sets `quiet.active = true` in
   `config.toml`. The worker starts routing rendered content to virtual state
   instead of the Vestaboard API.
2. **Wake** (`{"action": "wake"}`): sets `quiet.active = false`. On the next
   worker loop iteration (within ~1 s), the worker detects the transition,
   sends the stored virtual state to the board, and resumes normal operation.

During quiet mode:
- Cron jobs continue firing and rendering content normally
- Rendered character grids are stored in memory as virtual state
- Idle refresh keeps virtual state current for real-time integrations
- The hold timer is skipped — the worker moves through queued messages at
  render speed, keeping only the latest virtual state
- No API calls are made to the Vestaboard

### Public mode

1. **Public** (`{"action": "public"}`): enables public mode. The worker starts
   skipping any queued message whose template has `private = true`.
   Already-displaying messages are not interrupted — the filter applies at the
   next dequeue.
2. **Private** (`{"action": "private"}`): disables public mode. All templates
   resume displaying normally.

The initial state is read from `[scheduler].public` in `config.toml` (or the
`--public` CLI flag). Runtime changes via webhook override the config value
and persist it for future restarts.

## Configuration

Both modes are controlled via webhook. Persisted state is stored automatically:

```toml
[scheduler]
# Written by quiet.set_quiet() — can also be set manually before startup.
quiet = false

# Written by public.set_public() — can also be set manually before startup.
public = false
```

## Webhook setup

The scheduler credential is auto-generated on first startup. Check the log
for the plaintext secret:

```
INFO - Auto-generated webhook credential 'scheduler': <secret>
```

Copy this secret into your iOS Shortcuts (see below).

**Endpoint:** `POST /webhook/scheduler`

**Authentication:** `X-Webhook-Secret: <secret>` header (preferred) or
`?secret=<secret>` query parameter.

## Payload schema

```json
{"action": "quiet"}
```

```json
{"action": "wake"}
```

```json
{"action": "public"}
```

```json
{"action": "private"}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `action` | string | Yes | `"quiet"`, `"wake"`, `"public"`, or `"private"` |

Invalid or missing `action` returns a 500 error.

## iOS Shortcuts setup

Quiet mode and public mode are designed to be triggered by iOS Shortcuts
Personal Automations. Both use the same `POST /webhook/scheduler` endpoint
and the same auto-generated credential. The setup uses reusable Shortcuts
called by multiple automation triggers.

### Prerequisites

- Webhook listener enabled in `config.toml` (with a route to your device —
  Cloudflare Tunnel, LAN, etc.)
- The auto-generated scheduler webhook secret from the log
- iOS 18+ (Focus mode triggers; Arrive/Leave triggers)

### Step 1: Create the Shortcuts

Build four Shortcuts — one per action (quiet, wake, public, private). Each
is a simple webhook POST with no conditional logic, so redundant calls (e.g.
waking an already-awake board) are harmless.

#### Vestaboard Quiet

A pre-built template is available at
`content/contrib/shortcuts/Vestaboard Quiet.shortcut`. Import it and fill in
the two import questions. To build manually:

1. Open Shortcuts > tap **+** > name it "Vestaboard Quiet"
2. Add **Text** > paste the webhook secret (replaced by import question);
   rename output to `secret`
3. Add **Get Contents of URL**:
   - URL: `https://<your-webhook-url>/webhook/scheduler` (replaced by import
     question)
   - Method: **POST**
   - Headers: add `X-Webhook-Secret` > `secret` magic variable from step 2
   - Body: **JSON** > add key `action` (Type: Text) > value `quiet`
4. Tap the **i** button > **Import Questions** > add two questions:
   - Question: `Webhook URL`, Parameter: URL field of Get Contents of URL
   - Question: `Webhook secret`, Parameter: the Text action from step 2

#### Vestaboard Wake

A pre-built template is available at
`content/contrib/shortcuts/Vestaboard Wake.shortcut`. Same as above, but:
- Name it "Vestaboard Wake"
- Change the `action` value in the JSON body to `wake`

#### Vestaboard Public

A pre-built template is available at
`content/contrib/shortcuts/Vestaboard Public.shortcut`. Same structure as
Quiet, but with a different action value:
- Name it "Vestaboard Public"
- Body: **JSON** > add key `action` (Type: Text) > value `public`

#### Vestaboard Private

A pre-built template is available at
`content/contrib/shortcuts/Vestaboard Private.shortcut`. Same as Public, but:
- Name it "Vestaboard Private"
- Change the `action` value in the JSON body to `private`

### Step 2: Create the automations

Create Personal Automations that call the Shortcuts above. Each automation
is a single "Run Shortcut" action.

#### Bedtime Begins > Quiet

1. Open Shortcuts > **Automation** tab > **+** > **Personal Automation**
2. Scroll to **Sleep** section > tap **Bedtime Begins**
3. Deselect **Notify When Run** (so it runs silently)
4. Add **Run Shortcut** > select "Vestaboard Quiet"

#### Waking Up > Wake

Same as above, but:
- Choose **Waking Up** instead of Bedtime Begins
- Select "Vestaboard Wake" instead of "Vestaboard Quiet"

### Focus mode triggers

Sleep Focus only offers Bedtime Begins and Waking Up as automation triggers —
it lacks the standard "When Turning On/Off" triggers that other Focus modes
have. The Waking Up trigger only fires from the Health app's sleep schedule,
not when manually switching away from Sleep to another Focus. This means a
"turns off" trigger cannot reliably detect when Sleep ends.

The recommended strategy is to **categorize each Focus as quiet or wake and
only use "turns on" triggers**. When you always have a Focus active, switching
between them fires the new Focus's "turns on" trigger, which is reliable.

Create one automation per Focus mode, each calling the appropriate Shortcut:

- **Quiet focuses** (board should sleep): DND, Sleep (via Bedtime Begins)
- **Wake focuses** (board should be active): Personal, Work, etc.

Since the webhook calls are idempotent (waking an awake board or quieting a
quiet board is a no-op), redundant triggers from Focus switches are harmless.

> **Note:** This approach requires that you always have a Focus active. If you
> sometimes have no Focus on, you'll also need a "DND turns off" > Wake
> automation as a fallback.

### Arrive and Leave (public mode)

Public mode can be toggled automatically based on whether you are home using
iOS Shortcuts **Arrive** and **Leave** Personal Automation triggers (under
Travel Triggers). They use geofencing, run automatically without confirmation
on iOS 17+, and fire the same `POST /webhook/scheduler` endpoint.

1. **Leave home** > Run Shortcut "Vestaboard Public" (hide private content
   when away — guests may be visiting)
2. **Arrive home** > Run Shortcut "Vestaboard Private" (restore private
   content when home)

Note: Arrive/Leave triggers have a minimum geofence radius of ~100 m and can
be delayed in low-power mode.

### Summary of automations

| Trigger | Shortcut | Purpose |
|---|---|---|
| Bedtime Begins | Vestaboard Quiet | Nightly quiet |
| Waking Up | Vestaboard Wake | Scheduled wake |
| DND turns on | Vestaboard Quiet | Nap / ad-hoc quiet |
| Personal turns on | Vestaboard Wake | Wake on Focus switch |
| Work turns on | Vestaboard Wake | Wake on Focus switch |
| Leave home | Vestaboard Public | Hide private content |
| Arrive home | Vestaboard Private | Restore private content |

All automations should have **Notify When Run** deselected so they run
silently.

## Apple Home (HomeBridge)

Both modes can also be exposed as native **Apple Home** switches via HomeBridge,
giving manual toggles, Siri control, and Home automations alongside the iOS
Shortcuts path above. The scheduler exposes a read-side `GET /state` endpoint
(current `quiet`/`public` state plus board content) for switch sync and an
optional `[homebridge]` push so the switches update instantly on a mode change.
See [`docs/homebridge.md`](../../docs/homebridge.md) for the full setup.

## Interaction with hardware quiet hours

If the Vestaboard's hardware quiet hours overlap with software quiet mode,
both operate independently. Software quiet mode intercepts *before* the API
call, so the board never receives a 423 "locked" response during software
quiet. On wake, the virtual state is sent normally — if the board's hardware
quiet hours are still active, the API will return 423 and the worker will
handle it as usual (clear hold, re-enqueue).

For most setups, you can disable the Vestaboard's hardware quiet hours
entirely and rely on software quiet mode for full control via your sleep
schedule.

## Keeping data current

This integration has no hardcoded external data. No periodic maintenance
required.
