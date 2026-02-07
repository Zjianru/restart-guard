#!/usr/bin/env python3
"""
restart-guard: write_context.py
Generate a restart context file with YAML frontmatter + Markdown body.

Usage:
  python3 write_context.py --config <path> --reason "..." \
    [--verify <cmd> <expect>]... \
    [--resume <step>]... \
    [--note "..."]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

TEMPLATE = """\
---
reason: {reason}
triggered_at: {triggered_at}
triggered_by: {triggered_by}
verify:
{verify_block}
resume:
{resume_block}
rollback:
  config_backup: {config_backup}
---

# Restart Context

## Reason

{reason_text}

## Pre-Restart State

<!-- Auto-generated; agent may append details -->

## Notes

{notes}
"""


def main():
    parser = argparse.ArgumentParser(description="Write restart context file")
    parser.add_argument("--config", required=True, help="Path to restart-guard.yaml")
    parser.add_argument("--reason", required=True, help="Why restart is needed")
    parser.add_argument("--verify", nargs=2, action="append", metavar=("CMD", "EXPECT"),
                        help="Verification command and expected output (repeatable)")
    parser.add_argument("--resume", action="append", metavar="STEP",
                        help="Post-restart resume step (repeatable)")
    parser.add_argument("--note", default="", help="Additional notes")
    parser.add_argument("--triggered-by", default="agent", help="Who triggered (agent/user/cron)")
    args = parser.parse_args()

    # Load config to get context_file path
    config = load_config(args.config)
    context_path = os.path.expanduser(config.get("paths", {}).get("context_file", "~/.openclaw/net/work/restart-context.md"))
    backup_dir = os.path.expanduser(config.get("paths", {}).get("backup_dir", ""))

    # Build YAML blocks
    verify_lines = []
    if args.verify:
        for cmd, expect in args.verify:
            verify_lines.append(f'  - command: "{cmd}"')
            verify_lines.append(f'    expect: "{expect}"')
    else:
        verify_lines.append('  - command: "openclaw health --json"')
        verify_lines.append('    expect: "ok"')

    resume_lines = []
    if args.resume:
        for step in args.resume:
            resume_lines.append(f'  - "{step}"')
    else:
        resume_lines.append('  - "向用户汇报重启结果"')

    now = datetime.now(timezone.utc).astimezone()
    content = TEMPLATE.format(
        reason=quote_yaml(args.reason),
        triggered_at=now.isoformat(),
        triggered_by=args.triggered_by,
        verify_block="\n".join(verify_lines),
        resume_block="\n".join(resume_lines),
        config_backup=quote_yaml(os.path.join(backup_dir, "openclaw.json")) if backup_dir else '""',
        reason_text=args.reason,
        notes=args.note or "<!-- none -->",
    )

    os.makedirs(os.path.dirname(context_path), exist_ok=True)
    with open(context_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Context written to {context_path}")


def quote_yaml(s):
    """Quote a string for YAML if it contains special chars."""
    if any(c in s for c in ':"{}[]&*?|>!%@`'):
        return f'"{s}"'
    return f'"{s}"'


def load_config(path):
    """Load YAML config (minimal parser, no PyYAML dependency)."""
    import json
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        print(f"Warning: config not found at {expanded}, using defaults", file=sys.stderr)
        return {}
    # Try JSON first (YAML superset for simple configs)
    try:
        with open(expanded, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        pass
    # Minimal YAML-like parser for flat/nested keys
    return _parse_simple_yaml(expanded)


def _parse_simple_yaml(path):
    """Very basic YAML parser for the config format we use."""
    import re
    result = {}
    current_section = None
    current_subsection = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            # Top-level section (no indent)
            m = re.match(r'^(\w[\w_]*):\s*$', stripped)
            if m:
                current_section = m.group(1)
                result[current_section] = {}
                current_subsection = None
                continue
            # Sub-section (2-space indent)
            m = re.match(r'^  (\w[\w_]*):\s*$', stripped)
            if m and current_section is not None:
                current_subsection = m.group(1)
                result[current_section][current_subsection] = {}
                continue
            # Key-value (2-space indent, in section)
            m = re.match(r'^  (\w[\w_]*):\s+(.+)$', stripped)
            if m and current_section is not None and current_subsection is None:
                key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
                result[current_section][key] = val
                continue
            # Key-value (4-space indent, in subsection)
            m = re.match(r'^    (\w[\w_]*):\s+(.+)$', stripped)
            if m and current_section is not None and current_subsection is not None:
                key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
                result[current_section][current_subsection][key] = val
                continue
    return result


if __name__ == "__main__":
    main()
