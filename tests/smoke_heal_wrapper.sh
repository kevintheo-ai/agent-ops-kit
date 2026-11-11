#!/usr/bin/env bash
# Env-gated fixture smoke for heal_wrapper.sh, run with SMOKE=1.
# Checks every gate against a real (temp) fixture on the target platform
# (macOS and Linux): kill switch, IDLE gate, WORK run, lock, budget, signals, mode.
set -euo pipefail

[ "${SMOKE:-0}" = "1" ] || { echo "skip (set SMOKE=1 to run)"; exit 0; }

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

mk_config() { # $1 = enabled (true|false), $2 = mode (default shadow)
  cat > "$TMP/config.json" <<JSON
{
  "enabled": $1,
  "mode": "${2:-shadow}",
  "alerts_path": "$TMP/alerts.jsonl",
  "jobs": []
}
JSON
}

open_alert() { printf '%s\n' '{"fingerprint":"demo:stale","resolved":false}' > "$TMP/alerts.jsonl"; }

run_wrapper() { # $1 = brain command (default: touch a marker), $2 = budget seconds
  AGENT_OPS_CONFIG="$TMP/config.json" \
  AGENT_OPS_BRAIN_CMD="${1:-touch $TMP/brain_ran}" \
  AGENT_OPS_LOCK_FILE="$TMP/heal.lock" \
  AGENT_OPS_LOG_DIR="$TMP/logs" \
  AGENT_OPS_WALLCLOCK_S="${2:-30}" \
  bash "$KIT_DIR/scripts/heal_wrapper.sh"
}

lock_is_free() {
  python3 - "$TMP/heal.lock" <<'PY'
import fcntl, sys
with open(sys.argv[1], "a") as fh:
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
PY
}

wait_for() { # $1 = file; up to 10 s
  for _ in $(seq 1 100); do [ -e "$1" ] && return 0; sleep 0.1; done
  fail "timed out waiting for $1"
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
open_alert
run_wrapper
[ -f "$TMP/brain_ran" ] || fail "brain did not run on WORK"
rm -f "$TMP/brain_ran"

# D: a live run holds the lock -> a second run exits without starting the brain, however old the lock is
python3 - "$TMP/heal.lock" "$TMP/held" <<'PY' &
import fcntl, sys, time
fh = open(sys.argv[1], "a")
fcntl.flock(fh, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(30)
PY
HOLDER=$!
wait_for "$TMP/held"
touch -t 202001010000 "$TMP/heal.lock"
run_wrapper
[ ! -f "$TMP/brain_ran" ] || fail "brain ran although a live run held the lock"
kill "$HOLDER"; wait "$HOLDER" 2>/dev/null || true

# E: corrupt/unreadable config -> kill switch fails CLOSED (exit 78 by design, tolerated here)
printf 'this is not json\n' > "$TMP/config.json"
run_wrapper || true
[ ! -f "$TMP/brain_ran" ] || fail "brain ran despite corrupt config (kill switch must fail closed)"

# F: a crashed run leaves no stale lock: the lock file exists, nobody holds it -> the brain runs
mk_config true
run_wrapper
[ -f "$TMP/brain_ran" ] || fail "brain did not run after the last holder ended"
rm -f "$TMP/brain_ran"

# G: invalid wallclock budgets fall back to the default instead of disabling or tripping the hard kill
for bad in not-a-number 0; do
  run_wrapper "" "$bad"
  [ -f "$TMP/brain_ran" ] || fail "brain did not run with budget '$bad'"
  grep -q "AGENT_OPS_WALLCLOCK_S='$bad'.*falling back to 3600s" "$TMP/logs/heal.log" || fail "no fallback WARN for budget '$bad'"
  rm -f "$TMP/brain_ran"
done

# H: budget overrun ends the brain's whole process group, children included
set +e
run_wrapper "sleep 301 & echo \$! > $TMP/child; wait" 2
rc=$?
set -e
[ "$rc" = 70 ] || fail "overrun exit code $rc, expected 70"
child="$(cat "$TMP/child")"
! kill -0 "$child" 2>/dev/null || { kill "$child"; fail "a child of the brain survived the budget kill"; }
lock_is_free || fail "lock still held after the overrun"

# I: stopping the wrapper (SIGTERM) ends the brain first, then frees the lock
rm -f "$TMP/child"
AGENT_OPS_CONFIG="$TMP/config.json" AGENT_OPS_BRAIN_CMD="sleep 302 & echo \$! > $TMP/child; wait" \
  AGENT_OPS_LOCK_FILE="$TMP/heal.lock" AGENT_OPS_LOG_DIR="$TMP/logs" AGENT_OPS_WALLCLOCK_S=60 \
  bash "$KIT_DIR/scripts/heal_wrapper.sh" &
WRAPPER=$!
wait_for "$TMP/child"
kill -TERM "$WRAPPER"
set +e; wait "$WRAPPER"; rc=$?; set -e
[ "$rc" != 0 ] || fail "wrapper stopped by SIGTERM exited 0"
child="$(cat "$TMP/child")"
! kill -0 "$child" 2>/dev/null || { kill "$child"; fail "brain kept running after the wrapper was stopped"; }
lock_is_free || fail "lock still held after the wrapper was stopped"

# J: an unknown mode runs as shadow, never as anything that may act
mk_config true gated
run_wrapper "printf %s \"\$AGENT_OPS_MODE\" > $TMP/mode"
[ "$(cat "$TMP/mode")" = shadow ] || fail "mode 'gated' reached the brain as '$(cat "$TMP/mode")'"
grep -q "mode 'gated' is not shadow or auto" "$TMP/logs/heal.log" || fail "no WARN for unknown mode"

echo "SMOKE OK: kill switch (incl. fail-closed on corrupt config), IDLE gate, WORK run, OS lock (live holder, no stale lock), wallclock budget (validation, whole-group kill), stop signal, unknown mode"
