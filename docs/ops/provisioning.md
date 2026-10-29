# Provisioning an embry0 tenant

Stand up a host from nothing. Commands below use placeholders: the guest is
`acme-embry0`, the hypervisor SSH host is `<hypervisor>`, the VM id is
`<vmid>`, a private forge (if any) is `gitlab.example.com`, and this repo
clones as `<owner>/embry0`. Substitute your own before running them. Several
steps are recorded in more detail than a checklist would need, because the
first deploy of this shape was built without a runbook.

Read `docs/ops/verification.md` for the pre-commit and CI gates; this document
covers standing up a host from nothing.

## 1. Host requirements

Proxmox VE 9.x. On PVE 9 the disk-import command is `qm disk import`; the older
`qm importdisk` name is gone.

Before starting, confirm the host has room and the VMID is free:

```bash
ssh <hypervisor> 'free -g | head -2; pvesm status | grep local-lvm; qm list; qm config <vmid>'
```

The last command should report that the configuration file does not exist. If it
prints a config, the VMID is taken — pick another rather than reusing it.

Storage is thin-provisioned on `local-lvm`, so a generously sized disk costs
nothing until it is used.

**PostgreSQL 15 or later.** The compose stack runs `postgres:16.6-alpine`. A
deployment that points `DATABASE_URL` at a server of its own needs PostgreSQL
15 or later: migration 133 keys the knowledge maps with unique indexes that
use `NULLS NOT DISTINCT`, which older servers refuse, and the orchestrator runs
its migrations before it starts.

**On memory.** This host runs its guests oversubscribed on maxima with balloon
floors keeping it safe. A new tenant VM should follow that pattern rather than
demanding a fixed allocation. See §9 for what ballooning looks like in practice.

### Sizing DinD and the job cap

