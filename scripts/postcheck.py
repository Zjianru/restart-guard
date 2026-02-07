#!/usr/bin/env python3
"""
restart-guard: postcheck.py
Post-restart verification. Reads context file, runs verify commands, reports results.

Usage:
  python3 postcheck.py --config <path>

Exit codes:
  0 = all verifications passed
  1 = one or more verifications failed
  2 = context file missing or parse error
"""
import argparse
import json
import os
import re
import subprocess
import shutil
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    parser = argparse.ArgumentParser(description="Post-restart verification")
    parser.add_argument("--config", required=True, help="Path to restart-guard.yaml")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    config = load_config(args.config)
    paths = config.get("paths", {})
    context_path = expand(paths.get("context_file", "~/.openclaw/net/work/restart-context.md"))
    oc_bin = find_openclaw(paths.get("openclaw_bin", ""))

    if not os.path.isfile(context_path):
        if args.json:
            print(json.dumps({"status": "no_context", "message": "No restart context file found"}))
        else:
            print("No restart context file found. Nothing to verify.")
        sys.exit(0)  # Not an error — just no pending restart

    # Parse YAML frontmatter
    frontmatter = parse_frontmatter(context_path)
    if frontmatter is None:
        print("Error: cannot parse context file frontmatter", file=sys.stderr)
        sys.exit(2)

    reason = frontmatter.get("reason", "unknown")
    verify_list = frontmatter.get("verify", [])
    resume_list = frontmatter.get("resume", [])
    rollback = frontmatter.get("rollback", {})

    results = []
    all_passed = True

    for item in verify_list:
        if isinstance(item, dict):
            cmd = item.get("command", "")
            expect = item.get("expect", "")
        elif isinstance(item, str):
            cmd = item
            expect = ""
        else:
            continue

        if not cmd:
            continue

        # Replace 'openclaw' with actual binary
        actual_cmd = cmd
        if oc_bin and cmd.startswith("openclaw "):
            actual_cmd = oc_bin + cmd[8:]

        try:
            proc = subprocess.run(
                actual_cmd, shell=True, capture_output=True, text=True, timeout=30,
            )
            output = proc.stdout.strip()
            passed = True
            if expect:
                passed = expect.lower() in output.lower()
            if proc.returncode != 0:
                passed = False

            results.append({
                "command": cmd,
                "expect": expect,
                "output": output[:500],
                "returncode": proc.returncode,
                "passed": passed,
            })
            if not passed:
                all_passed = False
        except (subprocess.TimeoutExpired, OSError) as e:
            results.append({
                "command": cmd,
                "expect": expect,
                "output": str(e),
                "returncode": -1,
                "passed": False,
            })
            all_passed = False

    report = {
        "status": "passed" if all_passed else "failed",
        "reason": reason,
        "checks": results,
        "resume": resume_list,
        "rollback": rollback,
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"Restart postcheck: {'✅ PASSED' if all_passed else '❌ FAILED'}")
        print(f"Reason: {reason}")
        for r in results:
            status = "✅" if r["passed"] else "❌"
            print(f"  {status} {r['command']}")
            if r["expect"]:
                print(f"     expect: {r['expect']}")
            if not r["passed"]:
                print(f"     output: {r['output'][:200]}")
        if resume_list:
            print("\nResume steps:")
            for i, step in enumerate(resume_list, 1):
                print(f"  {i}. {step}")

    sys.exit(0 if all_passed else 1)


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


def parse_frontmatter(path):
    """Parse YAML frontmatter from a Markdown file (minimal, no PyYAML)."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract between --- markers
    m = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
    if not m:
        return None

    yaml_text = m.group(1)
    return _parse_yaml_block(yaml_text)


def _parse_yaml_block(text):
    """Minimal YAML parser for our frontmatter format."""
    result = {}
    current_key = None
    current_list = None

    for line in text.split("\n"):
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key with value
        m = re.match(r'^(\w[\w_]*):\s+(.+)$', stripped)
        if m:
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            result[key] = val
            current_key = key
            current_list = None
            continue

        # Top-level key without value (start of block/list)
        m = re.match(r'^(\w[\w_]*):\s*$', stripped)
        if m:
            current_key = m.group(1)
            result[current_key] = {}
            current_list = None
            continue

        # List item (string)
        m = re.match(r'^  - "(.+)"$', stripped)
        if m and current_key:
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            result[current_key].append(m.group(1))
            continue

        # List item (dict start)
        m = re.match(r'^  - (\w[\w_]*):\s+(.+)$', stripped)
        if m and current_key:
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            item = {m.group(1): m.group(2).strip().strip('"').strip("'")}
            result[current_key].append(item)
            current_list = result[current_key]
            continue

        # Dict continuation (4-space indent)
        m = re.match(r'^    (\w[\w_]*):\s+(.+)$', stripped)
        if m and current_list and len(current_list) > 0:
            current_list[-1][m.group(1)] = m.group(2).strip().strip('"').strip("'")
            continue

        # Sub-key (2-space indent, in dict)
        m = re.match(r'^  (\w[\w_]*):\s+(.+)$', stripped)
        if m and current_key and isinstance(result.get(current_key), dict):
            result[current_key][m.group(1)] = m.group(2).strip().strip('"').strip("'")
            continue

    return result


def load_config(path):
    sys.path.insert(0, SCRIPT_DIR)
    from write_context import load_config as _load
    return _load(path)


if __name__ == "__main__":
    main()
