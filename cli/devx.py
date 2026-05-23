import sys
import argparse
import subprocess
import os
import re
from pathlib import Path

try:
    from inquirer import prompt, Checkbox
    HAS_INQUIRER = True
except ImportError:
    prompt = None
    Checkbox = None
    HAS_INQUIRER = False


def install_inquirer():
    """Notify user to install inquirer manually if unavailable."""
    if HAS_INQUIRER:
        return
    print("Inquirer is not installed.")
    print("Install it manually to enable the interactive checkbox menu:")
    print("  python3 -m pip install inquirer")
    print("Falling back to simple prompts.")


def run_command(cmd, cwd=None, capture_output=True, silent=False, env=None):
    """Helper to run a command in the WSL environment."""
    try:
        merged_env = None
        if env:
            merged_env = os.environ.copy()
            merged_env.update(env)

        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            capture_output=capture_output,
            text=True,
            env=merged_env,
        )
        return result.stdout.strip() if capture_output else None
    except subprocess.CalledProcessError as e:
        if not silent:
            if capture_output:
                print(f"Error running command {' '.join(cmd)}: {e.stderr}")
            else:
                print(f"Error running command {' '.join(cmd)}")
        raise e


def setup_ssh_keys():
    """Generates SSH keys in WSL if they don't exist and prepares them for injection."""
    ssh_dir = Path.home() / ".ssh"
    key_path = ssh_dir / "id_rsa_devx"
    pub_key_path = ssh_dir / "id_rsa_devx.pub"

    if not ssh_dir.exists():
        ssh_dir.mkdir(mode=0o700)

    if not key_path.exists():
        print(f"Generating new SSH key for DevX at {key_path}...")
        run_command(
            ["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", str(key_path), "-N", ""],
            capture_output=False,
        )

    with open(pub_key_path, "r") as f:
        return f.read().strip()


def inject_ssh_key(pub_key, container_id):
    """Injects the public key into the running sandbox container for both root and devx users."""
    print("Injecting SSH public key into container...")
    for user in ["root", "devx"]:
        home = "/root" if user == "root" else "/devx"
        run_command(["docker", "exec", container_id, "mkdir", "-p", f"{home}/.ssh"])
        # Use grep to check if key already exists before appending
        cmd = f"grep -qF '{pub_key}' {home}/.ssh/authorized_keys || echo '{pub_key}' >> {home}/.ssh/authorized_keys && chmod 600 {home}/.ssh/authorized_keys && chown -R {user}:{user} {home}/.ssh"
        run_command(["docker", "exec", container_id, "sh", "-c", cmd])


def check_docker():
    """Ensure dockerd is running in WSL."""
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Error: Docker is not running in WSL. Please start dockerd.")
        sys.exit(1)


def get_compose_files(core_dir, stacks):
    """Return the list of compose files to use in the current operation."""
    files = [os.path.join(core_dir, "docker-compose.yml")]
    for stack in stacks:
        stack_file = os.path.join(core_dir, f"docker-compose.{stack}.yml")
        if not os.path.exists(stack_file):
            raise FileNotFoundError(f"Optional compose file not found: {stack_file}")
        files.append(stack_file)
    return files


def run_compose(
    core_dir,
    stacks,
    compose_args,
    instance,
    ssh_port,
    postgres_port,
    capture_output=True,
):
    cmd = ["docker", "compose", "-p", instance]
    for compose_file in get_compose_files(core_dir, stacks):
        cmd += ["-f", compose_file]
    cmd += compose_args
    return run_command(
        cmd,
        cwd=core_dir,
        capture_output=capture_output,
        env={
            "DEVX_SSH_PORT": str(ssh_port),
            "DEVX_POSTGRES_PORT": str(postgres_port),
        },
    )


def run_compose_legacy(
    core_dir,
    stacks,
    compose_args,
    instance,
    ssh_port,
    postgres_port,
    capture_output=True,
):
    cmd = ["docker-compose", "-p", instance]
    for compose_file in get_compose_files(core_dir, stacks):
        cmd += ["-f", compose_file]
    cmd += compose_args
    return run_command(
        cmd,
        cwd=core_dir,
        capture_output=capture_output,
        env={
            "DEVX_SSH_PORT": str(ssh_port),
            "DEVX_POSTGRES_PORT": str(postgres_port),
        },
    )


