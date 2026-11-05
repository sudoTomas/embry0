---
name: install
description: Stand up a new embry0 deployment on this machine with Docker (clone, a .env with generated secrets, build and start the stack), then mint an admin API key and connect this plugin to it. Use when the person wants to install, deploy or run their own embry0 and has no deployment yet.
---

# Stand up embry0 on this machine

This is the quick start in the repository's `README.md`, done from the chat. Every secret is written into a file by a command and never shown: do not print `.env`, a key or a token, and never ask the person to type one here, because the chat transcript keeps it.

Show each command before you run it, and stop at the first step that fails.

## 1. Where

Ask the person:

- The directory for the checkout (default `~/embry0`).
- The repository to clone (default `https://github.com/sudoTomas/embry0.git`, or the fork they name).

The deployment runs on the machine this session runs on. For another host, the person starts Claude Code there and runs this skill again.

## 2. What the machine needs

Check and report each line; on a gap, stop and say what is missing. Do not install system packages without the person's word.

```bash
uname -sm                                   # Linux x86_64
docker info --format '{{.ServerVersion}}'   # Docker answers without sudo
docker compose version                      # the Compose plugin
python3 --version                           # 3.12 or newer
python3 -c 'import venv, ensurepip' && echo "venv: ok"
claude --version                            # Claude Code 2.1.285 or newer
free -g | awk '/^Mem:/{print $2 " GB memory"}'
df -h --output=avail "$(docker info --format '{{.DockerRootDir}}')" | tail -1   # free disk where Docker writes
ss -ltn 'sport = :8200' | tail -n +2        # nothing listed: the dashboard port is free
```

- Stop unless `uname -sm` prints `Linux x86_64`: the quick start supports x86_64 Linux hosts only.
- Python must be 3.12 or newer, with `venv` and `ensurepip` (on Debian and Ubuntu, the `python3-venv` package).
- Claude Code must be 2.1.285 or newer: step 6 hands the key to the plugin with `claude plugin configure`. `claude update` updates it.
- A first install, with its images and build cache, takes about 20 GB of disk, on the filesystem of Docker's root directory.
- The dashboard port is `PROD_PORT` in `.env`, 8200 unless the person picks another. Check the port they pick; step 4 writes it.

The stack's memory limits total about 24 GB and each job's sandbox can take up to 8 GB more. A smaller machine starts, but jobs can run out of memory: say so and let the person decide.

## 3. Clone

```bash
git clone <repository> <directory>
```

If the directory already holds an embry0 checkout, use it as it is; pull only on the person's word.

Then check that the stack's pinned network range is free on this machine. The dashboard network has a fixed subnet (the orchestrator trusts the dashboard's fixed addresses in it), and Docker refuses to create it over another network's range, only after the long build:

```bash
cd <directory> && python3 - <<'PY'
import ipaddress, json, re, subprocess
wanted = [ipaddress.ip_network(s) for s in re.findall(r"subnet: ([0-9a-fA-F.:/]+)", open("infra/docker-compose.yml").read())]
ids = subprocess.run(["docker", "network", "ls", "-q"], capture_output=True, text=True, check=True).stdout.split()
nets = json.loads(subprocess.run(["docker", "network", "inspect", *ids], capture_output=True, text=True, check=True).stdout)
clashes = [f"{want} overlaps {cfg['Subnet']} (network {net['Name']})"
           for net in nets if not net["Name"].startswith("embry0_")
           for cfg in (net.get("IPAM") or {}).get("Config") or [] if cfg.get("Subnet")
           for want in wanted
           if want.version == ipaddress.ip_network(cfg["Subnet"]).version and want.overlaps(ipaddress.ip_network(cfg["Subnet"]))]
print("\n".join(clashes) or f"free: {', '.join(map(str, wanted))}")
PY
```

On an overlap, stop and show it. The person either removes the other network, if nothing uses it, or moves the stack's range: every `172.29.0.` in `infra/docker-compose.yml` (the subnet, two fixed addresses and the `FORWARDED_ALLOW_IPS` default) and in `infra/docker-compose.rootless.yml` changes together to a free range, as a local edit of this checkout.

