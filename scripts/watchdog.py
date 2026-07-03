#!/usr/bin/env python3
"""Job-health watchdog: check heartbeats, append alerts. Log, don't page.

Every scheduled job you care about writes (or touches) a heartbeat file when
it finishes successfully. This watchdog checks those heartbeats against a
max age and appends one alert per problem to an append-only JSONL store.

Design rules baked in:
- "Log, don't page": the watchdog never notifies anyone. Alerts go to the
  store; a human-facing digest or a heal loop consumes them from there.
- One unresolved alert per problem (fingerprint dedup) — no alert storms.
- Resolving is explicit (--resolve FINGERPRINT), typically done by the heal
  loop after a verified fix, or by a human.

Stdlib only. Part of agent-ops-kit.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import tempfile


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@contextlib.contextmanager
def _locked(alerts_path: str):
    """Exclusive lock scoped to one alert store.

    Guards the read-modify-write cycles in append_alert() and
    resolve_fingerprint() against each other, so a scheduled watchdog run
    appending a new alert can't race a heal loop's --resolve call and lose
    the append (codex-scan QF7: resolve_fingerprint reads the whole file,
    then replaces it — an append landing in that window used to vanish).
    """
    lock_path = alerts_path + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    with open(lock_path, "a") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def read_alerts(path: str) -> list[dict]:
    alerts: list[dict] = []
    if not os.path.exists(path):
        return alerts
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                alerts.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip corrupt lines here; the precheck gate treats a corrupt
                # store as WORK (fail-open), so nothing gets lost silently.
                continue
    return alerts


def unresolved_fingerprints(alerts: list[dict]) -> set[str]:
    return {a.get("fingerprint", "") for a in alerts if not a.get("resolved")}


def append_alert(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with _locked(path), open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def resolve_fingerprint(path: str, fingerprint: str) -> int:
    """Mark all unresolved alerts with this fingerprint as resolved (atomic rewrite)."""
    with _locked(path):
        alerts = read_alerts(path)
        changed = 0
        for a in alerts:
            if a.get("fingerprint") == fingerprint and not a.get("resolved"):
                a["resolved"] = True
                changed += 1
        if changed:
            dir_ = os.path.dirname(path) or "."
            fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".alerts-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for a in alerts:
                    fh.write(json.dumps(a, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        return changed


def check_jobs(cfg: dict, now: dt.datetime) -> list[dict]:
    """Return new alert records for missing/stale heartbeats (deduped)."""
    alerts_path = cfg.get("alerts_path", "state/alerts.jsonl")
    known = unresolved_fingerprints(read_alerts(alerts_path))
    new: list[dict] = []
    for job in cfg.get("jobs", []):
        name = job.get("name", "unnamed-job")
        hb = job.get("heartbeat")
        max_age_h = float(job.get("max_age_hours", 26))
        kind = None
        msg = ""
        if not hb or not os.path.exists(hb):
            kind, msg = "never_ran", f"heartbeat missing: {hb}"
        else:
            age_h = (now.timestamp() - os.path.getmtime(hb)) / 3600.0
            if age_h > max_age_h:
                kind, msg = "stale", f"heartbeat {age_h:.1f}h old (max {max_age_h}h)"
        if kind is None:
            continue
        fp = f"{name}:{kind}"
        if fp in known:
            continue
        new.append(
            {
                "ts": now.isoformat(timespec="seconds"),
                "source": "watchdog",
                "job": name,
                "kind": kind,
                "message": msg,
                "fingerprint": fp,
                "resolved": False,
            }
        )
    return new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Heartbeat watchdog (log, don't page).")
    ap.add_argument("--config", required=True, help="path to agent_ops config JSON")
    ap.add_argument("--now", help="ISO timestamp override (for tests)")
    ap.add_argument("--resolve", help="mark alerts with this fingerprint as resolved")
    ap.add_argument("--strict", action="store_true", help="exit 1 when new alerts were raised")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    alerts_path = cfg.get("alerts_path", "state/alerts.jsonl")

    if args.resolve:
        n = resolve_fingerprint(alerts_path, args.resolve)
        print(f"resolved {n} alert(s) for {args.resolve}")
        return 0

    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now()
    new = check_jobs(cfg, now)
    for rec in new:
        append_alert(alerts_path, rec)
        print(f"ALERT {rec['fingerprint']}: {rec['message']}")
    if not new:
        print("OK: no new alerts")
    return 1 if (new and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
