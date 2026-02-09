---
name: restart-guard
description: Safely restart the OpenClaw Gateway with context preservation, health monitoring, and failure notification. Use when the agent needs to restart the Gateway (config changes, model switches, plugin reloads, or any reason requiring a restart). Handles pre-restart context saving, guardian process spawning, gateway restart triggering, post-restart verification, and fallback notifications.
---

# Restart Guard

Safely restart the OpenClaw Gateway with context preservation and automated health verification.

## Prerequisites

- `commands.restart: true` in `openclaw.json`
- Agent has `gateway` and `exec` tools allowed
- Config file ready (copy `config.example.yaml`, fill in values, pass via `--config`)

## Flow

```
write_context.py → restart.py → [SIGUSR1] → guardian.py monitors → postcheck.py verifies
```

### 1. Write Context

```bash
python3 <skill-dir>/scripts/write_context.py \
  --config <config-path> \
  --reason "config change" \
  --verify 'openclaw health --json' 'ok' \
  --resume "report restart result to user"
```

Generates a context file with YAML frontmatter (machine-readable: reason, verify commands, resume steps, rollback path) and Markdown body (human-readable notes).

### 2. Restart

```bash
python3 <skill-dir>/scripts/restart.py --config <config-path> --reason "config change"
```

Validates context → checks cooldown lock → backs up `openclaw.json` → spawns guardian (detached, survives restart) → sends pre-restart notification → triggers `gateway.restart`.

### 3. Post-Restart Verification

After gateway pings the session back:

```bash
python3 <skill-dir>/scripts/postcheck.py --config <config-path>
```

Reads `verify` commands from context frontmatter, runs each, compares output to expected value. Returns JSON (`--json`) or human-readable report.

### 4. Guardian Behavior

Runs independently. Polls `openclaw health --json` every N seconds.
- **Success**: sends notification, releases lock, exits 0
- **Timeout**: runs diagnostics (`openclaw doctor`, log tail), sends failure notification with diagnostics, releases lock, exits 1

Notification priority: OpenClaw message tool (primary) → all configured fallback channels broadcast (Telegram/Discord/Slack/generic webhook). Multiple channels can be enabled simultaneously.

## Safety

- **Cooldown lock**: minimum interval between restarts (default 600s)
- **Consecutive failure limit**: stops auto-restart after N failures (default 3)
- **Config backup**: `openclaw.json` backed up before each restart
- **Guardian detached**: `start_new_session=True` (setsid), not `exec background`

## Troubleshooting

See `references/troubleshooting.md` for common issues (lock cleanup, notification failures, verification mismatches).
