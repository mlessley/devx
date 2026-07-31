# DevX Sandbox Hardening: Replacing DooD with Sysbox-based DinD

## Problem

DevX runs Claude Code inside a container (`core/docker-compose.yml`) intended as a
sandbox boundary between agent activity and the host. A recent change
(`e1ed2b2`, "Add Docker socket passthrough support in DevX sandbox") mounted the
host's `/var/run/docker.sock` into the sandbox so the agent could build/run
containers as part of normal dev/test workflows (Docker-outside-of-Docker, DooD).

This mount, combined with the sandbox's passwordless root (`devx ALL=(ALL)
NOPASSWD:ALL` in `core/Dockerfile`), means anything running inside the sandbox —
including a misbehaving or compromised agent — has trivial, full root-equivalent
control of the host's Docker daemon:

```
docker run -v /:/host --privileged ubuntu chroot /host
```

This is a full container-to-host escape, not a theoretical one. It defeats the
purpose of sandboxing the agent at all.

**Threat model for this design:** a misbehaving or compromised agent process
running inside the sandbox (bad prompt injection, a bug, an over-eager tool
call) — not a fully adversarial user with local shell access to the host, and
not defending against kernel-level exploits. The goal is to make sure the
agent's blast radius stops at the sandbox boundary.

**Hard requirement:** the agent must retain the ability to build, run, and
compose containers as part of real dev/test workflows. Removing Docker access
entirely is not acceptable — the fix must preserve the workflow.

## Approaches considered

- **Sysbox-based Docker-in-Docker (chosen).** Run the sandbox container under
  the `sysbox-runc` OCI runtime instead of default `runc`. Sysbox gives the
  container real Linux user-namespace isolation (container root maps to an
  unprivileged host UID) plus a virtualized `/proc`/`/sys`, which together make
  it safe to run a second, ordinary `dockerd` *inside* the sandbox without
  `--privileged`. The agent's Docker workflow is unchanged; the daemon it talks
  to is no longer the host's.
- **Plain DinD (`--privileged`).** The common `docker:dind`-style pattern.
  Rejected: `--privileged` disables seccomp, AppArmor, and device restrictions,
  and is treated in practice as equivalent to host root (well-known escapes via
  cgroup `release_agent`, direct `/dev` block-device access, etc.). This
  doesn't fix the problem, it relocates it.
- **Docker socket proxy (e.g. `docker-socket-proxy`) in front of the host
  socket.** Allow-list safe Docker API endpoints, block dangerous ones.
  Rejected as the primary fix: the Docker API is large, and an allow-list
  permissive enough to support real `build`/`run`/`compose` workflows is hard
  to make airtight. Kept in mind as a fallback if sysbox proves infeasible.
- **Full microVM per sandbox (Firecracker/Kata).** Rejected as unnecessary for
  this threat model — it defends against kernel-level escape, which is a
  broader threat than "agent misbehaves," and adds substantial operational
  complexity (nested virtualization under WSL2, a different launch model for
  `devx up`). Worth revisiting only if the threat model expands to genuinely
  adversarial/untrusted code.

## Design

### Architecture

```
Windows 11
 └─ WSL2 (Ubuntu 24.04, kernel 6.6.87.2)
     └─ dockerd (existing WSL2 Docker Engine — unchanged)
         └─ devx sandbox container  [runtime: sysbox-runc]
             ├─ sshd (key-auth only)
             ├─ nested dockerd (new — an ordinary, unmodified Docker Engine)
             │    └─ containers the agent builds/runs
             └─ devx user (NOPASSWD sudo retained — see Rationale)
