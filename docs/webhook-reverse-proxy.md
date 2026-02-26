# Webhook TLS setup

The built-in webhook listener speaks plain HTTP. For **local senders** — Plex
Media Server on the same host or LAN, iOS Shortcuts on Wi-Fi — that is fine:
traffic never leaves your network. For **external senders** — iOS Shortcuts
over cellular, any service originating outside your LAN — the shared secret
would be transmitted in plaintext, so TLS is required.

This page covers the recommended setup options.

---

## Option 1 — Tailscale Funnel (recommended)

[Tailscale Funnel](https://tailscale.com/kb/1223/funnel) exposes a local port
on the public internet at a stable `https://<machine>.tail<hash>.ts.net` URL
with automatic TLS. No domain, no certificate management, no router port
forwarding needed.

### Unraid

1. Install the **Tailscale** Community Application (if not already installed)
   and authenticate it to your tailnet via **Settings → Tailscale**.
2. In `config.toml`, set `bind = "0.0.0.0"` so the container accepts
   connections from outside localhost. Restart the container.
3. Open the **Unraid terminal** (Tools → Terminal) and run:
   ```
   tailscale funnel --https=8443 --bg 32800
   ```
   Port 443 is occupied by Unraid's own web UI, so 8443 is used instead.
   Both 443 and 8443 are valid Tailscale Funnel ports. The `--bg` flag
   persists the configuration across Tailscale daemon restarts.
4. Your public webhook URL is:
   ```
   https://<machine>.tail<hash>.ts.net:8443/webhook/<integration>
   ```
   Pass the secret as a query parameter or `X-Webhook-Secret` header (see
   [Webhook setup](../AGENTS.md#webhook-integrations)).

> **Note:** Tailscale Funnel makes the endpoint reachable from the open
> internet. The shared secret protects it — guard it like a password.
>
> **Do not** use the per-container Tailscale integration available in the
> Docker container edit UI — that hook requires the container to run as
> root, which is unnecessary here and should be avoided.

### Docker Compose

Run Tailscale as a sidecar and share the network namespace:

```yaml
services:
  e-note-ion:
    image: ghcr.io/jasonpuglisi/e-note-ion
    network_mode: service:tailscale
    volumes:
      - ./config.toml:/app/config.toml

  tailscale:
    image: tailscale/tailscale
    environment:
      - TS_AUTHKEY=tskey-auth-...
      - TS_SERVE_CONFIG=/config/serve.json
    volumes:
      - ./tailscale:/var/lib/tailscale
      - ./tailscale-config:/config
    cap_add:
      - NET_ADMIN
      - NET_RAW
```

With `network_mode: service:tailscale`, the e-note-ion container shares
Tailscale's network stack. Configure Funnel in `tailscale-config/serve.json`
to route public HTTPS traffic on port 443 to `http://localhost:8080`.
See the [Tailscale Docker guide](https://tailscale.com/kb/1282/docker) for
the serve config format.

---

## Option 2 — Tailscale direct (sender on tailnet)

If the webhook sender is a device you control that already has Tailscale
installed (e.g. an iPhone running iOS Shortcuts), you can skip Funnel
entirely. All traffic between Tailscale nodes is WireGuard-encrypted, so
plain HTTP over the tailnet is safe.

1. Ensure both the Unraid host and the sending device are on the same tailnet.
2. Set `bind = "0.0.0.0"` in `config.toml`.
3. Use the Unraid node's **Tailscale IP** (e.g. `100.x.x.x`) as the webhook
   host:
   ```
   http://100.x.x.x:32800/webhook/<integration>?secret=<your-secret>
   ```

No router changes, no TLS certificates, no Funnel quota.

---

## Option 3 — Nginx Proxy Manager or Caddy (custom domain)

If you already have a reverse proxy running and want to use a custom domain,
point it at the webhook host port (`32800` by default on Unraid) and enable
HTTPS as you normally would.

The listener reads the secret from the `X-Webhook-Secret` header or the
`?secret=` query parameter — no special header rewriting is required. Refer
to your reverse proxy's own documentation for TLS and Let's Encrypt setup.

---

## What you do NOT need

- **Router port forwarding** — Tailscale Funnel handles inbound connections
  without any changes to your router or firewall.
- **A custom domain** — Funnel provides a stable `*.ts.net` hostname.
- **Certificate management** — Funnel provisions and renews TLS certificates
  automatically.
