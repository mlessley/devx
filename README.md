# DevX 2.0: Containerized Development Sandbox

DevX 2.0 is a high-performance sandbox and bootstrap layer for workspace workflows. It provides a fast, persistent Linux execution environment for ad hoc work, experimentation, and workspace orchestration.

## Scope

DevX currently provides:
- A persistent sandbox container with SSH access
- A cross-platform CLI entry point (`./devx` and `devx.bat`)
- Claude Code preinstalled in the base sandbox (Node.js 22 LTS)
- Optional stack overlays for sandbox toolchains (`java`, `dotnet`)
- Optional service overlays (`postgres`, `chroma`, `ztp`)
- Multi-instance startup support through instance and port flags

## Key Features

- **Bootstrap Layer**: A cross-platform entry point (`./devx`) that starts and manages a persistent sandbox from Windows (via WSL), macOS, or Linux.
- **Volume-Native Performance**: All project data and user settings live in named Docker volumes (ext4), bypassing slow host-to-container file system overhead for maximum I/O performance.
- **Pure UV Runtimes**: Powered by `uv`, providing lightning-fast Python runtime management (3.11, 3.12, ...) and dependency resolution.
- **Built-in Claude Code**: The base sandbox includes `claude` out of the box, with updates pinned to image rebuilds (`DISABLE_AUTOUPDATER=true`).
- **Automated Connectivity**: Automatically configures the host SSH client to connect to the sandbox with a single command: `ssh devx`.

## Architecture

1. **Local machine**: Windows 11 (via WSL2), macOS, or Linux.
2. **DevX CLI**: Starts, stops, and configures the sandbox.
3. **Docker sandbox**: A container with SSH access and persistent volume storage.

## Getting Started

### Prerequisites
- **Windows**: WSL2 (Ubuntu) and Docker Desktop (or native Docker inside WSL2).
- **macOS/Linux**: Docker and Python 3.

### Installation
1. Clone this repository to your host machine.
2. Open a terminal in the project root.
3. Initialize the sandbox:
   ```bash
   ./devx up
   ```
   *(On Windows CMD/PowerShell, you can still use `devx.bat up`)*

### Usage
- **Start/Update**: `./devx up`
- **Check Status**: `./devx status`
- **Stop**: `./devx down`
- **Connect**: `ssh devx` (or use VS Code Remote-SSH to connect to the `devx` host).
- **Run Claude Code**: `claude` (one-time OAuth login persists because `/devx` is a named volume).

### Optional stacks
DevX supports optional compose overlays so toolchains and service overlays can be added when needed for a particular repo or experiment.

#### Interactive mode
Run `./devx up` without arguments to launch an interactive menu that lets you select:
- Sandbox toolchains (Java, Node, .NET — added to your dev container)
- Service overlays (Postgres sidecar, Chroma sidecar, ZTP stack — run as separate containers)

The menu uses a modern checkbox interface (installs `inquirer` automatically using `uv`).

#### Direct mode
Pass stack names directly:
- Start sandbox + Chroma only:
  ```bash
  ./devx up chroma
  ```
- Start sandbox + Postgres only:
  ```bash
  ./devx up postgres
  ```
- Start sandbox + Java 11 runtime only:
  ```bash
  ./devx up java
  ```
- Start sandbox + ZTP stack (Temporal + Nautobot 2.x):
  ```bash
  ./devx up ztp
  ```
- Start sandbox with multiple optional services:
  ```bash
  ./devx up chroma postgres java
  ```

If you prefer, the same stack names can be passed to `status` and `down` as well:

- `./devx status postgres`
- `./devx down chroma java`

#### ZTP stack integration points
The `ztp` stack is intended to work with external/shared observability and includes:
- Export-ready service endpoints:
  - Temporal gRPC/UI: `127.0.0.1:7233` / `127.0.0.1:8233`
  - Nautobot v2.4.21: `127.0.0.1:8889` (override with `DEVX_NAUTOBOT_PORT`)
- Preconfigured sandbox env for Temporal clients:
  - `TEMPORAL_ADDRESS=temporal:7233`
  - `TEMPORAL_CLI_ADDRESS=temporal:7233`
- Optional OTLP env hook (`OTEL_EXPORTER_OTLP_ENDPOINT`) for push-based telemetry
- Discovery labels (`devx.metrics.*`) so an external Prometheus harness can discover scrape targets

The stack does not include Prometheus or Grafana. Keep scrape and dashboard configuration in your external observability deployment.

### Multi-instance usage
Use instance and port flags when running more than one sandbox at the same time.

- Start a second sandbox instance:
  ```bash
  ./devx up -i devx2 -p 2223
  ```
- Start a second sandbox instance with Java + Postgres:
  ```bash
  ./devx up -i devx2 -p 2223 --postgres-port 5433 java postgres
  ```
- Check status for a specific instance:
  ```bash
  ./devx status -i devx2 -p 2223
  ```

## Project Structure

- `cli/`: The orchestrator logic (Python).
- `core/`: Docker infrastructure and entrypoint scripts.
- `devx`: The universal shell bridge.
- `devx.bat`: Windows CMD/PowerShell convenience bridge.
- `/devx/`: The persistent root inside the container (Home directory, repos, and tools).
