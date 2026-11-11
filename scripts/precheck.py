#!/usr/bin/env python3
"""LLM-free pre-check gate: decide whether a headless LLM loop has real work.

Prints exactly one line:
  "IDLE"            -> the loop wrapper may skip this cycle
  "WORK: <reason>"  -> the loop must run

Contract (fail-open): ONLY a line starting with "IDLE" may suppress a cycle.
Errors, corrupt state, a store it may not read, empty output: anything
unexpected must produce WORK, because a skipped real cycle means an unhealed
failure, while a wasted cycle only costs tokens. Only a store that does not
exist counts as "no alerts".

Relative paths in the config resolve against the kit folder (the folder
above scripts/), the same base heal_wrapper.sh and watchdog.py use.

Why this exists: in July 2026 a measured 7-day audit of a real hourly repair
loop showed ~95% of its runs were no-ops, roughly $231/week of
API-equivalent spend for zero changes. The fix wasn't a smarter model. It
was this gate: don't start an LLM to discover there is nothing to do.

Stdlib only. Part of agent-ops-kit.
"""

from __future__ import annotations

import argparse
import json
import os

KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _kit_path(path: str) -> str:
    """Return the path unchanged when it is absolute, else resolved against the kit folder."""
    return os.path.join(KIT_DIR, os.path.expanduser(path))


def _unresolved_alerts(alerts_path: str) -> int:
    """Count unresolved alerts; a missing store counts 0, any other read or parse problem raises."""
    try:
        fh = open(alerts_path, encoding="utf-8")
    except FileNotFoundError:
        return 0
    count = 0
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)  # corrupt or non-object line -> exception -> WORK (fail-open)
            if not rec.get("resolved"):
                count += 1
    return count


def decide(config_path: str) -> str:
    """Return "IDLE" only when the store is readable and has no unresolved alert, else a WORK line."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        open_alerts = _unresolved_alerts(_kit_path(cfg.get("alerts_path", "state/alerts.jsonl")))
        if open_alerts:
            return f"WORK: {open_alerts} unresolved alert(s)"
        return "IDLE"
    except Exception as exc:  # fail-open by design: never silently skip a cycle
        return f"WORK: precheck-error {exc.__class__.__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    """Print the one decision line; always exit 0 so the wrapper reads the line, not the code."""
    ap = argparse.ArgumentParser(description="LLM-free pre-check gate (fail-open).")
    ap.add_argument("--config", required=True, help="path to agent_ops config JSON")
    args = ap.parse_args(argv)
    print(decide(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