```

Sysbox is a container *runtime* (an alternative to `runc`), not a replacement
for the host's Docker Engine. It's registered as an additional runtime choice
in the existing WSL2 `dockerd`'s config and applied per-container via
`runtime: sysbox-runc`. Every other container on the host is unaffected. There
is exactly one sysbox layer (the outer sandbox container) — the nested
`dockerd` inside it is a completely ordinary Docker Engine.

### Host prerequisites (one-time, outside the repo's image build)

- Install `sysbox-ce` on the WSL2 Ubuntu 24.04 host (kernel 6.6 comfortably
  meets sysbox's ID-mapped-mounts requirement, no `shiftfs` needed).
- Register `sysbox-runc` in `dockerd`'s `/etc/docker/daemon.json`, restart
  `dockerd`.
- A spike must confirm this combination works with the host's Docker Engine
  29.6.1 before the rest of the build depends on it — sysbox's documented
  compatibility testing may lag current Docker releases.
- Add `scripts/install-sysbox.sh` to the repo to make this step documented and
  repeatable rather than tribal knowledge.

### `core/Dockerfile` changes

- **Docker install (step 4b):** currently extracts only the `docker` CLI
  binary from Docker's static release tarball. The same tarball also contains
  `dockerd`, `containerd`, `containerd-shim-runc-v2`, `runc`, `docker-init`,
  and `docker-proxy` — install all of them so the image can run a full nested
  Docker Engine. This preserves the existing distro-agnostic static-binary
  install pattern (avoids apt-repo/codename issues with `trixie` being very
  new).
- Create a `docker` group at build time (deterministic GID, no longer sniffed
  from a host-mounted socket) and add `devx` to it.
- SSH hardening (step 7): change `PasswordAuthentication` to `no` and
  `PermitRootLogin` to `no`. Root remains reachable via `sudo` once logged in
  as `devx`; there's no need for a second, weaker path to root over SSH. This
  is independently worthwhile regardless of the DooD fix — it protects
  sandbox contents (source, tokens, credentials) from anything on the Windows
  host that can reach the loopback-bound SSH port.

### `core/docker-compose.yml` changes

- Remove the `/var/run/docker.sock` volume mount.
- Add `runtime: sysbox-runc` to the `sandbox` service.
- Add a persistent named volume (e.g. `devx_docker_data:/var/lib/docker`) so
  images and build cache the agent creates survive `devx down`/`up` cycles.
  Note this cache is *not* shared with the host's Docker — first pulls after a
  fresh volume are cold.
- No new port publishing. Ad hoc access to ports the agent's nested containers
  publish goes through SSH local-forwarding (`ssh -L <port>:localhost:<port>
  devx`), which reaches the sandbox's own network namespace where the nested
  daemon binds ports — not through pre-opened ports sitting on the host. This
  keeps exposure on-demand rather than standing, and VS Code Remote-SSH (already
  part of the documented workflow) auto-offers this forwarding with no manual
  step.
- Do **not** add manual `cap_drop`/`security-opt`/seccomp restrictions to this
  service. Sysbox manages the isolation model itself via user namespaces;
  hand-rolled capability restrictions on a sysbox container are likely to break
  nested Docker rather than add safety. Resource limits (`mem_limit`, `cpus`,
  `pids_limit`) are orthogonal and still worth adding for stability (containing
  a runaway build), not as a security boundary.

### `core/entrypoint.sh` changes

- Remove the dynamic host-socket GID-mapping block (`stat`/`getent`/`groupmod`
  against `/var/run/docker.sock`) — nothing left to match against, since there's
  no host socket mounted.
- Start the nested `dockerd` in the background early in the script, with a
  short wait-loop for its socket to become available, before handing off to
  `sshd`.
- `devx`'s `NOPASSWD:ALL` sudo is retained deliberately (see Rationale below),
  not removed.

### Rationale: why NOPASSWD sudo stays

Today, NOPASSWD sudo + the host Docker socket = instant host root, which is
exactly the danger this design removes. Once sysbox's user-namespace remapping
is in place, root inside the sandbox container maps to a harmless, unprivileged
UID on the WSL2 host — so escalating to root *inside* the sandbox no longer
escalates *out* of it. Keeping sudo passwordless preserves agent autonomy
(installing packages, etc., without an interactive password prompt breaking
automation) without reintroducing the risk it used to carry.

### Bundled cleanup (low priority, not load-bearing for the core fix)

- `core/docker-compose.ztp.yml`: replace throwaway credentials (`admin/admin`,
  `dev-secret-key-change-in-prod`) and `NAUTOBOT_ALLOWED_HOSTS: "*"` with
  generated/env-driven values.
- `cli/devx.py`'s `inject_ssh_key`: stop building the `sh -c` command via
  f-string interpolation of the public key; pass it through safely (e.g. via
  stdin) even though it isn't attacker-reachable today (the key is generated
  locally by the same script).

## Validation plan

1. Spike: confirm `sysbox-ce` installs cleanly on the host and a minimal
   `runtime: sysbox-runc` container successfully runs a nested `dockerd`,
   against this exact host (Ubuntu 24.04, kernel 6.6.87.2, Docker 29.6.1).
2. Build the updated image; `devx up`; SSH in; confirm nested `docker
   version` and `docker run hello-world` work.
3. Confirm isolation: `/var/run/docker.sock` inside the sandbox is not the
   host's socket; the host's own `docker ps` (on WSL2) does not show
   containers started by the agent inside the sandbox.
4. Confirm the user-namespace property directly: processes owned by
   "root" inside the sandbox show up as an unprivileged UID from the host's
   `ps aux`, not UID 0.
5. Confirm persistence: build an image inside the sandbox, `devx down &&
   devx up`, confirm the image is still cached.
6. Confirm the SSH tunnel flow end-to-end: a nested container publishes a
   port, `ssh -L <port>:localhost:<port> devx` from Windows reaches it.
7. Multi-instance sanity check (`devx up -i devx2 ...`): independent nested
   daemon per instance, no cross-talk.

## Out of scope

- MicroVM-based isolation (Kata/Firecracker) — reconsider only if the threat
  model expands beyond "agent misbehaves" to genuinely adversarial/untrusted
  code.
- Changes to `cli/devx.py`'s orchestration logic beyond the `inject_ssh_key`
  hygiene fix noted above — `devx up`/`down`/`status` are unaffected by this
  design since sysbox is purely a compose/Dockerfile/entrypoint concern.
