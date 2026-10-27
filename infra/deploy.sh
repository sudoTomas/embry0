#!/usr/bin/env bash
# Deploy embry0 on the host that runs it, and leave a deploy marker in Loki.
#
#   infra/deploy.sh                      # orchestrator (+ mcp) and frontend
#   infra/deploy.sh orchestrator         # only what changed
#   infra/deploy.sh marker <sha> <svc>…  # only the marker, after a deploy done by hand
#
# Steps: refuse unless the checkout is clean and on main, build the images on
# this host, recreate the containers (`mcp` runs the orchestrator image, so it
# is recreated with the orchestrator — a rebuild that leaves it out ships a
# stale MCP server), wait for /health, then push one line to Loki:
#
#   deployed <sha>: orchestrator frontend
#
# under {job="embry0-deploy"}. That line is what lets embry0's own log watcher
# walk a self case past `merged`: DeployLedgerSync reads the stack's
# `deploy_logql`, and the self stack's override selects this job label. The
# format is embry0/services/healing/deploys.py's `_MARKER_RE`, pinned by
# tests/unit/infra/test_deploy_marker.py. A Loki outage never fails a deploy
# that already rolled.
#
# Environment:
#   EMBRY0_DEPLOY_LOKI_URL  where the marker goes. Defaults to WATCHER_LOKI_URL
#                           from the root .env (the watcher's own Loki); empty =
#                           no marker, said out loud.
#   EMBRY0_DEPLOY_DRY_RUN=1 print the marker line instead of pushing it.
#   EMBRY0_DEPLOY_FORCE=1   deploy past the preflight refusals: a running job,
#                           a running-jobs check that got no 200, a dirty
#                           checkout. The recreate interrupts a running job:
#                           the orchestrator drains, and the job resumes from
#                           its last checkpoint when the new one starts,
#                           re-running the node it was in (#307).
#
# Only the services named are recreated (--no-deps). dind is never one of
# them: restarting it kills every sandbox, so it is recreated by hand, by name.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"

