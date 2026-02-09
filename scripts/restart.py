#!/usr/bin/env python3
"""
restart-guard: restart.py
Main restart orchestrator. Validates context, spawns guardian, triggers restart.

Usage:
  python3 restart.py --config <path> [--reason "..."] [--force]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser(description="Restart OpenClaw Gateway")
    parser.add_argument("--config", required=True, help="Path to restart-guard.yaml")
    parser.add_argument("--reason", default="", help="Override reason (uses context file if empty)")
    parser.add_argument("--force", action="store_true", help="Ignore cooldown lock")
    args = parser.parse_args()

    config = load_config(args.config)
    paths = config.get("paths", {})
    safety = config.get("safety", {})
    gateway_cfg = config.get("gateway", {})
    notif = config.get("notification", {})

    context_path = expand(paths.get("context_file", "~/.openclaw/net/work/restart-context.md"))
    lock_path = expand(paths.get("lock_file", "/tmp/restart-guard.lock"))
    log_path = expand(paths.get("restart_log", "~/.openclaw/net/work/restart.log"))
    backup_dir = expand(paths.get("backup_dir", "~/.openclaw/net/work/restart-backup"))
    oc_config = expand(paths.get("openclaw_config", "~/.openclaw/openclaw.json"))
    oc_bin = find_openclaw(paths.get("openclaw_bin", ""))

    cooldown = int(safety.get("cooldown_seconds", 600))
    max_failures = int(safety.get("max_consecutive_failures", 3))
    do_backup = str(safety.get("backup_config", "true")).lower() == "true"

    host = gateway_cfg.get("host", "127.0.0.1")
    port = gateway_cfg.get("port", "18789")
    delay_ms = gateway_cfg.get("restart_delay_ms", "2000")
    auth_token_env = gateway_cfg.get("auth_token_env", "GATEWAY_AUTH_TOKEN")
    auth_token = os.environ.get(auth_token_env, "")

    # --- Validations ---
    if not os.path.isfile(context_path) or os.path.getsize(context_path) == 0:
        die(f"Restart context missing or empty: {context_path}\nWrite context first (write_context.py)")

    if not auth_token:
        # Try .env fallback
        auth_token = dotenv_get(auth_token_env)
    if not auth_token:
        die(f"Missing {auth_token_env} environment variable")

    if not oc_bin:
        die("Cannot find 'openclaw' binary. Set paths.openclaw_bin in config.")

    # --- Cooldown lock ---
    if os.path.exists(lock_path) and not args.force:
        try:
            mtime = os.path.getmtime(lock_path)
            age = time.time() - mtime
            if age < cooldown:
                die(f"Cooldown active ({int(cooldown - age)}s remaining). Use --force to override.")
        except OSError:
            pass

    # Check consecutive failures
    failures = count_recent_failures(log_path, max_failures)
    if failures >= max_failures and not args.force:
        die(f"Consecutive failures ({failures}) >= max ({max_failures}). Manual intervention required.")

    # --- Acquire lock ---
    try:
        with open(lock_path, "w") as f:
            f.write(json.dumps({
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "reason": args.reason,
            }))
    except OSError as e:
        die(f"Cannot acquire lock: {e}")

    # --- Backup config ---
    if do_backup and os.path.isfile(oc_config):
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, "openclaw.json")
        shutil.copy2(oc_config, backup_path)
        log_entry(log_path, "backup", f"config backed up to {backup_path}")

    # --- Precheck ---
    rc = subprocess.call([oc_bin, "doctor", "--non-interactive"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        log_entry(log_path, "precheck_fail", "openclaw doctor failed")
        notify(notif, config, oc_bin,
               f"⚠️ Restart aborted: precheck failed (openclaw doctor rc={rc})")
        cleanup_lock(lock_path)
        sys.exit(1)

    log_entry(log_path, "precheck_ok", "")

    # --- Spawn guardian ---
    guardian_py = os.path.join(SCRIPT_DIR, "guardian.py")
    guardian_log = os.path.join(os.path.dirname(context_path), "guardian.log")
    guardian_cmd = [
        sys.executable, guardian_py,
        "--config", args.config,
    ]

    # Fully detach guardian from this process tree
    with open(guardian_log, "a") as glog:
        subprocess.Popen(
            guardian_cmd,
            stdout=glog, stderr=glog,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # setsid equivalent
        )

    log_entry(log_path, "guardian_spawned", "")

    # --- Send pre-restart notification ---
    reason = args.reason or "(see context file)"
    notify(notif, config, oc_bin,
           f"🔄 OpenClaw: preparing to restart\n- reason: {reason}\n- guardian: watching")

    # --- Trigger restart ---
    url = f"http://{host}:{port}/tools/invoke"
    payload = json.dumps({
        "tool": "gateway",
        "action": "restart",
        "args": {"delayMs": int(delay_ms)},
        "sessionKey": "main",
    })

    try:
        result = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
             "-H", f"Authorization: Bearer {auth_token}",
             "-H", "Content-Type: application/json",
             "-d", payload, url],
            capture_output=True, text=True, timeout=30,
        )
        http_code = result.stdout.strip()
        if http_code == "200":
            log_entry(log_path, "triggered", f"http={http_code}")
            print(f"Restart triggered (http={http_code}). Guardian is monitoring.")
            sys.exit(0)
        else:
            log_entry(log_path, "trigger_failed", f"http={http_code}")
            notify(notif, config, oc_bin,
                   f"❌ Restart trigger failed (http={http_code})")
            cleanup_lock(lock_path)
            sys.exit(1)
    except subprocess.TimeoutExpired:
        # curl timeout might mean gateway already restarting
        log_entry(log_path, "trigger_timeout", "curl timed out, gateway may be restarting")
        print("Trigger timed out (gateway may already be restarting). Guardian is monitoring.")
        sys.exit(0)
    except Exception as e:
        log_entry(log_path, "trigger_error", str(e))
        notify(notif, config, oc_bin, f"❌ Restart trigger error: {e}")
        cleanup_lock(lock_path)
        sys.exit(1)


# --- Helpers ---

def expand(p):
    return os.path.expanduser(p) if p else p


def find_openclaw(configured):
    if configured:
        p = expand(configured)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    # which
    p = shutil.which("openclaw")
    if p:
        return p
    # nvm fallback
    import glob
    candidates = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/openclaw")))
    if candidates:
        return candidates[-1]
    return None


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


def count_recent_failures(log_path, window):
    """Count consecutive failures from the end of the log."""
    if not os.path.isfile(log_path):
        return 0
    count = 0
    with open(log_path, "r") as f:
        lines = f.readlines()
    for line in reversed(lines):
        if "result=ok" in line or "result=triggered" in line:
            break
        if "result=timeout" in line or "result=trigger_failed" in line or "result=trigger_error" in line:
            count += 1
    return count


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


def load_config(path):
    """Load config file (reuse write_context.py's parser)."""
    sys.path.insert(0, SCRIPT_DIR)
    from write_context import load_config as _load
    return _load(path)


def die(msg):
    print(f"restart-guard: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
