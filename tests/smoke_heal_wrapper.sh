#!/usr/bin/env bash
# Env-gated fixture smoke for heal_wrapper.sh — run with SMOKE=1.
# Verifies the four gates against a real (temp) fixture on the target
# platform (BSD/macOS friendly): kill switch, IDLE gate, WORK run, lock.
set -euo pipefail

[ "${SMOKE:-0}" = "1" ] || { echo "skip (set SMOKE=1 to run)"; exit 0; }

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

mk_config() { # $1 = enabled (true|false)
  cat > "$TMP/config.json" <<JSON
{
  "enabled": $1,
  "mode": "shadow",
  "alerts_path": "$TMP/alerts.jsonl",
  "approvals_dir": "$TMP/approvals",
  "jobs": []
}
JSON
}

run_wrapper() {
  AGENT_OPS_CONFIG="$TMP/config.json" \
  AGENT_OPS_BRAIN_CMD="touch $TMP/brain_ran" \
  AGENT_OPS_LOCK_DIR="$TMP/heal.lock" \
  AGENT_OPS_LOG_DIR="$TMP/logs" \
  AGENT_OPS_WALLCLOCK_S=30 \
  bash "$KIT_DIR/scripts/heal_wrapper.sh"
}

# A: kill switch stops everything
mk_config false
run_wrapper
[ ! -f "$TMP/brain_ran" ] || fail "brain ran despite kill switch"

# B: enabled but no work -> explicit IDLE suppresses the brain
mk_config true
run_wrapper
[ ! -f "$TMP/brain_ran" ] || fail "brain ran despite IDLE precheck"

# C: unresolved alert -> WORK -> brain runs
printf '%s\n' '{"fingerprint":"demo:stale","resolved":false}' > "$TMP/alerts.jsonl"
run_wrapper
[ -f "$TMP/brain_ran" ] || fail "brain did not run on WORK"
rm -f "$TMP/brain_ran"

# D: held (fresh) lock -> second instance exits without running
mkdir "$TMP/heal.lock"
run_wrapper
[ ! -f "$TMP/brain_ran" ] || fail "brain ran despite held lock"
rmdir "$TMP/heal.lock" 2>/dev/null || true

# E: corrupt/unreadable config -> kill switch fails CLOSED (codex-scan QF1)
# The wrapper exits 78 (FATAL) on a corrupt config by design — tolerate that
# nonzero exit here, the smoke assertion is "brain never ran".
printf 'this is not json\n' > "$TMP/config.json"
run_wrapper || true
[ ! -f "$TMP/brain_ran" ] || fail "brain ran despite corrupt config (kill switch must fail closed)"

# F: stale lock -> real takeover, brain runs, lock ends up released
# (codex-scan QF2: old code detected staleness but never re-acquired the
# lock, so it fell through without ever holding it)
mk_config true
printf '%s\n' '{"fingerprint":"demo:stale","resolved":false}' > "$TMP/alerts.jsonl"
mkdir "$TMP/heal.lock"
touch -t 202001010000 "$TMP/heal.lock"
run_wrapper
[ -f "$TMP/brain_ran" ] || fail "brain did not run on stale-lock takeover"
[ ! -d "$TMP/heal.lock" ] || fail "lock left held after stale takeover run"
rm -f "$TMP/brain_ran" "$TMP/alerts.jsonl"

# G: invalid wallclock budget -> falls back to a safe default instead of
# disabling the hard-kill guard (codex-scan QF8)
mk_config true
printf '%s\n' '{"fingerprint":"demo:stale","resolved":false}' > "$TMP/alerts.jsonl"
AGENT_OPS_CONFIG="$TMP/config.json" \
AGENT_OPS_BRAIN_CMD="touch $TMP/brain_ran" \
AGENT_OPS_LOCK_DIR="$TMP/heal.lock" \
AGENT_OPS_LOG_DIR="$TMP/logs" \
AGENT_OPS_WALLCLOCK_S="not-a-number" \
  bash "$KIT_DIR/scripts/heal_wrapper.sh"
[ -f "$TMP/brain_ran" ] || fail "brain did not run with invalid wallclock budget"
grep -q "falling back to 3600s" "$TMP/logs/heal.log" || fail "no fallback WARN logged for invalid budget"
rm -f "$TMP/brain_ran" "$TMP/alerts.jsonl"

echo "SMOKE OK: kill switch (incl. fail-closed on corrupt config), IDLE gate, WORK run, single-instance lock (incl. stale takeover), wallclock budget validation"
