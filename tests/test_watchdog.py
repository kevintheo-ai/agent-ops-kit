"""Tests for scripts/watchdog.py (loaded by file path — scripts/ is not a package)."""

import importlib.util
import json
import os
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load("watchdog")

NOW = "2026-07-03T12:00:00"
NOW_TS = 1751536800  # arbitrary fixed epoch used for mtime math below


def _write_config(tmp_path, jobs):
    cfg = {
        "enabled": True,
        "mode": "shadow",
        "alerts_path": str(tmp_path / "state" / "alerts.jsonl"),
        "approvals_dir": str(tmp_path / "state" / "approvals"),
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
