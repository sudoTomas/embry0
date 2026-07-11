# Security Policy

embry0 orchestrates autonomous coding agents inside Docker-in-Docker sandboxes and brokers credentials to them through authenticated proxies. Security reports are taken seriously — especially anything touching the sandbox boundary.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use [GitHub private vulnerability reporting](../../security/advisories/new) ("Report a vulnerability" under the Security tab). You will get an acknowledgement, and a fix or mitigation plan will be coordinated with you before any public disclosure.

## Scope — what we care most about

- **Sandbox escape**: agent code reaching the orchestrator, the Docker host, or other sandboxes.
- **Credential exposure**: any path that lets sandboxed code read `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`/OAuth tokens, or other proxy-held secrets directly (the design goal is that these never enter the sandbox environment).
- **Proxy auth bypass**: reaching git-proxy / github-proxy / auth-proxy endpoints without a valid per-sandbox enrollment token, or abusing `/admin/enroll` without `PROXY_ADMIN_TOKEN`.
- **API auth bypass**: unauthenticated access to the orchestrator API or webhook endpoints outside the documented dev modes.
- **Secret handling**: flaws in the Fernet-encrypted per-repo environment variable store.

## Out of scope

- Deployments with `AUTH_DEV_MODE=true` or `WEBHOOK_DEV_MODE=true` — these are documented, loudly-logged development bypasses and must never be used in production.
- Denial of service against your own self-hosted instance.

## Supported versions

Only the latest release / `main` branch receives security fixes.
