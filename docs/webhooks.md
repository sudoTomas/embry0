# Webhook Setup

embry0 reacts to GitHub events (issues opened/labeled/edited/closed, issue comments, pull requests) via a single webhook endpoint at `POST /api/v1/webhook`. Because embry0 usually runs on a private network, you need a way to get GitHub's webhook POSTs into your instance. Two supported approaches:

| Approach | Use when | Signature verification |
|----------|----------|------------------------|
| **Cloudflare Tunnel** | Production / always-on demo / shared team instance | **Required** — real HMAC secret |
| **smee.io relay** | Local dev on a laptop / ephemeral testing | **Skipped** — WEBHOOK_DEV_MODE=true, no secret |

## Option A — Cloudflare Tunnel (production)

The compose stack ships a `cloudflared` service that runs in remote-managed mode. You create a tunnel once in the Cloudflare dashboard (or via their API), paste the token into `.env`, and bring up the container. Any other tunnel/reverse-proxy solution (ngrok, Tailscale Funnel, a plain reverse proxy on a VPS) works the same way — the only requirement is that HTTPS POSTs reach the frontend's `/api/v1/webhook` route.

**1. Create the tunnel** (one-time): in [Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → Networks → Tunnels → Create a tunnel, add a public hostname (e.g. `webhooks.example.com`) pointing at `http://frontend:80`, and copy the tunnel token. Restricting the tunnel to the `/api/v1/webhook` path (via the tunnel config or a Cloudflare Access policy) is strongly recommended — keep the dashboard and full API LAN-only.

**2. Paste the token into `.env`:**

```bash
echo "TUNNEL_TOKEN=<your-tunnel-token>" >> .env
echo "CLOUDFLARED_TUNNEL_TOKEN=<your-tunnel-token>" >> .env
```

(Both names are written because the upstream `cloudflare/cloudflared` image expects `TUNNEL_TOKEN`, while the `.env.example` documents `CLOUDFLARED_TUNNEL_TOKEN` for clarity. Either alone would work.)

**3. Bring up the tunnel container:**

```bash
cd infra
docker compose up -d cloudflared
sleep 8
docker logs embry0-cloudflared --tail 20 | grep 'Registered tunnel'
```

You should see at least one `Registered tunnel connection` log line. Webhooks posted to `https://webhooks.example.com/api/v1/webhook` now flow through the tunnel into `orchestrator:8000`.

**4. Configure GitHub** to send webhooks to your hostname (Settings → Webhooks → Payload URL = `https://webhooks.example.com/api/v1/webhook`, content type `application/json`, secret = the value of `GITHUB_WEBHOOK_SECRET` in `.env`).

**Tearing down:** `docker compose stop cloudflared`, then delete the tunnel and its DNS record in the Cloudflare dashboard.

## Option B — smee.io relay (local development)

For testing real GitHub events against a local embry0 instance on your laptop, with no public hostname needed. smee.io re-serializes the webhook body before forwarding, which invalidates GitHub's HMAC — so this flow uses `WEBHOOK_DEV_MODE=true` and no secret.

**1. Get a smee channel:** visit [https://smee.io](https://smee.io), click **Start a new channel**, and copy the channel URL (e.g. `https://smee.io/aBcDeF1234`).

**2. Start the relay** (Node 20+ required):

```bash
npx smee-client --url https://smee.io/aBcDeF1234 --target http://localhost:8200/api/v1/webhook
```

Leave this running in a terminal pane — it prints every forwarded event.

**3. Enable WEBHOOK_DEV_MODE** in `.env` and clear the webhook secret:

```
WEBHOOK_DEV_MODE=true
GITHUB_WEBHOOK_SECRET=
```

Rebuild the orchestrator so the new config is picked up:

```bash
cd infra && docker compose build orchestrator && docker compose up -d orchestrator --force-recreate
```

**4. Configure the GitHub webhook** — repo → Settings → Webhooks → Add webhook:

- **Payload URL:** your smee channel URL (e.g. `https://smee.io/aBcDeF1234`)
- **Content type:** `application/json`
- **Secret:** *(leave blank)*
- **Events:** Issues, Issue comments, Pull requests

**5. Verify:** trigger an event in the repo. You should see the event appear in the smee-client terminal AND in `docker logs -f embry0-orchestrator | grep webhook_received`.

> **Note:** smee caches recent events and replays them on reconnect, which can cause duplicate job triggers after restarting the relay. For demos or production, always use Cloudflare Tunnel with HMAC verification.

## Without webhooks

You can trigger jobs manually via the dashboard — open the Issues page, find your issue, and click **Send to Agent**. No webhook setup required.
