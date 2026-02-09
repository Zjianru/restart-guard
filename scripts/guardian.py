#!/usr/bin/env python3
"""
restart-guard: guardian.py
Independent watchdog process. Survives gateway restart.
Polls gateway health, sends success/failure notification.

Spawned by restart.py via start_new_session (setsid).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Restart Guard: Guardian watchdog")
    parser.add_argument("--config", required=True, help="Path to restart-guard.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    paths = config.get("paths", {})
    guardian_cfg = config.get("guardian", {})
    notif = config.get("notification", {})
    gateway_cfg = config.get("gateway", {})

    lock_path = expand(paths.get("lock_file", "/tmp/restart-guard.lock"))
    log_path = expand(paths.get("restart_log", "~/.openclaw/net/work/restart.log"))
    oc_bin = find_openclaw(paths.get("openclaw_bin", ""))

    poll_interval = int(guardian_cfg.get("poll_interval", 3))
    timeout = int(guardian_cfg.get("timeout", 120))
    diag_commands = guardian_cfg.get("diagnostics", [
        "openclaw doctor --non-interactive",
        "openclaw logs --tail 30",
    ])
    # Ensure diag_commands is a list
    if isinstance(diag_commands, str):
        diag_commands = [diag_commands]

    host = gateway_cfg.get("host", "127.0.0.1")
    port = gateway_cfg.get("port", "18789")

    log(f"Guardian started. timeout={timeout}s, poll={poll_interval}s")

    start_time = time.time()

    # Wait a moment for gateway to begin restart
    time.sleep(min(5, poll_interval))

    while True:
        elapsed = time.time() - start_time

        # Check if gateway is healthy
        if check_health(oc_bin, host, port):
            log("Gateway is healthy after restart")
            log_entry(log_path, "ok", "gateway healthy")
            notify(notif, config, oc_bin,
                   "✅ OpenClaw restart succeeded.\nGateway is healthy and ready.")
            cleanup_lock(lock_path)
            sys.exit(0)

        # Timeout
        if elapsed > timeout:
            log(f"Timeout after {timeout}s")
            log_entry(log_path, "timeout", f"gateway not healthy after {timeout}s")

            # Run diagnostics
            diag_output = run_diagnostics(oc_bin, diag_commands)
            msg = (
                f"❌ OpenClaw restart timed out ({timeout}s).\n"
                f"Gateway did not become healthy.\n\n"
                f"Diagnostics:\n{diag_output[:1500]}"
            )
            notify(notif, config, oc_bin, msg)
            cleanup_lock(lock_path)
            sys.exit(1)

        time.sleep(poll_interval)


def check_health(oc_bin, host, port):
    """Check gateway health via openclaw health --json."""
    if not oc_bin:
        return check_health_curl(host, port)
    try:
        result = subprocess.run(
            [oc_bin, "health", "--json", "--timeout", "5000"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                return data.get("ok", False) or data.get("status") == "ok"
            except (json.JSONDecodeError, ValueError):
                return False
        return False
    except (subprocess.TimeoutExpired, OSError):
        return False


def check_health_curl(host, port):
    """Fallback health check via curl."""
    try:
        result = subprocess.run(
            ["curl", "-sS", "--max-time", "5",
             f"http://{host}:{port}/health"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and "ok" in result.stdout.lower()
    except (subprocess.TimeoutExpired, OSError):
        return False


def run_diagnostics(oc_bin, commands):
    """Run diagnostic commands and collect output."""
    outputs = []
    for cmd in commands:
        try:
            # Replace 'openclaw' with actual binary path
            if oc_bin and cmd.startswith("openclaw "):
                cmd = oc_bin + cmd[8:]
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30,
            )
            output = result.stdout.strip() or result.stderr.strip()
            outputs.append(f"$ {cmd}\n{output}")
        except (subprocess.TimeoutExpired, OSError) as e:
            outputs.append(f"$ {cmd}\n[error: {e}]")
    return "\n\n".join(outputs)


# --- Shared helpers (duplicated from restart.py to keep guardian self-contained) ---

def expand(p):
    return os.path.expanduser(p) if p else p


def find_openclaw(configured):
    if configured:
        p = expand(configured)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    p = shutil.which("openclaw")
    if p:
        return p
    import glob
    candidates = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/openclaw")))
    return candidates[-1] if candidates else None


def dotenv_get(key):
    env_file = os.path.expanduser("~/.openclaw/.env")
    if not os.path.isfile(env_file):
        return ""
    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                return line[len(key) + 1:]
    return ""


def log_entry(path, result, note):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {ts} result={result} note={note}\n")


def cleanup_lock(lock_path):
    try:
        os.remove(lock_path)
    except OSError:
        pass


def notify(notif_config, full_config, oc_bin, message):
    """Multi-channel notification (delegated to shared notify module)."""
    sys.path.insert(0, SCRIPT_DIR)
    from notify import notify as _notify
    _notify(notif_config, full_config, oc_bin, message)


def log(msg):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"[guardian {ts}] {msg}", flush=True)


def load_config(path):
    sys.path.insert(0, SCRIPT_DIR)
    from write_context import load_config as _load
    return _load(path)


if __name__ == "__main__":
    main()
