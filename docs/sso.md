# Public dashboard: Auth0 SSO + Pattern-B tunnel (RAV-1069)

The dashboard is published at the operator's own public origin
(`SSO_PUBLIC_ORIGIN`, e.g. `https://embry0.example.com`) behind a dedicated
Cloudflare tunnel, with two independent authentication methods:

1. **Auth0 SSO** (humans) — server-side OIDC against the operator's Auth0
   tenant (Universal Login, which can federate Microsoft SSO and the rest).
   The React frontend has **no Auth0 SDK**: login is a full-page redirect to
   `/api/v1/auth/login`; the backend exchanges the code and sets a signed
   `embry0_session` cookie (HttpOnly, Secure, SameSite=Lax, 12h).
2. **API key** (machines) — the `API_KEY` bearer works unchanged on every
   origin, LAN and public alike (MCP, CLI, and any other API client). It is
   also the break-glass path if SSO misbehaves.

## Roles — default-deny

Roles ride the claim named by `SSO_ROLES_CLAIM` (e.g.
`https://example.com/roles`), stamped by the tenant's post-login Action
**onto the ID token**. Unlike just-in-time provisioning, embry0 refuses any
authenticated identity without an embry0 role (it holds repo creds and can
push code):

| Auth0 role | embry0 tier | Grants |
|---|---|---|
| `embry0-admin` | admin | everything, incl. env-secret reveals, agent create/update/delete/reset, `PUT /config/integrations` |
| `embry0-operator` | operator | the rest of the dashboard/API |
| anything else / none | none | 403 access-denied screen |

Assign roles in Auth0 (Dashboard → User Management → Roles), or with
`infra/auth0/provision-roles.mjs --assign <role> <email>`. Role changes take
effect at the user's **next login** (sessions are stateless signed cookies).

## Backend surface

- `GET /api/v1/auth/check` — unauthenticated probe `{sso_enabled, authenticated, tier}`.
- `GET /api/v1/auth/login` → Auth0 authorize (PKCE; state in a signed cookie).
- `GET /api/v1/auth/callback` → code exchange, ID-token validation (JWKS), session cookie, relative redirect `/`.
- `GET /api/v1/auth/me`, `POST /api/v1/auth/logout` (returns the Auth0 `v2/logout` URL).
- `require_auth` accepts API-key bearer OR operator+ session; `require_admin`
  additionally gates the admin-tier routes. SSE streams and the jobs
  WebSocket accept the session cookie directly.
- Audit rows use the SSO email as actor. A request made with an API key is
  recorded under the key owner's email or `key:<name>`, one made with the
  master key under `embry0:master-key`, never under a client address.