def get_sandbox_container_id(core_dir, stacks, instance, ssh_port, postgres_port):
    """Resolve the current sandbox container ID for the selected compose project."""
    try:
        container_id = run_compose(
            core_dir,
            stacks,
            ["ps", "-q", "sandbox"],
            instance,
            ssh_port,
            postgres_port,
        )
    except Exception:
        container_id = run_compose_legacy(
            core_dir,
            stacks,
            ["ps", "-q", "sandbox"],
            instance,
            ssh_port,
            postgres_port,
        )

    if not container_id:
        raise RuntimeError("Unable to resolve sandbox container ID for SSH key injection.")
    return container_id


def get_windows_ssh_config_path():
    """Returns the path to the Windows SSH config file from WSL."""
    try:
        # Use cmd.exe to get the USERPROFILE environment variable directly
        win_profile = run_command(["cmd.exe", "/c", "echo %USERPROFILE%"]).strip()
        if not win_profile or "%USERPROFILE%" in win_profile:
            # Fallback if cmd.exe fails or returns literal
            win_user = run_command(["cmd.exe", "/c", "echo %USERNAME%"]).strip()
            config_path = Path(f"/mnt/c/Users/{win_user}/.ssh/config")
        else:
            # Convert Windows path (C:\Users\...) to WSL path (/mnt/c/Users/...)
            wsl_profile = run_command(["wslpath", win_profile]).strip()
            config_path = Path(wsl_profile) / ".ssh" / "config"
        return config_path
    except Exception as e:
        print(f"Debug: Failed to resolve Windows SSH path: {e}")
        return None


def update_ssh_config_file(config_path, host_alias, port):
    """Upsert a host entry in an SSH config file."""
    config_entry = (
        f"\nHost {host_alias}\n"
        "    HostName 127.0.0.1\n"
        f"    Port {port}\n"
        "    User devx\n"
        "    IdentityFile ~/.ssh/id_rsa_devx\n"
        "    StrictHostKeyChecking no\n"
        "    UserKnownHostsFile /dev/null\n"
    )

    content = ""
    if config_path.exists():
        with open(config_path, "r") as f:
            content = f.read()

    pattern = rf"(?ms)^Host\s+{re.escape(host_alias)}\n(?:[ \t].*\n)*"
    if re.search(pattern, content):
        updated = re.sub(pattern, config_entry.lstrip("\n"), content)
        with open(config_path, "w") as f:
            f.write(updated)
        print(f"Updated SSH config for '{host_alias}' in {config_path}.")
    else:
        with open(config_path, "a") as f:
            f.write(config_entry)
        print(f"Appended SSH config for '{host_alias}' in {config_path}.")


def update_windows_ssh_config(instance, ssh_port):
    """Updates Windows and WSL SSH config for the selected DevX instance."""
    config_path = get_windows_ssh_config_path()
    if not config_path:
        print("Warning: Could not locate Windows SSH config path.")
        return

    host_alias = "devx" if instance == "devx" else f"devx-{instance}"

    # If we can't even check existence of the parent, we likely have a mount/permission issue
    try:
        if not config_path.parent.exists():
            print(f"Creating Windows SSH directory: {config_path.parent}")
            config_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"Error: Permission denied accessing {config_path.parent}.")
        print(
            "Please ensure your WSL has permission to write to your Windows user profile."
        )
        return

    # We need to handle the IdentityFile path carefully.
    # Since the key is generated in WSL, we should probably copy it to Windows .ssh too.
    wsl_key = Path.home() / ".ssh" / "id_rsa_devx"
    win_key = config_path.parent / "id_rsa_devx"

    if wsl_key.exists() and not win_key.exists():
        print(f"Copying DevX key to Windows SSH directory: {win_key}")
        run_command(["cp", str(wsl_key), str(win_key)])

    # --- Update Windows Config ---
    update_ssh_config_file(config_path, host_alias, ssh_port)

    # --- Update WSL Config ---
    wsl_config_path = Path.home() / ".ssh" / "config"
    wsl_config_path.parent.mkdir(parents=True, exist_ok=True)

    update_ssh_config_file(wsl_config_path, host_alias, ssh_port)


