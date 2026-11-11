"""Tests for scripts/precheck.py: the fail-open contract is the point."""

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

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


def test_corrupt_alert_store_fails_open(tmp_path):
    cfg_path, cfg = _config(tmp_path)
    p = pathlib.Path(cfg["alerts_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("this is not json\n", encoding="utf-8")
    assert precheck.decide(str(cfg_path)).startswith("WORK: precheck-error")


def test_missing_config_fails_open(tmp_path):
    assert precheck.decide(str(tmp_path / "missing.json")).startswith("WORK: precheck-error")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_store_fails_open(tmp_path):
    cfg_path, cfg = _config(tmp_path)
    _write_alert(cfg, resolved=False)
    state = pathlib.Path(cfg["alerts_path"]).parent
    state.chmod(0)
    try:
        assert precheck.decide(str(cfg_path)).startswith("WORK: precheck-error")
    finally:
        state.chmod(0o755)


def test_files_in_an_approvals_folder_do_not_keep_the_gate_open(tmp_path):
    """Nothing in the kit consumes approval files, so they must not start a paid run every cycle."""
    approvals = tmp_path / "state" / "approvals"
    approvals.mkdir(parents=True)
    (approvals / "case-1.json").write_text("{}", encoding="utf-8")
    cfg_path, _ = _config(tmp_path, approvals_dir=str(approvals))
    assert precheck.decide(str(cfg_path)) == "IDLE"


def test_relative_alerts_path_resolves_against_the_kit_folder(tmp_path):
    kit = tmp_path / "kit"
    (kit / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPTS / "precheck.py", kit / "scripts" / "precheck.py")
    (kit / "state").mkdir()
    (kit / "state" / "alerts.jsonl").write_text('{"fingerprint": "a:stale", "resolved": false}\n', encoding="utf-8")
    (kit / "config.json").write_text(json.dumps({"alerts_path": "state/alerts.jsonl"}), encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    cmd = [sys.executable, str(kit / "scripts" / "precheck.py"), "--config", str(kit / "config.json")]
    out = subprocess.run(cmd, cwd=elsewhere, check=True, capture_output=True, text=True).stdout.strip()
    assert out == "WORK: 1 unresolved alert(s)"


def test_main_prints_single_line(tmp_path, capsys):
    cfg_path, _ = _config(tmp_path)
    assert precheck.main(["--config", str(cfg_path)]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["IDLE"]
