"""Tests for scripts/watchdog.py (loaded by file path, scripts/ is not a package)."""

import fcntl
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load("watchdog")

NOW = "2026-07-03T12:00:00"


def _write_config(tmp_path, jobs):
    cfg = {
        "enabled": True,
        "mode": "shadow",
        "alerts_path": str(tmp_path / "state" / "alerts.jsonl"),
        "jobs": jobs,
    }
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p, cfg


def _touch_with_age(path, now_iso, age_hours):
    import datetime as dt

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    now = dt.datetime.fromisoformat(now_iso).timestamp()
    ts = now - age_hours * 3600
    os.utime(path, (ts, ts))


def _alerts(cfg):
    return watchdog.read_alerts(cfg["alerts_path"])


def test_fresh_heartbeat_no_alert(tmp_path, capsys):
    hb = tmp_path / "state" / "hb.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "job-a", "heartbeat": str(hb), "max_age_hours": 26}])
    _touch_with_age(hb, NOW, age_hours=1)
    rc = watchdog.main(["--config", str(cfg_path), "--now", NOW])
    assert rc == 0
    assert "OK: no new alerts" in capsys.readouterr().out
    assert _alerts(cfg) == []


def test_stale_heartbeat_raises_alert(tmp_path, capsys):
    hb = tmp_path / "state" / "hb.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "job-a", "heartbeat": str(hb), "max_age_hours": 26}])
    _touch_with_age(hb, NOW, age_hours=30)
    rc = watchdog.main(["--config", str(cfg_path), "--now", NOW])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ALERT job-a:stale" in out
    recs = _alerts(cfg)
    assert len(recs) == 1
    assert recs[0]["kind"] == "stale"
    assert recs[0]["resolved"] is False


def test_missing_heartbeat_raises_never_ran(tmp_path):
    hb = tmp_path / "state" / "nope.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "job-b", "heartbeat": str(hb), "max_age_hours": 26}])
    watchdog.main(["--config", str(cfg_path), "--now", NOW])
    recs = _alerts(cfg)
    assert len(recs) == 1
    assert recs[0]["fingerprint"] == "job-b:never_ran"


def test_dedup_one_unresolved_alert_per_problem(tmp_path):
    hb = tmp_path / "state" / "hb.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "job-a", "heartbeat": str(hb), "max_age_hours": 26}])
    _touch_with_age(hb, NOW, age_hours=30)
    watchdog.main(["--config", str(cfg_path), "--now", NOW])
    watchdog.main(["--config", str(cfg_path), "--now", NOW])
    assert len(_alerts(cfg)) == 1  # second run must not duplicate


def test_strict_exit_code(tmp_path):
    hb = tmp_path / "state" / "nope.json"
    cfg_path, _ = _write_config(tmp_path, [{"name": "job-c", "heartbeat": str(hb), "max_age_hours": 26}])
    assert watchdog.main(["--config", str(cfg_path), "--now", NOW, "--strict"]) == 1


def test_resolve_marks_resolved_atomically(tmp_path, capsys):
    hb = tmp_path / "state" / "nope.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "job-d", "heartbeat": str(hb), "max_age_hours": 26}])
    watchdog.main(["--config", str(cfg_path), "--now", NOW])
    rc = watchdog.main(["--config", str(cfg_path), "--resolve", "job-d:never_ran"])
    assert rc == 0
    assert "resolved 1 alert(s)" in capsys.readouterr().out
    recs = _alerts(cfg)
    assert len(recs) == 1
    assert recs[0]["resolved"] is True
    assert watchdog.unresolved_fingerprints(recs) == set()