def up(args):
    """Starts the DevX sandbox."""
    check_docker()

    # Get the directory of the current script to find core/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    core_dir = os.path.join(project_root, "core")

    # 1. Setup SSH keys locally in WSL
    pub_key = setup_ssh_keys()

    # Get current git hash and origin for build signature
    try:
        current_hash = run_command(
            ["git", "rev-parse", "HEAD"], cwd=project_root, silent=True
        )
    except Exception:
        current_hash = "unknown"

    try:
        current_origin = run_command(
            ["git", "remote", "get-url", "origin"], cwd=project_root, silent=True
        )
    except Exception:
        current_origin = "unknown"

    if current_origin != "unknown":
        print(
            f"Starting DevX Sandbox (Build Signature: {current_hash[:8]}, Origin: {current_origin})..."
        )
    else:
        print(f"Starting DevX Sandbox (Build Signature: {current_hash[:8]})...")

    compose_stacks = getattr(args, "stacks", []) or []
    instance = args.instance
    ssh_port = args.ssh_port
    postgres_port = args.postgres_port
    compose_args = [
        "build",
        "--build-arg",
        f"DEVX_VERSION={current_hash}",
        "--build-arg",
        f"DEVX_ORIGIN={current_origin}",
    ]
    try:
        run_compose(
            core_dir,
            compose_stacks,
            compose_args,
            instance,
            ssh_port,
            postgres_port,
        )
        run_compose(
            core_dir,
            compose_stacks,
            ["up", "-d"],
            instance,
            ssh_port,
            postgres_port,
        )
    except Exception:
        # Fallback to legacy docker-compose if `docker compose` is unavailable.
        run_compose_legacy(
            core_dir,
            compose_stacks,
            compose_args,
            instance,
            ssh_port,
            postgres_port,
        )
        run_compose_legacy(
            core_dir,
            compose_stacks,
            ["up", "-d"],
            instance,
            ssh_port,
            postgres_port,
        )

    # 2. Inject the key into the container
    container_id = get_sandbox_container_id(
        core_dir, compose_stacks, instance, ssh_port, postgres_port
    )
    inject_ssh_key(pub_key, container_id)

    # 3. Update Windows SSH config
    update_windows_ssh_config(instance, ssh_port)

    host_alias = "devx" if instance == "devx" else f"devx-{instance}"
    print(f"Connect with: ssh {host_alias}")

    print("Sandbox is up and running.")


def down(args):
    """Stops the DevX sandbox."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    core_dir = os.path.join(os.path.dirname(script_dir), "core")

    compose_stacks = getattr(args, "stacks", []) or []
    instance = args.instance
    ssh_port = args.ssh_port
    postgres_port = args.postgres_port
    print("Stopping DevX Sandbox...")
    try:
        run_compose(
            core_dir,
            compose_stacks,
            ["down"],
            instance,
            ssh_port,
            postgres_port,
            capture_output=False,
        )
    except Exception:
        run_compose_legacy(
            core_dir,
            compose_stacks,
            ["down"],
            instance,
            ssh_port,
            postgres_port,
            capture_output=False,
        )
    print("Sandbox stopped.")


def status(args):
    """Checks the status of the DevX sandbox."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    core_dir = os.path.join(os.path.dirname(script_dir), "core")

    compose_stacks = getattr(args, "stacks", []) or []
    instance = args.instance
    ssh_port = args.ssh_port
    postgres_port = args.postgres_port
    try:
        output = run_compose(
            core_dir,
            compose_stacks,
            ["ps"],
            instance,
            ssh_port,
            postgres_port,
        )
    except Exception:
        output = run_compose_legacy(
            core_dir,
            compose_stacks,
            ["ps"],
            instance,
            ssh_port,
            postgres_port,
        )
    print(output)


def interactive_select():
    """Interactive menu to select sandbox toolchains and services."""
    install_inquirer()  # Ensure inquirer is available
    if HAS_INQUIRER:
        # Fancy menu with checkboxes
        questions = [
            {
                "type": "checkbox",
                "name": "toolchains",
                "message": "Select sandbox toolchains (add languages/runtimes to your dev container):",
                "choices": [
                    {"name": "Java (OpenJDK 11 + Maven/Gradle)", "value": "java"},
                    {"name": "Node.js (v20 + npm/yarn/pnpm)", "value": "node"},
                    {"name": ".NET (SDK 8.0)", "value": "dotnet"},
                ],
            },
            {
                "type": "checkbox",
                "name": "services",
                "message": "Select supporting services (run as separate containers):",
                "choices": [
                    {"name": "PostgreSQL database", "value": "postgres"},
                    {"name": "Chroma vector database", "value": "chroma"},
                ],
            },
        ]

        answers = prompt(questions)
        selected = answers.get("toolchains", []) + answers.get("services", [])

        if selected:
            print(f"\nSelected stacks: {', '.join(selected)}")
        else:
            print("\nNo stacks selected (using base sandbox only)")
        return selected
    else:
        # Fallback to simple text prompts
        print("DevX Interactive Setup")
        print("======================")

        # Sandbox toolchains
        print(
            "\nSelect sandbox toolchains (these add languages/runtimes to your dev container):"
        )
        toolchains = ["java", "node", "dotnet"]
        selected_toolchains = []
        for tc in toolchains:
            response = input(f"Include {tc}? (y/n): ").strip().lower()
            if response == "y":
                selected_toolchains.append(tc)

        # Services
        print("\nSelect supporting services (these run as separate containers):")
        services = ["postgres", "chroma"]
        selected_services = []
        for svc in services:
            response = input(f"Include {svc}? (y/n): ").strip().lower()
            if response == "y":
                selected_services.append(svc)

        selected = selected_toolchains + selected_services
        if selected:
            print(f"\nSelected stacks: {', '.join(selected)}")
        else:
            print("\nNo stacks selected (using base sandbox only)")
        return selected