Config: `AUTH0_ISSUER`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`,
`SESSION_SECRET`, `SSO_PUBLIC_ORIGIN` in the **root `.env`** — all-or-nothing
(partial set refuses startup). Fully unset = SSO off, LAN key-only deploy.

## Ingress

- One variable names the public hostname for the whole ingress path:
  **`DASHBOARD_PUBLIC_HOSTNAME`** in **`infra/.env`** (compose interpolation,
  not the root `.env`), set to the host part of `SSO_PUBLIC_ORIGIN`:
  `embry0.example.com`, with no scheme, port or path. The nginx public vhost,
  the dashboard tunnel's ingress and `provision.sh`'s CNAME are all rendered
  or checked from it by one script, `frontend/nginx/render-dashboard-public.sh`,
  so they cannot disagree. A value that is not one plain hostname (a URL, a
  port, a wildcard, `_`, several names) is refused everywhere.
- nginx serves that origin from a dedicated vhost, rendered when the frontend
  container starts. That vhost, and only that one:
  - hardcodes `X-Forwarded-Proto https` (TLS ends at the Cloudflare edge);
  - returns 404 for `/api/v1/webhook*`, `/api/v1/telegram/*` and
    `/api/v1/internal/*`, which have their own ingress (the webhooks tunnel)
    or sandbox-only auth;
  - sends `Strict-Transport-Security: max-age=31536000` (no
    `includeSubDomains`: the hostname may be an apex whose other subdomains
    are not the dashboard's) and a Content-Security-Policy.

  **Unset means no public vhost**: every Host, the public one included, gets
  the LAN vhost with none of the above, which is right for a LAN-only
  deployment. An invalid value stops the frontend container at start, with
  the reason in its log. Template: `frontend/nginx/`. Apply a change with
  `infra/deploy.sh frontend`. When upgrading from a release that hardcoded
  this vhost's hostname, set the variable **before** that deploy, or the
  public origin silently loses all of the above.
- The CSP, rendered for the hostname above:

  ```
  default-src 'self'; script-src 'self';
  style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
  font-src 'self' https://fonts.gstatic.com; img-src 'self' blob:;
  connect-src 'self' wss://<DASHBOARD_PUBLIC_HOSTNAME>; object-src 'none';
  base-uri 'self'; form-action 'self'; frame-ancestors 'none'
  ```

  It allows what the built dashboard loads: its own bundles, `/api`, the SSE
  streams and the job WebSocket; Google Fonts (linked from `index.html` and
  `@import`ed by `src/index.css`); blob: images (QA artifact thumbnails); and
  inline `<style>` elements, which the toast library injects. Scripts are
  same-origin only. Auth0 is a full-page navigation, which CSP does not
  govern. Loading anything from a new origin means adding it to the template,
  or the browser blocks it and logs a CSP violation in the console.
- The tunnel is Pattern B, separate from the remote-managed webhooks tunnel:
  `infra/cloudflared-dashboard/provision.sh` creates tunnel + proxied CNAME
  for `DASHBOARD_PUBLIC_HOSTNAME` (env or `infra/.env`; it refuses to run
  without a valid one) and writes `creds.json` (gitignored) +
  `CF_TUNNEL_ID`/`CF_RUN_USER` into `infra/.env` (`CF_API_TOKEN` lives there
  too, provision-only). Start with
  `docker compose --profile dashboard-tunnel up -d cloudflared-dashboard`.
- The tunnel's ingress is not a tracked file. The one-shot
  `cloudflared-dashboard-config` service renders
  `infra/cloudflared-dashboard/config.yml.template` into the
  `cloudflared-dashboard-config` volume: rule 0 sends the hostname to
  `http://frontend:80`, and everything else gets `http_status:404` at the
  tunnel. **It fails closed.** With the variable unset or invalid, the render
  exits non-zero, writes nothing (and removes any earlier render), and
  `cloudflared-dashboard`, which waits for it to succeed, never starts. The
  reason is in `docker logs embry0-cloudflared-dashboard-config`. There is no
  catch-all ingress, because with no public vhost a catch-all would reach
  nginx's LAN vhost. cloudflared reads its config only at start, and the
  hostname is in the tunnel's compose config, so after changing the variable
  `up -d cloudflared-dashboard` re-renders and recreates it.
- Static IPs on the pinned frontend network: nginx `.10`, tunnel `.11`;
  `FORWARDED_ALLOW_IPS` trusts exactly those two, so the Cloudflare-edge
  visitor IP reaches the rate limiter and audit trail.

## Tenant objects

A confidential client, which may be shared with the operator's other apps.
embry0's callback (`<SSO_PUBLIC_ORIGIN>/api/v1/auth/callback`), logout URLs
and web origin are registered **additively** via
`infra/auth0/register-embry0-callbacks.mjs` — additively so a shared client
keeps whatever the other apps registered.
Roles `embry0-operator`/`embry0-admin` created by `infra/auth0/provision-roles.mjs`.
Both scripts are idempotent and read Management credentials from outside the
repo; they never print secrets.

## Gotchas

- **Roles claim must be on the ID token**, under the exact namespace
  `SSO_ROLES_CLAIM` names. An access-token-only or app-specific claim
  silently yields `[]`, and default-deny then lands everyone on the
  access-denied screen with nothing in the log to say why. This is the
  classic tier-drift trap, and the reason `SSO_ROLES_CLAIM` is required
  rather than defaulted (#393).
- Rotating `SESSION_SECRET` invalidates all sessions (deliberate kill switch).
- The callback redirect is **relative** (`/`): session cookies are
  host-scoped; absolute redirects to another hostname lose them.
- Some TLDs (`.app`, `.dev`) are HSTS-preloaded: always use the https public
  URL, never a host-IP.
