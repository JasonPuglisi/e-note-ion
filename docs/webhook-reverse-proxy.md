# Webhook TLS setup

The built-in webhook listener speaks plain HTTP. For **local senders** — Plex
Media Server on the same host or LAN, iOS Shortcuts on Wi-Fi — that is fine:
traffic never leaves your network. For **external senders** — iOS Shortcuts
over cellular, any service originating outside your LAN — the shared secret
would be transmitted in plaintext, so TLS is required.

This page covers the recommended setup options.

---

## Option 1 — Cloudflare Tunnel (recommended)

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
exposes a local port on the public internet at a stable `https://` URL with
automatic TLS. No domain ownership is required (Cloudflare provides a free
`*.trycloudflare.com` hostname), no router port forwarding, and no certificate
management.

`cloudflared` runs as a lightweight sidecar container alongside e-note-ion —
a clean fit for both Docker Compose and Unraid.

### Step 1 — Create a tunnel

1. Log in to the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/).
2. Go to **Networks → Tunnels** and click **Create a tunnel**.
3. Select **Cloudflared** as the connector type, give the tunnel a name, and
   follow the prompts. At the end, copy the **tunnel token** shown on screen
   (a long base64 string). Keep it safe — treat it like a password.
4. Under **Public Hostname**, add a route: point any hostname to
   `http://localhost:8080` (the webhook listener's internal address). Save.

### Unraid

1. In the Unraid **Community Applications** panel, search for **cloudflared**
   and install it.
2. Set the `TUNNEL_TOKEN` environment variable to the token from Step 1.
3. In your e-note-ion Docker settings, set `bind = "0.0.0.0"` in `config.toml`
   and confirm the webhook port mapping is active (container `8080` → host
   `32800` by default).
4. Start the cloudflared container. Your public webhook URL will be the
   hostname you configured in the Cloudflare dashboard:
   ```
   https://<your-hostname>/webhook/<integration>
   ```

> **Note:** Both containers must be on the same Docker bridge network so
> cloudflared can reach e-note-ion at `http://e-note-ion:8080`, or configure
> cloudflared to target the host IP (e.g. `http://172.17.0.1:32800`) instead
> of `localhost`.

### Docker Compose

Run `cloudflared` as a sidecar sharing e-note-ion's network namespace:

```yaml
services:
  e-note-ion:
    image: ghcr.io/jasonpuglisi/e-note-ion
    volumes:
      - ./config.toml:/app/config.toml

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run
    environment:
      - TUNNEL_TOKEN=${TUNNEL_TOKEN}
    network_mode: service:e-note-ion
    depends_on:
      - e-note-ion
```

With `network_mode: service:e-note-ion`, cloudflared shares e-note-ion's
network stack and can reach the webhook listener at `http://localhost:8080`.
Store `TUNNEL_TOKEN` in a `.env` file next to `docker-compose.yml` — do not
commit it to version control.

---

## Option 2 — Tailscale direct (sender on tailnet)

If the webhook sender is a device you control that already has Tailscale
installed (e.g. an iPhone running iOS Shortcuts), you can skip a public
tunnel entirely. All traffic between Tailscale nodes is WireGuard-encrypted,
so plain HTTP over the tailnet is safe.

1. Ensure both the Unraid host and the sending device are on the same tailnet.
2. Set `bind = "0.0.0.0"` in `config.toml`.
3. Use the Unraid node's **Tailscale IP** (e.g. `100.x.x.x`) as the webhook
   host:
   ```
   http://100.x.x.x:32800/webhook/<integration>?secret=<your-secret>
   ```

No router changes, no TLS certificates, no tunnel quota.

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

- **Router port forwarding** — Cloudflare Tunnel handles inbound connections
  without any changes to your router or firewall.
- **A custom domain** — Cloudflare provides a free `*.trycloudflare.com`
  hostname, or you can use your own.
- **Certificate management** — Cloudflare provisions and renews TLS
  certificates automatically.