def print_stack_help():
    """Print available optional stacks and usage examples."""
    print("Available optional stacks:")
    print("  Toolchains (inside sandbox): java, node, dotnet")
    print("  Services (sidecars): postgres, chroma")
    print("")
    print("Examples:")
    print("  ./devx up")
    print("  ./devx up node")
    print("  ./devx up postgres chroma")
    print("  ./devx up node postgres")
    print("  ./devx up -i devx2 -p 2223 node chroma")
    print("  ./devx up -i devx2 -p 2223 --postgres-port 5433 node postgres")
    print("  ./devx status node postgres")
    print("  ./devx down node postgres")


def main():
    parser = argparse.ArgumentParser(
        description="DevX 2.0 Orchestrator",
        epilog=(
            "Optional stacks:\n"
            "  Toolchains: java node dotnet\n"
            "  Services:   postgres chroma\n\n"
            "Multi-instance options:\n"
            "  -i, --instance   Compose project name (default: devx)\n"
            "  -p, --ssh-port   Host SSH port for sandbox (default: 2222)\n\n"
            "Postgres options:\n"
            "  --postgres-port  Host port for postgres (default: 5432)\n\n"
            "Examples:\n"
            "  ./devx up\n"
            "  ./devx up node\n"
            "  ./devx up postgres chroma\n"
            "  ./devx up node postgres\n"
            "  ./devx up -i devx2 -p 2223 node chroma\n"
            "  ./devx up -i devx2 -p 2223 --postgres-port 5433 node postgres\n"
            "  ./devx stacks"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Up command
    up_parser = subparsers.add_parser("up", help="Start the sandbox")
    up_parser.add_argument(
        "-i",
        "--instance",
        default="devx",
        help="Compose project name (default: devx)",
    )
    up_parser.add_argument(
        "-p",
        "--ssh-port",
        type=int,
        default=2222,
        help="Host SSH port for sandbox (default: 2222)",
    )
    up_parser.add_argument(
        "--postgres-port",
        type=int,
        default=5432,
        help="Host port for postgres (default: 5432)",
    )
    up_parser.add_argument(
        "stacks",
        nargs="*",
        help="Optional stack names to include, e.g. node postgres chroma",
    )

    # Down command
    down_parser = subparsers.add_parser("down", help="Stop the sandbox")
    down_parser.add_argument(
        "-i",
        "--instance",
        default="devx",
        help="Compose project name (default: devx)",
    )
    down_parser.add_argument(
        "-p",
        "--ssh-port",
        type=int,
        default=2222,
        help="Host SSH port for sandbox (default: 2222)",
    )
    down_parser.add_argument(
        "--postgres-port",
        type=int,
        default=5432,
        help="Host port for postgres (default: 5432)",
    )
    down_parser.add_argument(
        "stacks",
        nargs="*",
        help="Optional stack names to use when stopping the sandbox",
    )

    # Status command
    status_parser = subparsers.add_parser("status", help="Check sandbox status")
    status_parser.add_argument(
        "-i",
        "--instance",
        default="devx",
        help="Compose project name (default: devx)",
    )
    status_parser.add_argument(
        "-p",
        "--ssh-port",
        type=int,
        default=2222,
        help="Host SSH port for sandbox (default: 2222)",
    )
    status_parser.add_argument(
        "--postgres-port",
        type=int,
        default=5432,
        help="Host port for postgres (default: 5432)",
    )
    status_parser.add_argument(
        "stacks", nargs="*", help="Optional stack names to use when checking status"
    )

    # Stacks command
    subparsers.add_parser("stacks", help="List optional stack names and examples")

    args = parser.parse_args()

    if args.command == "up":
        if not args.stacks:
            args.stacks = interactive_select()
        up(args)
    elif args.command == "down":
        down(args)
    elif args.command == "status":
        status(args)
    elif args.command == "stacks":
        print_stack_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
