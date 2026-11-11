#!/usr/bin/env python3
"""Job-health watchdog: check heartbeats, append alerts. Log, don't page.

Every scheduled job you care about writes (or touches) a heartbeat file when
it finishes successfully. This watchdog checks those heartbeats against a
max age and appends one alert per problem to a JSONL alert store.

Design rules baked in:
- "Log, don't page": the watchdog never notifies anyone. Alerts go to the
  store; a human-facing digest or a heal loop consumes them from there.
- One unresolved alert per problem (fingerprint dedup). Reading the store,
  deciding and appending happen under one lock, so overlapping runs can't
  raise the same alert twice.
- A broken job entry raises its own "bad_config" alert; the other jobs are
  still checked.
- Resolving is explicit (--resolve FINGERPRINT), typically done by the heal
  loop after a verified fix, or by a human. It rewrites the store in place,
  keeps every line it can't read and keeps the file mode.
- Relative paths in the config resolve against the kit folder (the folder
  above scripts/), the same base heal_wrapper.sh uses.

Stdlib only. Part of agent-ops-kit.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import stat
import tempfile

KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MAX_AGE_H = 26


def kit_path(path: str) -> str:
    """Return the path unchanged when it is absolute, else resolved against the kit folder."""
    return os.path.join(KIT_DIR, os.path.expanduser(path))


def load_config(path: str) -> dict:
    """Read the config JSON (a broken config is a loud error, not an alert)."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@contextlib.contextmanager
def _locked(alerts_path: str):
    """Hold the exclusive lock of one alert store; check-and-append and --resolve both take it."""
    lock_path = alerts_path + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    with open(lock_path, "a") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _parse(line: str) -> dict | None:
    """Return the alert on this line, or None for a corrupt or non-object line."""
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    return rec if isinstance(rec, dict) else None


def _read_lines(path: str) -> list[str]:
    """Return the raw lines of the store; a missing store has none, other read errors raise."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readlines()
    except FileNotFoundError:
        return []


def read_alerts(path: str) -> list[dict]:
    """Return every readable alert; unreadable lines are skipped here (the precheck treats them as WORK)."""
    return [rec for line in _read_lines(path) if line.strip() and (rec := _parse(line)) is not None]


def unresolved_fingerprints(alerts: list[dict]) -> set[str]:
    """Return the fingerprints of all alerts that are not resolved yet."""
    return {a.get("fingerprint", "") for a in alerts if not a.get("resolved")}


def resolve_fingerprint(path: str, fingerprint: str) -> int:
    """Mark unresolved alerts with this fingerprint as resolved and return how many changed."""
    with _locked(path):
        out, changed = [], 0
        for line in _read_lines(path):
            rec = _parse(line) if line.strip() else None
            if rec is not None and rec.get("fingerprint") == fingerprint and not rec.get("resolved"):
                rec["resolved"] = True
                changed += 1
                line = json.dumps(rec, ensure_ascii=False) + "\n"
            out.append(line if line.endswith("\n") else line + "\n")
        if changed:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".alerts-")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.writelines(out)
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
            os.replace(tmp, path)
        return changed


def _job_problem(job: object, seen: set[str]) -> str | None:
    """Return why this job entry can't be checked, or None when it is fine."""
    if not isinstance(job, dict):
        return "job entry is not an object"
    name = job.get("name")
    if not isinstance(name, str) or not name.strip():
        return "job has no name"
    if name in seen:
        return f"duplicate job name {name!r}"
    if not isinstance(job.get("heartbeat"), str) or not job["heartbeat"]:
        return "job has no heartbeat path"
    age = job.get("max_age_hours", DEFAULT_MAX_AGE_H)
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 < age < float("inf"):
        return f"max_age_hours must be a positive number, got {age!r}"
    return None


def _heartbeat_problem(heartbeat: str, max_age_h: float, now: dt.datetime) -> tuple[str | None, str]:
    """Return (kind, message) when the heartbeat is missing, unreadable or too old, else (None, "")."""
    try:
        st = os.stat(kit_path(heartbeat))
    except FileNotFoundError:
        return "never_ran", f"heartbeat missing: {heartbeat}"
    except OSError as exc:
        return "unreadable", f"cannot read heartbeat {heartbeat}: {exc.strerror or exc}"
    if not stat.S_ISREG(st.st_mode):  # a folder's mtime would pass for a fresh heartbeat
        return "unreadable", f"heartbeat is not a file: {heartbeat}"
    age_h = (now.timestamp() - st.st_mtime) / 3600.0
    if age_h > max_age_h:
        return "stale", f"heartbeat {age_h:.1f}h old (max {max_age_h}h)"
    return None, ""


def _job_results(jobs: list, now: dt.datetime):
    """Yield (label, kind, message) for every job entry that is broken or whose heartbeat is not healthy."""
    seen: set[str] = set()
    for i, job in enumerate(jobs):
        problem = _job_problem(job, seen)
        name = job.get("name") if isinstance(job, dict) else None
        label = f"jobs[{i}]"
        if isinstance(name, str) and name.strip() and name not in seen:
            seen.add(name)
            label = name
        if problem:
            yield label, "bad_config", problem
            continue
        kind, msg = _heartbeat_problem(job["heartbeat"], float(job.get("max_age_hours", DEFAULT_MAX_AGE_H)), now)
        if kind:
            yield label, kind, msg


def check_jobs(cfg: dict, now: dt.datetime, known: set[str]) -> list[dict]:
    """Return new alert records for broken entries and missing/stale heartbeats, skipping known fingerprints."""
    jobs = cfg.get("jobs", [])
    results = _job_results(jobs, now) if isinstance(jobs, list) else [("jobs", "bad_config", "jobs must be a list")]
    new = []
    for label, kind, msg in results:
        fp = f"{label}:{kind}"
        if fp in known:
            continue
        known.add(fp)
        new.append(
            {
                "ts": now.isoformat(timespec="seconds"),
                "source": "watchdog",
                "job": label,
                "kind": kind,
                "message": msg,
                "fingerprint": fp,
                "resolved": False,
            }
        )
    return new


def record_new_alerts(cfg: dict, now: dt.datetime) -> list[dict]:
    """Check all jobs and append the new alerts, reading and writing under the store lock."""
    path = kit_path(cfg.get("alerts_path", "state/alerts.jsonl"))
    with _locked(path):
        new = check_jobs(cfg, now, unresolved_fingerprints(read_alerts(path)))
        if new:
            with open(path, "a", encoding="utf-8") as fh:
                fh.writelines(json.dumps(rec, ensure_ascii=False) + "\n" for rec in new)
    return new


def main(argv: list[str] | None = None) -> int:
    """Run one watchdog pass, or resolve a fingerprint with --resolve."""
    ap = argparse.ArgumentParser(description="Heartbeat watchdog (log, don't page).")
    ap.add_argument("--config", required=True, help="path to agent_ops config JSON")
    ap.add_argument("--now", help="ISO timestamp override (for tests)")
    ap.add_argument("--resolve", help="mark alerts with this fingerprint as resolved")
    ap.add_argument("--strict", action="store_true", help="exit 1 when new alerts were raised")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.resolve:
        n = resolve_fingerprint(kit_path(cfg.get("alerts_path", "state/alerts.jsonl")), args.resolve)
        print(f"resolved {n} alert(s) for {args.resolve}")
        return 0

    now = dt.datetime.fromisoformat(args.now) if args.now else dt.datetime.now()
    new = record_new_alerts(cfg, now)
    for rec in new:
        print(f"ALERT {rec['fingerprint']}: {rec['message']}")
    if not new:
        print("OK: no new alerts")
    return 1 if (new and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