## 4. Write `.env`

From the checkout. The script copies `.env.example`, writes a fresh random value into each of the six secrets (the webhook secret too, which the setup's webhook step needs), empties the example token lines (credentials are added later, on the dashboard), writes the dashboard port if the person picked one other than 8200, and prints only the names it set. Leave out `<port>` to keep 8200:

```bash
cd <directory> && python3 - <port> <<'PY'
import os, re, secrets, shutil, sys
port = sys.argv[1] if len(sys.argv) > 1 else None
if port is not None and not re.fullmatch(r"[0-9]{2,5}", port):
    raise SystemExit(f"{port!r} is not a port number")
if os.path.exists(".env"):
    raise SystemExit(".env exists: keep it, or move it away first")
shutil.copyfile(".env.example", ".env")
os.chmod(".env", 0o600)
text = open(".env").read()
generated = ["API_KEY", "PROXY_ADMIN_TOKEN", "ENVIRONMENT_SECRET_KEY", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD",
             "GITHUB_WEBHOOK_SECRET"]
cleared = ["GITHUB_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]
for key in generated:
    text, n = re.subn(rf"(?m)^{key}=.*$", f"{key}={secrets.token_hex(32)}", text)
    if n != 1:
        raise SystemExit(f"{key}: expected one line in .env.example, found {n}")
for key in cleared:
    text = re.sub(rf"(?m)^{key}=.*$", f"{key}=", text)
if port is not None:
    text, n = re.subn(r"(?m)^PROD_PORT=.*$", f"PROD_PORT={port}", text)
    if n != 1:
        raise SystemExit(f"PROD_PORT: expected one line in .env.example, found {n}")
open(".env", "w").write(text)
print("set:", ", ".join(generated), "| emptied:", ", ".join(cleared), "| PROD_PORT:", port or "8200")
PY
```

If `.env` exists, ask whether to keep it (then go to step 5) or move it aside.

## 5. Build and start

```bash
cd <directory> && python3 -m venv .venv && .venv/bin/pip install -q -e .
cd <directory> && .venv/bin/embry0 start
```

`embry0 start` builds every image the first time: run it in the background and follow its output. When it has exited 0, wait for the stack to be ready. The script reads the port from `PROD_PORT` in `.env` and asks the ready check until it answers `"status": "ok"`. A stack whose database is not connected answers `degraded`, which is not ready:

```bash
cd <directory> && python3 - <<'PY'
import json, re, time, urllib.request
from pathlib import Path

def read_env(path):
    """KEY=value lines as compose reads them: an optional `export `, quotes, a ` #` comment after an unquoted value."""
    env = {}
    for line in Path(path).read_text().splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not match:
            continue
        value = match.group(2).strip()
        if value[:1] in ("'", '"') and value[0] in value[1:]:
            value = value[1:value.index(value[0], 1)]
        else:
            value = re.sub(r"\s+#.*$", "", value)
        env[match.group(1)] = value
    return env


def port_of(env):
    port = (env.get("PROD_PORT") or "8200").rsplit(":", 1)[-1]
    if not port.isdigit():
        raise SystemExit(f"PROD_PORT in .env is not a port number: {port!r}")
    return port


url = f"http://localhost:{port_of(read_env('.env'))}/api/v1/health/ready"
last = "no answer"
for attempt in range(60):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            answer = json.load(response)
        if answer.get("status") == "ok":
            print(f"ready: {url}")
            raise SystemExit(0)
        last = json.dumps(answer)
    except (OSError, ValueError) as error:
        last = str(error)
    time.sleep(5)
raise SystemExit(f"not ready at {url}: {last}")
PY
```

On "not ready", show the answer, then read `docker compose -f infra/docker-compose.yml ps` and the orchestrator's logs with the person.

## 6. Connect this plugin

Mint an admin key with the deployment's master key, and hand it to the plugin's options from the script, so no key is shown. `<plugin>` is this plugin's id as `claude plugin list` shows it (`embry0@embry0` when installed from the embry0 marketplace), and `<directory>` is the checkout.

Run it from this session's working directory, not from the checkout: a plugin installed for one project is found only from that project. The script:

- first proves that `claude plugin configure <plugin>` works, so it never mints a key that it cannot hand over;
- names the key after this machine (`claude-code-<host>`);
- revokes the new key if the options do not take it, for any reason;
- once the options carry the new key, revokes this machine's earlier keys of that name, which nobody holds any more.

```bash
python3 - <plugin> <directory> <<'PY'
import json, re, shutil, socket, subprocess, sys, urllib.error, urllib.request
from pathlib import Path

def read_env(path):
    """KEY=value lines as compose reads them: an optional `export `, quotes, a ` #` comment after an unquoted value."""
    env = {}
    for line in Path(path).read_text().splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if not match:
            continue
        value = match.group(2).strip()
        if value[:1] in ("'", '"') and value[0] in value[1:]:
            value = value[1:value.index(value[0], 1)]
        else:
            value = re.sub(r"\s+#.*$", "", value)
        env[match.group(1)] = value
    return env


def port_of(env):
    port = (env.get("PROD_PORT") or "8200").rsplit(":", 1)[-1]
    if not port.isdigit():
        raise SystemExit(f"PROD_PORT in .env is not a port number: {port!r}")
    return port


plugin, checkout = sys.argv[1], Path(sys.argv[2]).expanduser()
env = read_env(checkout / ".env")
url = f"http://localhost:{port_of(env)}"
name = "claude-code-" + re.sub(r"[^A-Za-z0-9.-]", "-", socket.gethostname())[:60]
claude = shutil.which("claude")
if claude is None:
    raise SystemExit("claude is not on PATH here, so no key was minted: run this where `claude` runs")
check = subprocess.run([claude, "plugin", "configure", plugin], capture_output=True, text=True, timeout=60)
if check.returncode != 0:
    raise SystemExit(f"`claude plugin configure {plugin}` failed, so no key was minted: {check.stderr.strip()}")
headers = {"Authorization": f"Bearer {env['API_KEY']}", "Content-Type": "application/json",
           "X-Requested-With": "XMLHttpRequest"}


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{url}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path}: HTTP {error.code} {error.read().decode(errors='replace')[:300]}")


