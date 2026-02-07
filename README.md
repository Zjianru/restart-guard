# restart-guard

An [OpenClaw](https://github.com/openclaw/openclaw) skill for safely restarting the Gateway with context preservation, health monitoring, and failure notification.

## Why

When an OpenClaw agent needs to restart the Gateway (config changes, model switches, plugin reloads), it faces a problem: the restart kills its own runtime. Without preparation, the agent loses context about *why* it restarted and *what to do next*.

**restart-guard** solves this with a structured flow:

1. **Save context** before restart (reason, verification commands, resume steps)
2. **Spawn a guardian** watchdog that survives the restart
3. **Trigger restart** via SIGUSR1 hot restart
4. **Guardian monitors** health and sends success/failure notifications
5. **Agent recovers** post-restart, runs targeted verification, resumes work

## Features

- 📋 **Structured context** — YAML frontmatter for machine-readable restart metadata + Markdown for human notes
- 🛡️ **Guardian watchdog** — Independent process (setsid) survives gateway restart, monitors health
- 🔔 **Notification abstraction** — Primary: OpenClaw message tool → Fallback: Telegram / Slack / Discord direct API
- 🔒 **Safety controls** — Cooldown lock, consecutive failure limit, config backup before restart
- 🎯 **Targeted verification** — Post-restart checks only run commands declared in the context file
- 🔧 **Zero external dependencies** — Pure Python 3 standard library + curl

## Quick Start

### 1. Install

```bash
clawhub install restart-guard
```

Or clone manually into your skills directory.

### 2. Configure

```bash
cp skills/restart-guard/config.example.yaml ~/.openclaw/config/restart-guard.yaml
```

Edit the config file — at minimum, set your notification preferences:

```yaml
notification:
  primary: "openclaw"       # Uses OpenClaw message tool (works when gateway is up)
  fallback: "telegram"      # Direct API fallback (works when gateway is down)
  telegram:
    bot_token_env: "TELEGRAM_BOT_TOKEN"
    chat_id: "YOUR_CHAT_ID"
```

### 3. Enable Gateway Restart

In `openclaw.json`:

```json
{
  "commands": {
    "restart": true
  }
}
```

Ensure your agent has `gateway` and `exec` in `tools.allow`.

### 4. Use

The agent calls the scripts in sequence:

```bash
SKILL=skills/restart-guard/scripts
CFG=~/.openclaw/config/restart-guard.yaml

# Step 1: Save context
python3 $SKILL/write_context.py --config $CFG \
  --reason "Model config change" \
  --verify 'openclaw health --json' 'ok' \
  --resume "Report result to user"

# Step 2: Restart (spawns guardian, triggers SIGUSR1)
python3 $SKILL/restart.py --config $CFG --reason "Model config change"

# Step 3: After gateway recovers and pings the session
python3 $SKILL/postcheck.py --config $CFG
```

## How It Works

```
Agent                    restart.py              guardian.py           Gateway
  │                          │                       │                   │
  ├─ write_context.py ──────►│                       │                   │
  │                          ├─ validate context     │                   │
  │                          ├─ check cooldown       │                   │
  │                          ├─ backup config        │                   │
  │                          ├─ spawn guardian ──────►│ (detached)        │
  │                          ├─ send notification     │                   │
  │                          ├─ POST /tools/invoke ──┼───────────────────►│ SIGUSR1
  │                          └─ exit                  │                   │
  │                                                   │  ┌─ restarting ──►│
  │                                                   │  │                │
  │                                                   ├──┤ poll health    │
  │                                                   │  │                │
  │                                                   │  └─ healthy! ────►│
  │                                                   ├─ send notification│
  │                                                   └─ release lock     │
  │                                                                       │
  │◄──────────────────────── gateway pings session ───────────────────────┤
  ├─ postcheck.py                                                         │
  │   ├─ read context frontmatter                                         │
  │   ├─ run verify commands                                              │
  │   └─ report results                                                   │
  └─ resume work                                                          │
```

## Configuration Reference

See [`config.example.yaml`](config.example.yaml) for all options. Key sections:

| Section | Purpose |
|---------|---------|
| `paths` | Context file, lock file, restart log, backup directory, openclaw binary |
| `gateway` | Host, port, restart delay, auth token env var |
| `guardian` | Poll interval, timeout, diagnostic commands |
| `safety` | Cooldown seconds, max consecutive failures, config backup toggle |
| `notification` | Primary/fallback channels, per-channel settings |

## Context File Format

The context file uses YAML frontmatter for structured data:

```yaml
---
reason: "Model config change"
triggered_at: "2026-02-07T15:30:00+08:00"
triggered_by: agent
verify:
  - command: "openclaw health --json"
    expect: "ok"
resume:
  - "Report restart result to user"
  - "Continue documentation task"
rollback:
  config_backup: "~/.openclaw/restart-backup/openclaw.json"
---

# Restart Context

## Reason
Model config change — switched to claude-opus-4.6

## Notes
<!-- Additional context for post-restart recovery -->
```

## Safety Mechanisms

| Mechanism | Default | Purpose |
|-----------|---------|---------|
| Cooldown lock | 600s | Prevents restart storms |
| Consecutive failure limit | 3 | Stops auto-restart, requires manual intervention |
| Config backup | Enabled | Backs up `openclaw.json` before restart |
| Lock file | `/tmp/restart-guard.lock` | Single restart at a time |
| Precheck | `openclaw doctor` | Validates config before restart |

## File Structure

```
restart-guard/
├── SKILL.md                # Skill metadata and agent instructions
├── README.md               # This file
├── config.example.yaml     # Configuration template
├── scripts/
│   ├── write_context.py    # Generate restart context file
│   ├── restart.py          # Main orchestrator (validate → backup → guardian → restart)
│   ├── guardian.py         # Independent watchdog (health poll → notify → unlock)
│   └── postcheck.py        # Post-restart verification (run verify commands from context)
├── templates/
│   └── restart-context.md  # Context file template
└── references/
    └── troubleshooting.md  # Common issues and solutions
```

## Requirements

- **OpenClaw** with `commands.restart: true`
- **Python 3.10+** (standard library only, no pip install needed)
- **curl** (for API calls)
- **`GATEWAY_AUTH_TOKEN`** environment variable (or in `~/.openclaw/.env`)

## Troubleshooting

See [`references/troubleshooting.md`](references/troubleshooting.md) for common issues:
- Lock file cleanup
- Guardian notification failures
- Verification mismatches
- Config rollback procedure

## License

MIT — Copyright (c) 2026 [Zjianru](https://github.com/Zjianru)
