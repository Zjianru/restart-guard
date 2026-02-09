"""
notify.py — Shared multi-channel notification for restart-guard.
Supports: OpenClaw message tool, Telegram, Discord, Slack, generic webhook.
All enabled channels are notified (not just one fallback).
"""

import json
import os
import subprocess


def dotenv_get(key):
    """Read a key from ~/.openclaw/.env (simple KEY=VALUE parser)."""
    env_path = os.path.expanduser("~/.openclaw/.env")
    if not os.path.isfile(env_path):
        return ""
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def _resolve_env(env_name):
    """Resolve env var from os.environ or .env file."""
    return os.environ.get(env_name, "") or dotenv_get(env_name)


def notify(notif_config, full_config, oc_bin, message):
    """Send notification to ALL enabled channels.

    Config format (restart-guard.yaml):
        notifications:
          primary: openclaw          # try openclaw message tool first
          channels:                  # all enabled channels get notified as fallback
            - telegram
            - discord
            - slack
            - webhook
          # Legacy: fallback: "telegram" still works (single channel)
          telegram: { bot_token_env, chat_id }
          discord:  { webhook_url_env }
          slack:    { webhook_url_env }
          webhook:  { url_env, method, headers }
    """
    primary = notif_config.get("primary", "openclaw")

    # Try primary (openclaw message tool)
    if primary == "openclaw":
        if _notify_openclaw(notif_config, full_config, oc_bin, message):
            return

    # Determine channels to notify
    channels = notif_config.get("channels", [])
    if not channels:
        # Legacy: single fallback string
        fallback = notif_config.get("fallback", "")
        if fallback:
            channels = [fallback]

    # Notify all enabled channels
    for ch in channels:
        ch = ch.strip().lower()
        try:
            if ch == "telegram":
                _notify_telegram(notif_config, message)
            elif ch == "discord":
                _notify_discord(notif_config, message)
            elif ch == "slack":
                _notify_slack(notif_config, message)
            elif ch == "webhook":
                _notify_webhook(notif_config, message)
        except Exception:
            pass


def _notify_openclaw(notif_config, full_config, oc_bin, message):
    """Try sending via openclaw message tool (gateway HTTP API)."""
    if not oc_bin:
        return False
    oc_notif = notif_config.get("openclaw", {})
    gateway_cfg = full_config.get("gateway", {})
    host = gateway_cfg.get("host", "127.0.0.1")
    port = gateway_cfg.get("port", "18789")
    auth_env = gateway_cfg.get("auth_token_env", "GATEWAY_AUTH_TOKEN")
    auth_token = _resolve_env(auth_env)
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


def _notify_telegram(notif_config, message):
    """Send via Telegram Bot API."""
    tg = notif_config.get("telegram", {})
    token_env = tg.get("bot_token_env", "TELEGRAM_BOT_TOKEN")
    token = _resolve_env(token_env)
    chat_id = tg.get("chat_id", "")
    if not token or not chat_id:
        return
    subprocess.run(
        ["curl", "-sS", "-X", "POST",
         f"https://api.telegram.org/bot{token}/sendMessage",
         "-d", f"chat_id={chat_id}",
         "--data-urlencode", f"text={message}"],
        capture_output=True, timeout=10,
    )


def _notify_discord(notif_config, message):
    """Send via Discord webhook."""
    dc = notif_config.get("discord", {})
    url_env = dc.get("webhook_url_env", "DISCORD_WEBHOOK_URL")
    url = _resolve_env(url_env)
    if not url:
        return
    subprocess.run(
        ["curl", "-sS", "-X", "POST", "-H", "Content-Type: application/json",
         "-d", json.dumps({"content": message}), url],
        capture_output=True, timeout=10,
    )


def _notify_slack(notif_config, message):
    """Send via Slack incoming webhook."""
    sl = notif_config.get("slack", {})
    url_env = sl.get("webhook_url_env", "SLACK_WEBHOOK_URL")
    url = _resolve_env(url_env)
    if not url:
        return
    subprocess.run(
        ["curl", "-sS", "-X", "POST", "-H", "Content-Type: application/json",
         "-d", json.dumps({"text": message}), url],
        capture_output=True, timeout=10,
    )


def _notify_webhook(notif_config, message):
    """Send via generic webhook (configurable URL, method, headers)."""
    wh = notif_config.get("webhook", {})
    url_env = wh.get("url_env", "RESTART_GUARD_WEBHOOK_URL")
    url = _resolve_env(url_env)
    if not url:
        return
    method = wh.get("method", "POST").upper()
    headers = wh.get("headers", {"Content-Type": "application/json"})
    body_template = wh.get("body_template", '{"text": "{{message}}"}')
    body = body_template.replace("{{message}}", message.replace('"', '\\"'))

    cmd = ["curl", "-sS", "-X", method]
    for k, v in headers.items():
        cmd.extend(["-H", f"{k}: {v}"])
    cmd.extend(["-d", body, url])
    subprocess.run(cmd, capture_output=True, timeout=10)
