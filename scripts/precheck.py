#!/usr/bin/env python3
"""LLM-free pre-check gate: decide whether a headless LLM loop has real work.

Prints exactly one line:
  "IDLE"            -> the loop wrapper may skip this cycle
  "WORK: <reason>"  -> the loop must run

Contract (fail-open): ONLY a line starting with "IDLE" may suppress a cycle.
Errors, corrupt state, empty output — anything unexpected — must produce
WORK, because a skipped real cycle means an unhealed failure, while a wasted
cycle only costs tokens.

Why this exists: a measured 7-day audit of a real hourly repair loop showed
~95% of its runs were no-ops — roughly $231/week of API-equivalent spend for
zero changes. The fix wasn't a smarter model. It was this gate: don't start
an LLM to discover there is nothing to do.

Stdlib only. Part of agent-ops-kit.
"""

from __future__ import annotations

import argparse
import glob
import json
import os


def _unresolved_alerts(alerts_path: str) -> int:
    count = 0
    with open(alerts_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)  # corrupt line -> exception -> WORK (fail-open)
            if not rec.get("resolved"):
                count += 1
    return count


def decide(config_path: str) -> str:
    try:
        with open(config_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        alerts_path = cfg.get("alerts_path", "state/alerts.jsonl")
        approvals_dir = cfg.get("approvals_dir", "state/approvals")
        open_alerts = _unresolved_alerts(alerts_path) if os.path.exists(alerts_path) else 0
        pending = len(glob.glob(os.path.join(approvals_dir, "*.json")))
        if open_alerts:
            return f"WORK: {open_alerts} unresolved alert(s)"
        if pending:
            return f"WORK: {pending} pending approval(s)"
        return "IDLE"
    except Exception as exc:  # fail-open by design: never silently skip a cycle
        return f"WORK: precheck-error {exc.__class__.__name__}: {exc}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LLM-free pre-check gate (fail-open).")
    ap.add_argument("--config", required=True, help="path to agent_ops config JSON")
    args = ap.parse_args(argv)
    print(decide(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
