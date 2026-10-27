# Third-party notices

embry0's own source code is under the MIT licence ([LICENSE](LICENSE)). It
depends on software under other licences, and the container images built from
this repository contain more. This file lists what an operator should know
about them.

It is a summary, not legal advice. Each component's own licence and terms
govern it. The exact versions are in `uv.lock`, `frontend/package-lock.json`,
the Dockerfiles and `infra/docker-compose.yml`.

## No prebuilt images

The project does not publish container images. Build them yourself from the
Dockerfiles in `infra/` and `frontend/`, as the README's Quick start does.
There are two reasons:

- **The Claude Code CLI is proprietary.** The `claude-agent-sdk` Python package
  is MIT, but its wheel bundles the Claude Code CLI binary
  (`claude_agent_sdk/_bundled/claude`). That binary is "© Anthropic PBC. All
  rights reserved" and its use is subject to
  [Anthropic's legal agreements](https://code.claude.com/docs/en/legal-and-compliance).
  Installing the SDK puts the binary into the orchestrator, sandbox and QA
  sandbox images. When you build them, you download it from PyPI yourself,
  under Anthropic's terms. A published image would redistribute it.
- **Chrome for Testing's binary terms are unclear.** The QA sandbox image
  downloads Chrome for Testing (through `vibium install`) and Playwright's
  Chromium build. Upstream does not clearly state the terms for redistributing
  these binaries.

If you redistribute images you have built, you take on the obligations of
everything in them, including the components listed below.

## Python dependencies

Nearly all of them are under permissive licences: MIT, BSD, Apache-2.0, ISC,
PSF-2.0 or MIT-0. These are the exceptions:

| Package | Licence | Used | Notes |
|---|---|---|---|
| `psycopg`, `psycopg-binary`, `psycopg-pool` | LGPL-3.0-only | at runtime | Imported unmodified, as separate modules that can be replaced. `psycopg-binary` bundles libpq (PostgreSQL License) and OpenSSL (Apache-2.0). |
| `certifi` | MPL-2.0 | at runtime | Used unmodified. |
| `orjson` | MPL-2.0 AND (Apache-2.0 OR MIT) | at runtime, through LangGraph | Used unmodified. |
| `pathspec` | MPL-2.0 | in development only, through mypy | Not part of a deployment. |
| `claude-agent-sdk` | MIT | at runtime | Its wheel bundles the proprietary Claude Code CLI. See above. |

## Frontend

- The runtime dependencies bundled into the dashboard are under MIT, ISC,
  Apache-2.0 or BSD-3-Clause. Icons come from `lucide-react` (ISC).
- `lightningcss` (MPL-2.0) is a build-time dependency of Tailwind CSS and is not
  part of the built bundle.
- One font is redistributed: D-DIN, by Datto Inc., under the SIL Open Font
  License 1.1, in `frontend/public/fonts/d-din/` with its licence (`OFL.txt`).
  The dashboard serves it unmodified.
- The dashboard loads Roboto Mono and Pixelify Sans from Google Fonts at runtime
  (SIL Open Font License 1.1), so each dashboard load makes a request to Google.

## Software in the images you build

| Component | Images | Licence |
|---|---|---|
| Python, from the official `python` slim (Debian bookworm) base image | orchestrator, sandbox images, proxy | PSF-2.0 |
| Distribution packages: from Debian (git, curl, the GTK, NSS and X11 libraries, fonts and others) and, in the frontend image, from Alpine | every image | Various, including GPL and LGPL. Debian's sources are at [sources.debian.org](https://sources.debian.org). |
| Node.js from NodeSource, with npm | orchestrator, sandbox images | MIT (Node.js), Artistic-2.0 (npm) |
| Docker CLI and the Compose plugin | orchestrator, QA sandbox | Apache-2.0 |
| Eclipse Temurin JDK 21 | `dev-jvm-python` sandbox, QA sandbox | GPL-2.0 with the Classpath Exception |
| Poetry | `dev-python` and `dev-jvm-python` sandboxes | MIT |
| Playwright, `@playwright/mcp`, vibium | QA sandbox | Apache-2.0 |
| Chrome for Testing, Playwright's Chromium | QA sandbox | See [No prebuilt images](#no-prebuilt-images). |
| The Claude Code CLI, in `claude-agent-sdk` | orchestrator, sandbox images | Proprietary. See [No prebuilt images](#no-prebuilt-images). |
| nginx, from the official `nginx` Alpine base image | frontend | BSD-2-Clause |

## Services the compose stack runs

The stack runs these from their publishers' images, unmodified and as separate
containers. embry0's code does not include or link them.

| Service | Image | Licence |
|---|---|---|
| Silo, the community fork of MinIO, for QA artifacts | `docker.io/pgsty/silo` | AGPL-3.0-or-later, as MinIO is. embry0 reaches it over the S3 API, as a separate network service, unmodified. An operator who modifies it and lets others use it over a network must offer them its source (AGPL section 13). |
| PostgreSQL | `postgres` | PostgreSQL License |
| Postgres backups | `prodrigestivill/postgres-backup-local` | MIT |
| Docker-in-Docker and the Docker CLI | `docker:dind`, `docker:cli` | Apache-2.0 |
| Image registry | `registry` | Apache-2.0 |
| `htpasswd`, to generate the registry login | `httpd` | Apache-2.0 |
| Cloudflare Tunnel | `cloudflare/cloudflared` | Apache-2.0 |