Every job's containers run inside the one DinD daemon: each job's sandbox,
the sandboxes a QA run fans out to and the app containers they boot, plus the
credential proxies. Compose caps that daemon with `DIND_CPUS` and
`DIND_MEMORY` (`infra/.env`, defaults `4` and `8G`), and nothing else bounds
the sum. A sandbox's own limits come from its sandbox profile, not from
`.env`: `slim` 8g/4 CPUs, `dev-python` 6g/4, `dev-jvm-python` 8g/4, `qa-jvm`
12g/6, `qa-external` 8g/4. (The unused `SANDBOX_MEMORY` and `SANDBOX_CPUS`
settings were removed, #590.)

A container memory limit is a ceiling, not a reservation, so the profiles
oversubscribe DinD freely: with the defaults, one `qa-jvm` sandbox may grow
past all of `DIND_MEMORY`, where only the daemon's limit binds. When the jobs
running together need more than `DIND_MEMORY`, the kernel OOM-kills processes
inside DinD. A job then fails on whatever was killed (a build, Chromium, the
agent), not with a queueing message. CPU oversubscription only slows jobs
down; memory is the limit to size for.

1. **Give the host room for the rest of the stack first.** The host needs
   `ORCHESTRATOR_MEMORY` (default `4G`) plus `DIND_MEMORY`, plus Postgres,
   MinIO, the registry, nginx and the OS. Set `DIND_MEMORY` to most of what
   remains, and `DIND_CPUS` to the cores you can spare.
2. **Measure one job's peak.** Run a representative job of each kind your
   repositories use and watch it with
   `docker exec embry0-dind docker stats --no-stream`, repeated during builds,
   test runs and browser QA. Count a QA job as all of its concurrent sub-task
   sandboxes and app containers: one QA job holds one run slot, but runs up
   to `parallelism.max_concurrent_apps` apps at once (default 8,
   [qa.yaml](../qa-yaml-reference.md)). Lower that per repository if one QA
   job alone does not fit.
3. **Set the job cap.** `MAX_RUNNING_JOBS` (root `.env`, default `0` = no
   cap) is the number of jobs that run at once; the rest wait as `pending`,
   in the board's "queued" lane, and start when a slot frees. Take
   `DIND_MEMORY`, less what the proxies use (the same `docker stats` shows
   them), divide by the largest per-job peak you measured, and round down,
   to no less than 1. Without measurements, divide by the largest profile
   limit your jobs use: safe but pessimistic, and with the defaults it gives
   `1`.

The default stays `0` so that an upgrade does not start queueing jobs on a
deployment that has not sized for it; set it once you have.

Jobs waiting on an answer (`awaiting_input`, `paused`) hold no slot: their
agent has stopped and their sandbox idles. Anything an agent left running in
the sandbox, such as a build daemon, keeps its memory while the job waits;
`paused` sandboxes expire after `PAUSED_JOB_TTL_HOURS`. When the runtime
moves off DinD (#268), the same arithmetic sizes the container daemon's slice
limits.

## 2. Create the VM

**Or with OpenTofu.** [`infra/tofu/modules/proxmox-vm`](../../infra/tofu/modules/proxmox-vm/README.md)
creates the same VM through the Proxmox API in place of the `qm` commands
below. Its README covers the two files it expects on the hypervisor: the cloud
image as an `import` volume, and the cloud-init snippet. Keep your root
module, variables and state outside this repository
([`infra/tofu/README.md`](../../infra/tofu/README.md)), then continue from the
address poll below.

The manual steps follow. The cloud-init snippet lives in this repo at
`infra/cloud-init/embry0-user.yaml.example`. Copy it to the host first:

```bash
scp infra/cloud-init/embry0-user.yaml.example \
    <hypervisor>:/var/lib/vz/snippets/embry0-user.yaml
```

Then create, import, attach and start:

```bash
qm create <vmid> --name acme-embry0 --cores 8 --sockets 1 --cpu host \
  --memory 16384 --balloon 4096 \
  --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single \
  --ostype l26 --agent enabled=1 --onboot 1 --serial0 socket --vga std \
  --ipconfig0 ip=dhcp \
  --cicustom "user=local:snippets/embry0-user.yaml"

qm disk import <vmid> /var/lib/vz/template/iso/ubuntu-26.04-server-cloudimg-amd64.img local-lvm
```

**The imported disk arrives detached, as `unused0`.** Attach it explicitly —
read the name from `qm config <vmid> | grep unused` rather than assuming it:

```bash
qm set <vmid> --scsi0 local-lvm:vm-<vmid>-disk-0
qm disk resize <vmid> scsi0 150G
qm set <vmid> --ide2 local-lvm:cloudinit
qm set <vmid> --boot order=scsi0
qm start <vmid>
```

First boot installs packages, so the guest agent takes a few minutes to answer.
Poll for the address rather than guessing when it is ready:

```bash
until ssh <hypervisor> 'qm guest exec <vmid> -- ip -4 -o addr show dev eth0' >/dev/null 2>&1
do sleep 15; done
ssh <hypervisor> 'qm guest exec <vmid> -- ip -4 -o addr show dev eth0'
```

Verify the result from your workstation, substituting the address:

```bash
ssh embry0@<VM_IP> '
  hostnamectl --static          # acme-embry0
  docker --version              # Docker 29.x
  docker compose version        # v2.x
  id -nG embry0                 # includes sudo and docker
  systemctl is-active prometheus-node-exporter   # active
  grep gitlab.example.com /etc/hosts            # <forge-ip>
  cloud-init status             # status: done
  nproc; df -h / | tail -1
'
```

## 3. Trust a private CA

Needed when the forge presents a certificate signed by an internal authority.
Skip this section entirely for a public forge.

The certificate goes in **two** places, for two different reasons:

| Destination | Serves |
|---|---|
| `/usr/local/share/ca-certificates/` then `update-ca-certificates` | Ordinary `git` and `curl` on the host, so hand debugging works |
| `/etc/embry0/gitlab-ca.pem` | The file compose bind-mounts into the orchestrator |

Installing only the first leaves the orchestrator untrusting; only the second
leaves you unable to diagnose anything by hand.

### Get the CA, not the leaf

This is the easy mistake. The server presents only its leaf certificate, so the
CA cannot be scraped from the TLS handshake — fetch it from the forge host:

```bash
ssh <forge-host> 'cat /etc/gitlab/trusted-certs/ca.crt' > /tmp/internal-ca.crt
openssl x509 -in /tmp/internal-ca.crt -noout -subject -issuer -dates
```

A CA is **self-signed**: subject and issuer match, and its expiry is years out.
If subject and issuer differ, or the expiry is close, you have the leaf. Stop
and re-check the path.

### Install it

```bash
scp /tmp/internal-ca.crt embry0@<VM_IP>:/tmp/
ssh embry0@<VM_IP> '
  sudo install -m 0644 /tmp/internal-ca.crt /usr/local/share/ca-certificates/internal-ca.crt
  sudo update-ca-certificates            # expect "1 added"
  sudo mkdir -p /etc/embry0
  sudo cp /usr/local/share/ca-certificates/internal-ca.crt /etc/embry0/gitlab-ca.pem
  sudo chmod 0644 /etc/embry0/gitlab-ca.pem
  rm /tmp/internal-ca.crt
'
rm -f /tmp/internal-ca.crt
```

A copy rather than a symlink, because compose bind-mounts the path into a
container.

Verify with the same check the orchestrator's loader runs:

```bash
ssh embry0@<VM_IP> 'openssl x509 -in /etc/embry0/gitlab-ca.pem -noout -subject'
```

A file that fails here refuses the boot in §5.

### Renewals

The CA's own expiry is what matters for trust; a forge's leaf certificate will
rotate under it without anything here changing. If the CA is ever regenerated,
every clone fails at once and looks like an application bug — re-fetch the CA,
repeat this section, and restart the orchestrator.

## 4. Configure the two env files

### Get the source onto the host

This repo is private, so an HTTPS clone fails with `could not read Username`.
Use a **read-only deploy key** scoped to this one repository rather than a
personal access token, which would be broader than the host needs:

```bash
ssh embry0@<VM_IP> 'ssh-keygen -t ed25519 -N "" -C "<hostname> deploy key" -f ~/.ssh/id_ed25519 -q
  ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
  cat ~/.ssh/id_ed25519.pub'

# from your workstation, with gh authenticated
gh repo deploy-key add <pubkey-file> --repo <owner>/embry0 --title "<hostname> read-only"
```

Then clone over SSH and confirm the key cannot push:

```bash
ssh embry0@<VM_IP> 'git clone git@github.com:<owner>/embry0.git ~/embry0
  git -C ~/embry0 push --dry-run'     # must be refused
```

### Which file holds what

Per `docs/adr/0005-two-env-files.md`: the repo-root `.env` is the **container**
environment, `infra/.env` feeds **compose interpolation**. They are not
interchangeable.

| Root `.env` | `infra/.env` |
|---|---|
| `API_KEY`, `PROXY_ADMIN_TOKEN`, `ENVIRONMENT_SECRET_KEY` | `POSTGRES_*`, `MINIO_ROOT_*` |
| `GITLAB_URL`, `GITLAB_TOKEN`, `GITLAB_CA_BUNDLE` | `GITLAB_CA_BUNDLE_HOST` |
| `DEFAULT_FORGE`, `AUTH_DEV_MODE`, `IMAGE_REGISTRY` | `DIND_CPUS`, `DIND_MEMORY`, `TZ` |
| `CLAUDE_CODE_OAUTH_TOKEN` | `CLOUDFLARED_TUNNEL_TOKEN` |

Generate secrets straight into the file rather than through your shell history:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set both files to mode 0600.

### The CA pair

**Both must be set, and the failure modes are asymmetric:**

- `GITLAB_CA_BUNDLE_HOST` (in `infra/.env`) — the host path, bind-mounted read-only.
- `GITLAB_CA_BUNDLE` (in root `.env`) — the container path, `/etc/embry0/gitlab-ca.pem`.

Host-only is a **silent no-op** that fails later at clone time. Container-only
**refuses the boot**, because the mount defaults to `/dev/null` and the loader
reports the path contains no certificate. Verify both landed:

```bash
ssh embry0@<VM_IP> 'cd ~/embry0
  grep -c "^GITLAB_CA_BUNDLE=/etc/embry0/gitlab-ca.pem$" .env
  grep -c "^GITLAB_CA_BUNDLE_HOST=/etc/embry0/gitlab-ca.pem$" infra/.env'
```

### `DEFAULT_FORGE` is deliberately empty

On a single-forge tenant, leave `DEFAULT_FORGE=` empty. That makes an
unregistered repo fail loudly instead of being assumed to live on GitHub, and it
means every repository must be registered with its `forge` stated explicitly
(§8). Leaving it at the default `github` silently disables that protection.

### The webhook tunnel is opt-in

`cloudflared`, the webhook tunnel, runs only with the `webhook-tunnel` compose
profile, so a tenant with no inbound path sets nothing for it. A tenant with a
tunnel adds `COMPOSE_PROFILES=webhook-tunnel` and `CLOUDFLARED_TUNNEL_TOKEN` to
`infra/.env` (see `infra/.env.example` and `docs/webhooks.md`). Confirm the
default set:

```bash
ssh embry0@<VM_IP> 'cd ~/embry0/infra && docker compose config --services | sort'
```

`cloudflared` appears only when the profile is on.

## 5. Build images and start the stack

This section builds the default runtime, where sandboxes run in the `dind`
service. To run them on a dedicated rootless daemon instead, set that up first
(§10); it changes where the sandbox images are built and drops the registry.

### A fresh host needs TWO build passes

This is the step that most often goes wrong, because neither pass is enough
alone. `embry0 start` and `embry0 build` run both as one build.

```bash
cd ~/embry0/infra

# 1. The images profile: the sandbox and proxy images that jobs run inside.
docker compose --profile images build \
  sandbox-image-builder dev-python-image-builder dev-jvm-python-image-builder \
  qa-image-builder proxy-image-builder

# 2. The ordinary services: orchestrator and frontend.
docker compose build
```

Why both:

- `init-push-images` requires **all five** profile images on the host daemon and
  exits 1 otherwise. The orchestrator declares
  `depends_on: init-push-images: service_completed_successfully`, so a missing
  profile image means the orchestrator never starts at all.
- `orchestrator` and `frontend` are build-only, with no `image:` to pull. The
  `mcp` service then *reuses* the orchestrator's image by name
  (`image: embry0-orchestrator`), which resolves only because the compose
  project is named `embry0` at the top of the file. Skip this pass and `up`
  fails with `pull access denied for embry0-orchestrator`.

Confirm all seven before starting:

```bash
docker images --format "{{.Repository}}:{{.Tag}}" \
  | grep -E "^embry0-(sandbox|proxy|orchestrator|frontend)" | sort
```

### Start

The sandbox isolation chain needs the `br_netfilter` kernel module on the
host: without it iptables in DinD does not see traffic between containers on
one bridge, the orchestrator logs `sandbox_isolation_unavailable`, and it
refuses to create any sandbox. The example cloud-init loads it; on a host
provisioned some other way, load it now and at every boot:

```bash
sudo modprobe br_netfilter
echo br_netfilter | sudo tee /etc/modules-load.d/br_netfilter.conf
```

Then start the stack:

```bash
docker compose up -d
docker compose ps -a --format "{{.Service}}\t{{.State}}\t{{.Status}}"
```

Expect every long-running service `running (healthy)`, and the three one-shot
initialisers (`registry-auth-init`, `init-sandbox-networks`, `init-push-images`)
`exited (0)`. A non-zero exit from `init-push-images` means a profile image is
missing — go back to pass 1.

**A fresh start logs no errors.** The images Docker-in-Docker does not hold
yet are pulled from the in-stack registry, and the egress helper is built;
their absence is logged at debug, not as an error (#655). So an error at
startup is real: `proxy_image_missing_in_dind` or
`sandbox_image_unavailable_no_build_context` means the registry lacks that
image too (#622), and `sandbox_isolation_unavailable` means `br_netfilter` is
not loaded (above).

```bash
docker compose logs orchestrator 2>&1 | grep -c '"level": "error"'   # expect 0
docker exec embry0-dind iptables -L EMBRY0-SANDBOX-ISOLATION -n | head -3   # the chain exists
curl -sS localhost:8200/api/v1/health/ready                          # {"status": "ok"}
```

Migrations run automatically at orchestrator startup — there is no manual step.
Confirm the tail applied:

```bash
docker compose logs orchestrator 2>&1 | grep migration_applied | tail -2
```

### The registry login and the image pins

The in-stack registry refuses anonymous clients, and the orchestrator runs the
images the bootstrap pushed by digest, never by tag (#523). Neither needs a
setting:

1. `registry-auth-init` generates a random password on its first run, and keeps
   it on every later one, in the `registry-auth` volume, next to the bcrypt
   `htpasswd` the registry reads. Only the registry and its three clients mount
   that volume.
2. The registry counts as healthy only while it answers an anonymous `/v2/` with
   `401`, so nothing downstream starts on an open registry.
3. `init-push-images` logs in, pushes, and writes the digest each push reported
   to `pins` in the `image-pins` volume.
4. The orchestrator writes the login into its own docker CLI config and installs
   the pins. It **refuses to start** when either is missing, or when the pins
   lack the proxy or a builtin sandbox image.

Verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5001/v2/       # 401
docker logs embry0-init-push-images | grep -E '^(pushed|pinned) '          # a digest per image
docker compose logs orchestrator 2>&1 | grep -E 'registry_login_configured|image_pins_installed'
docker exec embry0-dind docker ps --format '{{.Image}}' | sort -u        # …:latest@sha256:…
```

What follows from it:

- **The pins are read at startup.** The rebuild recipes already recreate
  `init-push-images` *and* the orchestrator; re-running `init-push-images` alone
  leaves the orchestrator on the previous digests until it restarts.
- **Anything else that talks to the registry needs the login,** including the
  docker CLI inside the `dind` container: `docker exec embry0-dind docker pull
  registry:5000/…` is refused, while running an image DinD already holds still
  works.
- **Pre-built QA images** baked from now on are recorded by digest. One baked
  before #523 still runs by tag until it is rebuilt; the first QA run that
  selects it logs `qa_prebuilt_image_unpinned`. Rebuild it with
  `embry0 build-qa-image --repo <repo> --branch <branch> --force`.
- **Rotate the password** by deleting it and recreating its users:

  ```bash
  docker run --rm -v embry0_registry-auth:/auth alpine rm /auth/password
  docker compose up -d --force-recreate registry-auth-init registry init-push-images orchestrator
  ```

- **Limits.** The login crosses the backend network in clear text (the registry
  speaks plain HTTP there), so it stops casual pushes rather than a determined
  attacker on that network; the pins are what keep a replaced image from
  running. An image you push by hand and name in a custom sandbox profile is not
  pinned and runs by tag.

### Where the API actually lives

The orchestrator does **not** publish a host port. Only `frontend` does, on
**8200**, and its nginx proxies `/api` to the orchestrator. So the base URL is
`http://<VM_IP>:8200/api/v1/...`, not port 8000.

### Prove the CA refusal works

Worth doing once per host, because the silent-failure mode is the expensive one:

```bash
cd ~/embry0 && sed -i "s#^GITLAB_CA_BUNDLE=.*#GITLAB_CA_BUNDLE=/etc/embry0/absent.pem#" .env
cd infra && docker compose up -d --force-recreate orchestrator; sleep 12
docker compose ps -a | grep orchestrator          # Restarting
docker compose logs --tail 12 orchestrator | grep CABundleError
```

Expect `CABundleError: GITLAB_CA_BUNDLE '/etc/embry0/absent.pem' could not be
read`. Then restore the real path, recreate, and confirm:

```bash
docker logs embry0-orchestrator | grep gitlab_ca_bundle_loaded | tail -1
```

The event logs the path and a byte count and never the certificate body.

## 6. Survive a reboot

Without this the stack has to be started by hand after every reboot.

```bash
scp infra/systemd/embry0.service embry0@<VM_IP>:/tmp/
ssh embry0@<VM_IP> '
  sudo install -m 0644 /tmp/embry0.service /etc/systemd/system/embry0.service
  sudo systemctl daemon-reload
  sudo systemctl enable embry0.service
  rm /tmp/embry0.service
'
```

The unit is `Type=oneshot` with `RemainAfterExit=yes`, running
`docker compose up -d` as the `embry0` user from `~/embry0/infra`, with
`TimeoutStartSec=0` so a slow first pull is not killed.

Then actually reboot and check — an untested unit is not a working unit:

```bash
ssh embry0@<VM_IP> 'sudo systemctl reboot'
# wait for SSH to answer again, then:
ssh embry0@<VM_IP> 'uptime -s; systemctl is-active embry0.service
  cd ~/embry0/infra && docker compose ps --format "{{.Service}}\t{{.Status}}"'
```

The boot time should be recent, the unit `active`, and every container's uptime
should date from the reboot rather than from when you started it by hand.

### Do not wait on `systemctl is-system-running --wait`

It exits non-zero when the system is `degraded`, so a poll loop built on it
never finishes even though the machine is perfectly healthy. On these VMs
`openipmi.service` fails permanently, because a virtual machine has no IPMI
hardware, and that alone is enough to report `degraded`.

Poll for SSH answering a real command instead, and check failures explicitly:

```bash
ssh embry0@<VM_IP> 'systemctl list-units --state=failed --no-legend'
```

`openipmi.service` in that list is expected and harmless.

## 7. Verify with a job

*(filled in during Task 7, real-tenant run added in Task 10)*

## 8. Register a repository

*(filled in during Task 9)*

## 9. Troubleshooting

*(expanded during Task 11)*

### The VM reports far less memory than its configured maximum

Expected on an oversubscribed host, and not a fault. With `--memory 16384
--balloon 4096`, the guest is given somewhere between the two depending on its
own demand and the host's free memory, so an idle VM reports something near the
floor.

Check what the hypervisor is actually doing before changing anything:

```bash
ssh <hypervisor> 'qm status <vmid> --verbose | grep -iE "^mem|balloon|maxmem"'
```

`maxmem` is the ceiling, `balloon_min` the floor, `balloon` the current
allocation and `mem` what the guest is really using. A guest using 1.5 GiB while
holding 7 GiB with a 16 GiB ceiling is healthy.

Raise `--balloon` only with evidence that the balloon fails to inflate fast
enough under a real workload — image builds are the first genuine load. Raising
it speculatively takes guaranteed memory from every other guest.

## 10. A rootless sandbox daemon

By default every proxy and sandbox runs inside the `dind` compose service, a
**privileged** Docker-in-Docker container. `AGENT_RUNTIME=rootless` runs them on
a dedicated **rootless** Docker daemon on the same host instead (#268): dockerd
under rootlesskit, owned by an unprivileged account, in its own user and network
namespaces. A container escape then lands in that account, not in a privileged
container that is root on the host.

It is not the host's own Docker daemon. That one also runs Postgres, MinIO and
the orchestrator (and, on a shared host, unrelated production containers), and
its socket is root over all of them. The orchestrator refuses to start under
`rootless` unless the daemon it reaches reports itself rootless and able to
enforce memory, CPU and process limits.

Both runtimes keep every isolation control: the restricted network with no NAT,
the `EMBRY0-SANDBOX-ISOLATION` chain, the private-range egress drop,
the credential proxies and their per-sandbox enrollment, the profile isolation
checks and the per-sandbox limits.

### What changes

| Mechanism | Under `dind` | Under `rootless` |
|---|---|---|
| Who runs sandboxes | the privileged `dind` service | dockerd under rootlesskit, as `embry0-sandbox`, a systemd user service |
| How the orchestrator reaches it | TCP + TLS (`tcp://dind:2376`, client certs) | the unix socket `/run/embry0-sandbox/docker.sock`, mode 0660; the orchestrator joins its group. Not TCP: a listener would sit in the daemon's network namespace, which every sandbox can address at its bridge gateway |
| The collective ceiling | the `dind` container's cgroup (`DIND_CPUS`, `DIND_MEMORY`) | `embry0-sandboxes.slice` of the account's user manager (`SANDBOX_MEMORY_MAX`, `SANDBOX_CPU_QUOTA`, `SANDBOX_TASKS_MAX`), where the daemon puts every container; the account's user slice above it caps the daemon and builds too |
| Egress and isolation rules | iptables in DinD's network namespace, written by a helper container on `--network host` | the same helper and the same rules, in rootlesskit's network namespace. `DOCKERD_ROOTLESS_ROOTLESSKIT_DETACH_NETNS=false` keeps the bridges in that namespace: in detached mode `--network host` is the real host's namespace, where the account cannot write iptables |
| `net.bridge.bridge-nf-call-iptables` | per network namespace, 1 in DinD's | per network namespace, 1 by default in rootlesskit's once root has loaded `br_netfilter`; the account cannot load modules, so the provisioning script loads it at every boot. The isolation chain still refuses to write while it reads 0 |
| `sandbox-restricted`, `sandbox-internet` | created in DinD by `init-sandbox-networks` | created on the rootless daemon by the same script. Its own address pool (`10.213.0.0/16`, `daemon.json`) keeps them clear of the host's compose networks, which a proxy must still route to |
| Outbound traffic | NAT out of the `dind` container | NAT in rootlesskit's namespace, then slirp4netns, which opens each connection as the account. Host loopback (`10.0.2.2`) is off. The private-range drop applies first, so a sandbox still reaches no LAN or compose address |
| Proxies and enrollment | eight containers in DinD; enrollment by `docker exec` into each | unchanged: the same containers on the rootless daemon, the same `docker exec` enrollment |
| MinIO and the orchestrator from a sandbox | through `minio-proxy` and `presign-proxy`, which reach the compose services by IP | unchanged. The rootless daemon is still a network namespace apart from the compose networks, so a sandbox has no other route to them |
| Images | built on the host, pushed to the in-stack `registry` by `init-push-images`, run by digest | built on the rootless daemon itself; `registry`, `registry-auth-init` and `init-push-images` do not run. Images run by local tag: only the account that owns the daemon can replace one, which is what the pins guarded against on a registry other services could reach |
| Volumes, `docker commit` pre-builds | on DinD's storage | on the account's storage (`~embry0-sandbox/.local/share/docker`), unchanged otherwise |
| `dind_enabled` profiles, QA `mode: dind` | allowed with an admin's per-repo Docker grant | **refused** before anything is created: the daemon's account owns every sandbox and proxy, and a container it starts on the host network can rewrite their isolation rules. Use QA `mode: process` |
| Every sandbox object | found by name (`sandbox-*`) | the same, and labelled `embry0.managed=true` with `embry0.role` (`sandbox` or `proxy`) and the job id, on both runtimes |

### Isolation under rootless, and why gVisor is not offered

Isolation under `rootless` is: the rootless daemon (an unprivileged account in
its own user namespace), `--cap-drop=ALL` and `no-new-privileges`, the
egress drop and the `EMBRY0-SANDBOX-ISOLATION` chain, and per-sandbox memory,
CPU and process limits under the collective ceiling.

**gVisor (`runsc`) is not offered under `rootless`.** Under rootless Docker
runsc runs only with `--ignore-cgroups`, which creates no container cgroup at
all, so every per-sandbox limit would be dropped; with its own network stack it
also loses Docker's embedded DNS, and with it the proxies' names. It is
refused at both gates: a sandbox profile naming any OCI runtime but runc is
refused before the sandbox is created, and the orchestrator refuses to start on
a daemon that registers runsc (under any name) or defaults to a runtime other
than runc. Do not add a `runtimes` entry to `daemon.json`. gVisor is revisited
for the SaaS runtime (#587).

### Why `minio-proxy` and `presign-proxy` stay

They look like DinD plumbing, and the plan for this runtime expected them to go.
They must not be removed. A sandbox reaches MinIO and the presign endpoint only
through them, because the rootless daemon is a network namespace of its own,
like DinD: `sandbox-restricted` has no NAT and leads nowhere, and
`sandbox-internet` drops every private address, the compose networks included.
Removing them would take either a private-range egress exception for MinIO and
the orchestrator, or exposing the whole orchestrator API where `presign-proxy`
forwards one path. Both weaken an isolation control (#268).

### Requirements

- Linux with cgroup v2 and systemd (a user manager per account, `loginctl`).
- Docker Engine with its rootless extras (`docker-ce-rootless-extras`),
  `uidmap`, `slirp4netns`, `iptables` and `dbus-user-session`, from your
  distribution or Docker's repository. Verified with Docker 29.9 and
  rootlesskit 3.2 on Ubuntu 24.04.
- Kernel modules root can load: `br_netfilter` above all.

### Set it up

```bash
sudo infra/rootless/provision-sandbox-daemon.sh
# ... ready: DOCKER_HOST=unix:///run/embry0-sandbox/docker.sock, group embry0-sandbox (gid 998), ...
```

It is idempotent; run it again after changing a setting. Settings, as
environment: `SANDBOX_USER` (`embry0-sandbox`), `SANDBOX_MEMORY_MAX` (`8G`),
`SANDBOX_CPU_QUOTA` (`400%`, 100% per core), `SANDBOX_TASKS_MAX` (`16384`) and
`DAEMON_MEMORY_EXTRA` (`2G`, the daemon's room above the ceiling). It:

1. checks the prerequisites and cgroup v2, and loads `br_netfilter` and the
   iptables modules now and at every boot (`/etc/modules-load.d`);
2. creates the account with a login shell of `nologin`; it needs a subordinate
   uid and gid range (`/etc/subuid`, `/etc/subgid`);
3. creates `/run/embry0-sandbox` (mode 0750, the account's group) at every boot
   (`/etc/tmpfiles.d`);
4. delegates the cpu, cpuset, io, memory and pids controllers to the account's
   user manager, caps its user slice, and enables lingering so the daemon starts
   at boot without a login;
5. installs `infra/rootless/docker.service` and `embry0-sandboxes.slice` as user
   units and `infra/rootless/daemon.json` as the daemon's configuration, and
   (re)starts the daemon;
6. refuses unless the daemon reports rootless, cgroup v2 and all three limits,
   and a probe container lands under `embry0-sandboxes.slice`.

`daemon.json` listens on the socket only, makes the socket's group the
account's own (`"group": "0"`: root inside the user namespace is the account),
sets the address pool and the cgroup parent. To read the daemon's log:
`journalctl --user -M embry0-sandbox@ -u docker`.

### Point the stack at it

The user that runs compose (`embry0`) joins the account's group, so it can
build images on the daemon and the boot unit can wait for it:

```bash
sudo usermod -aG embry0-sandbox embry0     # log in again for it to apply
```

In `infra/.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.rootless.yml
SANDBOX_DOCKER_GID=998                     # the gid the script printed
```

`infra/docker-compose.rootless.yml` then sets `AGENT_RUNTIME=rootless` and the
socket for the orchestrator, mounts `/run/embry0-sandbox` (the directory, so a
daemon restart does not strand the mount), adds the group, creates the sandbox
networks on the rootless daemon, and switches off `dind`, `registry`,
`registry-auth-init` and `init-push-images`. `infra/deploy.sh` and the systemd
unit run compose from `infra/` and pick the overlay up from `COMPOSE_FILE`.
`embry0 start`, `build` and `build-sandbox` read the same `COMPOSE_FILE`, pass
`infra/.env` to compose after the root `.env` when it is set, build the sandbox
and proxy images on the rootless daemon, and wait for that daemon instead of
`dind`.

The overlay also mounts the skills store from a host directory,
`SKILLS_STORE_HOST` in `infra/.env` (default `/var/lib/embry0/skills`), and
passes that path to the orchestrator as `SKILLS_STORE_DAEMON_PATH`: the rootless
daemon resolves a sandbox's skill mounts on the host, where the base file's
named volume has no path. Create the directory before the first start. It must
be writable by the orchestrator container's user (`orchestrator`; from `infra/`, its uid is
`docker compose run --rm --no-deps orchestrator id -u`) and readable by the
sandbox daemon's account, so mode 0755 owned by that uid serves both:

```bash
sudo install -d -m 0755 -o "$(docker compose run --rm --no-deps orchestrator id -u)" /var/lib/embry0/skills
```

### Build the images on the daemon

The images profile builds on whichever daemon `DOCKER_HOST` names. Build the
sandbox and proxy images on the rootless daemon, and the orchestrator and
frontend on the host's, as before:

```bash
cd ~/embry0/infra
DOCKER_HOST=unix:///run/embry0-sandbox/docker.sock docker compose --profile images build \
  sandbox-image-builder dev-python-image-builder dev-jvm-python-image-builder \
  qa-image-builder proxy-image-builder
docker compose build
docker compose up -d
```

The rebuild recipes in [deploy.md](deploy.md#images-registry-pins-and-rebuilds)
apply with the same `DOCKER_HOST` prefix on the image build, and without the
`init-push-images` recreate: recreating the orchestrator alone restarts the
proxies on the new image.

### Survive a reboot

Install the drop-in that makes `embry0.service` wait for the daemon before it
starts the stack, next to the unit from §6:

```bash
sudo install -D -m 0644 infra/systemd/embry0.service.d/rootless-sandbox-daemon.conf \
  /etc/systemd/system/embry0.service.d/rootless-sandbox-daemon.conf
sudo systemctl daemon-reload
```

### Verify

```bash
export DOCKER_HOST=unix:///run/embry0-sandbox/docker.sock
docker info --format '{{.SecurityOptions}} cgroup v{{.CgroupVersion}}'   # name=rootless … cgroup v2
docker network inspect sandbox-restricted --format '{{index .Options "com.docker.network.bridge.enable_ip_masquerade"}}'  # false
docker ps --filter label=embry0.role=proxy --format '{{.Names}}'          # the proxies
docker compose logs orchestrator 2>&1 | grep -E 'sandbox_networks_verified|egress_enforcement_ready|sandbox_isolation_ensured'
```

A start against the wrong daemon fails with `AGENT_RUNTIME=rootless refuses to
start: the daemon is not rootless …`.

### Go back to DinD

Remove the two lines from `infra/.env` and `docker compose up -d`. The rootless
daemon can stay; stop it with
`sudo systemctl --user -M embry0-sandbox@ disable --now docker`.
