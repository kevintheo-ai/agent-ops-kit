"""Tests for scripts/precheck.py — the fail-open contract is the point."""

import importlib.util
import json
import pathlib

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


precheck = _load("precheck")


def _config(tmp_path, **overrides):
    cfg = {
        "enabled": True,
        "mode": "shadow",
        "alerts_path": str(tmp_path / "state" / "alerts.jsonl"),
        "approvals_dir": str(tmp_path / "state" / "approvals"),
        "jobs": [],
    }
    cfg.update(overrides)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p, cfg


def _write_alert(cfg, resolved):
    p = pathlib.Path(cfg["alerts_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"fingerprint": "demo:stale", "resolved": resolved}
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_empty_state_is_idle(tmp_path):
    cfg_path, _ = _config(tmp_path)
    assert precheck.decide(str(cfg_path)) == "IDLE"


def test_unresolved_alert_is_work(tmp_path):
    cfg_path, cfg = _config(tmp_path)
    _write_alert(cfg, resolved=False)
    assert precheck.decide(str(cfg_path)).startswith("WORK: 1 unresolved")


def test_resolved_only_is_idle(tmp_path):
    cfg_path, cfg = _config(tmp_path)
    _write_alert(cfg, resolved=True)
    assert precheck.decide(str(cfg_path)) == "IDLE"


def test_pending_approval_is_work(tmp_path):
    cfg_path, cfg = _config(tmp_path)
    approvals = pathlib.Path(cfg["approvals_dir"])
    approvals.mkdir(parents=True, exist_ok=True)
    (approvals / "case-1.json").write_text("{}", encoding="utf-8")
    assert precheck.decide(str(cfg_path)).startswith("WORK: 1 pending approval")


def test_corrupt_alert_store_fails_open(tmp_path):
    cfg_path, cfg = _config(tmp_path)
    p = pathlib.Path(cfg["alerts_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("this is not json\n", encoding="utf-8")
    assert precheck.decide(str(cfg_path)).startswith("WORK: precheck-error")


def test_missing_config_fails_open(tmp_path):
    assert precheck.decide(str(tmp_path / "missing.json")).startswith("WORK: precheck-error")


def test_main_prints_single_line(tmp_path, capsys):
    cfg_path, _ = _config(tmp_path)
    assert precheck.main(["--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["IDLE"]