env_value() {  # env_value KEY — a value from the root .env, without sourcing it; empty when it has none
  [ -f "$root/.env" ] || return 0  # a failing sed would end the script silently under pipefail (#591)
  sed -n "s/^$1=//p" "$root/.env" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

marker_line() {  # marker_line SHA SERVICE... — the one line DeployLedgerSync parses
  local sha="$1"; shift
  echo "deployed $sha: $*"
}

push_deploy_marker() {  # push_deploy_marker SHA SERVICE...
  local line loki body
  line="$(marker_line "$@")"
  if [ "${EMBRY0_DEPLOY_DRY_RUN-}" = "1" ]; then
    echo "$line"
    return 0
  fi
  loki="${EMBRY0_DEPLOY_LOKI_URL-$(env_value WATCHER_LOKI_URL)}"
  if [ -z "$loki" ]; then
    echo "deploy: no Loki URL (EMBRY0_DEPLOY_LOKI_URL / WATCHER_LOKI_URL), marker not pushed: $line" >&2
    return 0
  fi
  body="$(printf '{"streams":[{"stream":{"job":"embry0-deploy"},"values":[["%s","%s"]]}]}' "$(date +%s%N)" "$line")"
  if curl -fsS -m 5 -H 'Content-Type: application/json' -X POST --data "$body" "${loki%/}/loki/api/v1/push" >/dev/null; then
    echo ">> marker pushed to $loki: $line"
  else
    echo "deploy: could not push the deploy marker to $loki (deploy itself succeeded): $line" >&2
  fi
}

if [ "${1-}" = "marker" ]; then
  shift
  [ $# -ge 2 ] || { echo "usage: infra/deploy.sh marker <sha> <service>..." >&2; exit 2; }
  push_deploy_marker "$@"
  exit 0
fi

services=("$@")
[ ${#services[@]} -gt 0 ] || services=(orchestrator frontend)
for svc in "${services[@]}"; do
  case "$svc" in
    orchestrator|frontend) ;;
    *) echo "deploy: unknown service '$svc' (orchestrator, frontend)" >&2; exit 2 ;;
  esac
done

branch="$(git -C "$root" rev-parse --abbrev-ref HEAD)"
if [ "$branch" != "main" ]; then
  echo "deploy: $root is on '$branch', not main — the deployed checkout must stay on main" >&2
  exit 1
fi
sha="$(git -C "$root" rev-parse --short=12 HEAD)"

# The images are built from the working tree, so an uncommitted edit (or an
# untracked file) would ship under the committed sha's name.
dirty="$(git -C "$root" status --porcelain)"
if [ -n "$dirty" ] && [ "${EMBRY0_DEPLOY_FORCE-}" != "1" ]; then
  echo "deploy: $root has uncommitted changes, which a build would ship as $sha:" >&2
  printf '%s\n' "$dirty" | head -n 20 >&2 || true  # a long list closes the pipe early
  echo "deploy: the deployed checkout moves only by git pull --ff-only — clean it, or EMBRY0_DEPLOY_FORCE=1" >&2
  exit 1
fi

# Compose interpolates infra/.env and loads the root .env into the orchestrator
# (ADR 0005); a deploy without either cannot work, so say which one is missing.
for env_file in "$here/.env:infra/.env.example" "$root/.env:.env.example"; do
  if [ ! -f "${env_file%%:*}" ]; then
    echo "deploy: ${env_file%%:*} is missing — copy ${env_file#*:} and fill it in (CONTRIBUTING.md)" >&2
    exit 1
  fi
done

port="$(sed -n 's/^PROD_PORT=//p' "$here/.env" | tail -n 1)"
port="${port:-8200}"
api_key="$(env_value API_KEY)"
if [ "${EMBRY0_DEPLOY_FORCE-}" != "1" ] && [ -n "$api_key" ]; then
  # Only a 200 answers the question: a 401 or no answer at all says nothing
  # about what is running, so it refuses instead of passing. The scout's jobs
  # do not count: one is always out, read-only, and resumes after the recreate.
  jobs_url="http://localhost:$port/api/v1/jobs?status=running&limit=1&exclude_dispatched_by=embry0:scout"
  reply="$(curl -sS -m 5 -w '\n%{http_code}' -H "Authorization: Bearer $api_key" "$jobs_url" 2>/dev/null)" || true
  code="${reply##*$'\n'}"
  if [ "$code" != "200" ]; then
    echo "deploy: the running-jobs check got HTTP ${code:-000} from $jobs_url, so it cannot tell whether a job is running — fix API_KEY or the stack, or EMBRY0_DEPLOY_FORCE=1" >&2
    exit 1
  fi
  if printf '%s' "${reply%$'\n'*}" | grep -q '"job_id"'; then
    echo "deploy: a job is running; a recreate interrupts it and it resumes after, re-running its current step — wait, or EMBRY0_DEPLOY_FORCE=1" >&2
    exit 1
  fi
fi

recreate=()
for svc in "${services[@]}"; do
  recreate+=("$svc")
  [ "$svc" = "orchestrator" ] && recreate+=(mcp)
done

# From infra/, with no -f: compose then reads infra/.env, including a
# COMPOSE_FILE that adds an overlay (docker-compose.rootless.yml), exactly as
# the systemd unit does. Without COMPOSE_FILE it is docker-compose.yml alone.
compose() { (cd "$here" && docker compose "$@"); }

echo ">> building ${services[*]} at $sha"
compose build "${services[@]}"
echo ">> recreating ${recreate[*]}"
compose up -d --force-recreate --no-deps "${recreate[@]}"

# nginx looks the orchestrator's name up once, when it loads its config: the
# LAN and public vhosts proxy /api, /ws, /webhook and /health to
# http://orchestrator:8000 without a resolver. A recreated orchestrator can
# come back on another address, so when the frontend was not recreated with
# it, reload nginx to look the name up again.
if [[ " ${services[*]} " == *" orchestrator "* && " ${services[*]} " != *" frontend "* ]]; then
  echo ">> reloading nginx, which still holds the old orchestrator's address"
  if ! compose exec -T frontend nginx -s reload; then
    echo "deploy: could not reload nginx in the frontend container, so it may still proxy to the old orchestrator — run: infra/deploy.sh frontend" >&2
    exit 1
  fi
fi

echo ">> waiting for http://localhost:$port/health"
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://localhost:$port/health" >/dev/null 2>&1; then
    echo ">> healthy"
    curl -fsS -m 5 "http://localhost:$port/api/v1/health/ready" 2>/dev/null && echo
    echo ">> deployed $sha: ${services[*]}"
    push_deploy_marker "$sha" "${services[@]}"
    exit 0
  fi
  sleep 3
done
echo "deploy: $sha did not become healthy on :$port within 3 minutes — no marker pushed" >&2
exit 1
