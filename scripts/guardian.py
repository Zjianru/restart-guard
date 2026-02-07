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
    primary = notif_config.get("primary", "openclaw")
    fallback = notif_config.get("fallback", "")
    if primary == "openclaw":
        if _notify_openclaw(notif_config, full_config, oc_bin, message):
            return
    if fallback:
        _notify_fallback(fallback, notif_config, message)


def _notify_openclaw(notif_config, full_config, oc_bin, message):
    if not oc_bin:
        return False
    oc_notif = notif_config.get("openclaw", {})
    gateway_cfg = full_config.get("gateway", {})
    host = gateway_cfg.get("host", "127.0.0.1")
    port = gateway_cfg.get("port", "18789")
    auth_env = gateway_cfg.get("auth_token_env", "GATEWAY_AUTH_TOKEN")
    auth_token = os.environ.get(auth_env, "") or dotenv_get(auth_env)
    if not auth_token:
        return False
    url = f"http://{host}:{port}/tools/invoke"
    args_obj = {"action": "send", "message": message}
    channel = oc_notif.get("channel", "")
    to = oc_notif.get("to", "")
    if channel:
        args_obj["channel"] = channel
    if to:
        args_obj["to"] = to
    payload = json.dumps({"tool": "message", "args": args_obj, "sessionKey": "main"})
    try:
        result = subprocess.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
             "-H", f"Authorization: Bearer {auth_token}",
             "-H", "Content-Type: application/json",
             "-d", payload, url],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "200"
    except Exception:
        return False


def _notify_fallback(channel, notif_config, message):
    if channel == "telegram":
        tg = notif_config.get("telegram", {})
        token_env = tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
        token = os.environ.get(token_env, "") or dotenv_get(token_env)
        chat_id = tg.get("chat_id", "")
        if token and chat_id:
            try:
                subprocess.run(
                    ["curl", "-sS", "-X", "POST",
                     f"https://api.telegram.org/bot{token}/sendMessage",
                     "-d", f"chat_id={chat_id}",
                     "--data-urlencode", f"text={message}"],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
    elif channel == "slack":
        sl = notif_config.get("slack", {})
        url_env = sl.get("webhook_url_env", "SLACK_WEBHOOK_URL")
        url = os.environ.get(url_env, "") or dotenv_get(url_env)
        if url:
            try:
                subprocess.run(
                    ["curl", "-sS", "-X", "POST", "-H", "Content-Type: application/json",
                     "-d", json.dumps({"text": message}), url],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
    elif channel == "discord":
        dc = notif_config.get("discord", {})
        url_env = dc.get("webhook_url_env", "DISCORD_WEBHOOK_URL")
        url = os.environ.get(url_env, "") or dotenv_get(url_env)
        if url:
            try:
                subprocess.run(
                    ["curl", "-sS", "-X", "POST", "-H", "Content-Type: application/json",
                     "-d", json.dumps({"content": message}), url],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass


def log(msg):
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"[guardian {ts}] {msg}", flush=True)


def load_config(path):
    sys.path.insert(0, SCRIPT_DIR)
    from write_context import load_config as _load
    return _load(path)


if __name__ == "__main__":
    main()