def test_locked_is_exclusive(tmp_path):
    """A second holder can never take the store lock while the first one holds it."""
    alerts_path = str(tmp_path / "state" / "alerts.jsonl")
    with watchdog._locked(alerts_path), open(alerts_path + ".lock", "a") as second_fh:
        with pytest.raises(BlockingIOError):
            fcntl.flock(second_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_bad_job_entry_raises_its_own_alert_and_other_jobs_are_still_checked(tmp_path):
    stale = tmp_path / "state" / "hb.json"
    _touch_with_age(stale, NOW, age_hours=30)
    jobs = [
        {"name": "typo", "heartbeat": str(stale), "max_age_hours": "26h"},
        {"name": "nulled", "heartbeat": str(stale), "max_age_hours": None},
        {"name": "job-a", "heartbeat": str(stale), "max_age_hours": 26},
    ]
    cfg_path, cfg = _write_config(tmp_path, jobs)
    assert watchdog.main(["--config", str(cfg_path), "--now", NOW]) == 0
    fps = sorted(r["fingerprint"] for r in _alerts(cfg))
    assert fps == ["job-a:stale", "nulled:bad_config", "typo:bad_config"]


def test_heartbeat_path_that_is_a_folder_is_not_a_fresh_heartbeat(tmp_path):
    folder = tmp_path / "state" / "heartbeats"
    folder.mkdir(parents=True)
    cfg_path, cfg = _write_config(tmp_path, [{"name": "j", "heartbeat": str(folder)}])
    watchdog.main(["--config", str(cfg_path), "--now", NOW])
    assert [r["fingerprint"] for r in _alerts(cfg)] == ["j:unreadable"]


def test_duplicate_or_missing_job_name_is_a_config_problem(tmp_path):
    hb = tmp_path / "state" / "nope.json"
    jobs = [
        {"name": "backup", "heartbeat": str(hb)},
        {"name": "backup", "heartbeat": str(hb)},
        {"heartbeat": str(hb)},
    ]
    cfg_path, cfg = _write_config(tmp_path, jobs)
    watchdog.main(["--config", str(cfg_path), "--now", NOW])
    fps = sorted(r["fingerprint"] for r in _alerts(cfg))
    assert fps == ["backup:never_ran", "jobs[1]:bad_config", "jobs[2]:bad_config"]


def test_non_object_and_corrupt_lines_are_skipped_and_kept_on_resolve(tmp_path):
    hb = tmp_path / "state" / "nope.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "j", "heartbeat": str(hb)}])
    store = pathlib.Path(cfg["alerts_path"])
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("42\nnot json\n", encoding="utf-8")
    store.chmod(0o644)
    assert watchdog.main(["--config", str(cfg_path), "--now", NOW]) == 0
    assert watchdog.main(["--config", str(cfg_path), "--resolve", "j:never_ran"]) == 0
    lines = store.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["42", "not json"]  # nothing the watchdog can't read gets lost
    assert json.loads(lines[2])["resolved"] is True
    assert store.stat().st_mode & 0o777 == 0o644  # the rewrite keeps the file mode


def _kit_copy(tmp_path, script):
    kit = tmp_path / "kit"
    (kit / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / script, kit / "scripts" / script)
    return kit


def test_relative_paths_resolve_against_the_kit_folder_not_the_cwd(tmp_path):
    kit = _kit_copy(tmp_path, "watchdog.py")
    (kit / "state" / "heartbeats").mkdir(parents=True)
    (kit / "state" / "heartbeats" / "a.json").write_text("{}", encoding="utf-8")
    cfg = {
        "alerts_path": "state/alerts.jsonl",
        "jobs": [
            {"name": "a", "heartbeat": "state/heartbeats/a.json"},
            {"name": "b", "heartbeat": "state/heartbeats/b.json"},
        ],
    }
    (kit / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"  # like cron, which starts in $HOME
    elsewhere.mkdir()
    cmd = [sys.executable, str(kit / "scripts" / "watchdog.py"), "--config", str(kit / "config.json")]
    subprocess.run(cmd, cwd=elsewhere, check=True, capture_output=True)
    assert not (elsewhere / "state").exists()
    lines = (kit / "state" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["fingerprint"] for x in lines] == ["b:never_ran"]


def test_dedup_check_runs_under_the_store_lock(tmp_path):
    """Two overlapping runs must not raise the same alert twice."""
    hb = tmp_path / "state" / "nope.json"
    cfg_path, cfg = _write_config(tmp_path, [{"name": "j", "heartbeat": str(hb)}])
    store = cfg["alerts_path"]
    cmd = [sys.executable, str(SCRIPTS / "watchdog.py"), "--config", str(cfg_path), "--now", NOW]
    with watchdog._locked(store):
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL)
        time.sleep(2)  # the run has started and waits for the lock
        with open(store, "a", encoding="utf-8") as fh:  # meanwhile another run raised the same alert
            fh.write(json.dumps({"fingerprint": "j:never_ran", "resolved": False}) + "\n")
    assert proc.wait(timeout=30) == 0
    assert [r["fingerprint"] for r in _alerts(cfg)] == ["j:never_ran"]