minted = call("POST", "/api/v1/apikeys", {"name": name, "scope": "admin", "expires_days": 365})
try:
    save = subprocess.run([claude, "plugin", "configure", plugin, "--values-stdin"], capture_output=True, text=True,
                          input=json.dumps({"url": url, "api_key": minted["key"]}), timeout=60)
except BaseException:
    call("DELETE", f"/api/v1/apikeys/{minted['id']}")
    raise
if save.returncode != 0:
    call("DELETE", f"/api/v1/apikeys/{minted['id']}")
    raise SystemExit(f"the options did not take the key, so key {minted['id']} was revoked: {save.stderr.strip()}")
earlier = [key["id"] for key in call("GET", "/api/v1/apikeys")["keys"]
           if key["name"] == name and key["id"] != minted["id"] and not key.get("revoked_at")]
for key_id in earlier:
    call("DELETE", f"/api/v1/apikeys/{key_id}")
print(f"saved {url} and the admin key {minted['id']} ({name}) in the options of {plugin}"
      + (f"; revoked this machine's earlier keys: {', '.join(earlier)}" if earlier else ""))
PY
```

The key expires in a year; revoke it on the dashboard when it is no longer needed. Ask the person to restart Claude Code (a value saved from the command line applies on restart), then use the `connect` skill of this plugin to check the server.

## 7. Next

- The dashboard is at `http://localhost:<port>`. It signs in with the master key, `API_KEY` in `.env`: the person reads it in their own terminal, not here.
- The deployment still needs a Claude account and a forge token, added on the dashboard (Infra, then Credentials). The `setup` skill of this plugin walks through that and the rest: an organization, its repositories, webhooks, testing and spend limits.
- To reach the deployment from other machines or from GitHub webhooks, see `docs/ops/deploy.md` and `docs/webhooks.md` in the checkout.
