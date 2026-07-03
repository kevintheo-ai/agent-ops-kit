#!/usr/bin/env bash
# heal_wrapper.sh — safe launcher for a headless LLM "brain" (agent-ops-kit).
#
# Order of gates, each one cheap and deterministic:
#   1. kill switch   config "enabled": false OR corrupt/unreadable config
#      -> exit (fail CLOSED, loudly: an ambiguous config must not let the
#      brain run — a wasted cycle costs nothing, an unwanted one costs trust)
#   2. single-instance lock (mkdir, portable; stale takeover after N minutes)
#   3. LLM-free precheck   ONLY an explicit "IDLE" line skips the cycle
#   4. brain run with wallclock budget (kill on overrun)
#
# Configure via environment:
#   AGENT_OPS_CONFIG       path to config JSON (default: <kit>/config/agent_ops.json)
#   AGENT_OPS_BRAIN_CMD    command to run       (default: claude -p /heal)
#   AGENT_OPS_WALLCLOCK_S  brain budget seconds (default: 3600)
#   AGENT_OPS_LOCK_DIR / AGENT_OPS_LOG_DIR / AGENT_OPS_LOCK_STALE_MIN
set -u

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Run from the kit root so config-relative state paths (e.g. "state/alerts.jsonl")
# resolve the same way regardless of the caller's cwd (cron/launchd/systemd
# rarely set a predictable cwd) — codex-scan QF3.
cd "$KIT_DIR" || { echo "FATAL: cannot cd into kit dir: $KIT_DIR" >&2; exit 78; }
CONFIG="${AGENT_OPS_CONFIG:-$KIT_DIR/config/agent_ops.json}"
LOG_DIR="${AGENT_OPS_LOG_DIR:-$KIT_DIR/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/heal.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG" >&2; }

[ -f "$CONFIG" ] || { log "FATAL: config missing: $CONFIG"; exit 78; }

# --- gate 1: kill switch (fail CLOSED on unreadable/ambiguous config) -----
# Only a literal JSON `true` (or a missing key, which defaults true) may
# enable the brain. Any parse error or non-boolean value is treated as
# disabled — loudly logged, never silent. codex-scan QF1.
if ! enabled="$(python3 -c 'import json,sys
v = json.load(open(sys.argv[1])).get("enabled", True)
print("True" if v is True else "False")' "$CONFIG" 2>/dev/null)"; then
  log "FATAL: config unreadable/invalid JSON -> kill switch fails CLOSED: $CONFIG"
  exit 78
fi
if [ "$enabled" != "True" ]; then
  log "kill switch: enabled is not true -> exit (fail-closed)"
  exit 0
fi
MODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mode", "shadow"))' "$CONFIG" 2>/dev/null || echo shadow)"

# --- gate 2: single-instance lock ------------------------------------------
LOCK_DIR="${AGENT_OPS_LOCK_DIR:-$KIT_DIR/state/heal.lock}"
LOCK_STALE_MIN="${AGENT_OPS_LOCK_STALE_MIN:-120}"
mkdir -p "$(dirname "$LOCK_DIR")"
acquire_lock() { mkdir "$LOCK_DIR" 2>/dev/null; }
if ! acquire_lock; then
  if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +"$LOCK_STALE_MIN" 2>/dev/null)" ]; then
    log "stale lock (>${LOCK_STALE_MIN}m) -> taking over"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    # Re-acquire after takeover — codex-scan QF2: the old code fell through
    # to the brain launch without ever holding the lock, so the exit trap
    # below would later rmdir a lock this process never owned.
    if ! acquire_lock; then
      log "lock takeover raced with another run -> exit"
      exit 0
    fi
  else
    log "another run holds the lock -> exit"
    exit 0
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

# --- gate 3: LLM-free precheck (only explicit IDLE suppresses) --------------
PRECHECK_OUT="$(python3 "$KIT_DIR/scripts/precheck.py" --config "$CONFIG" 2>>"$LOG" || true)"
log "precheck: ${PRECHECK_OUT:-<empty output>}"
case "$PRECHECK_OUT" in
  IDLE*)
    log "no actionable work -> brain not started (one LLM run saved)"
    exit 0
    ;;
esac

# --- gate 4: brain with wallclock budget ------------------------------------
BRAIN_CMD="${AGENT_OPS_BRAIN_CMD:-claude -p /heal}"
BUDGET_S="${AGENT_OPS_WALLCLOCK_S:-3600}"
case "$BUDGET_S" in
  ''|*[!0-9]*)
    log "WARN: AGENT_OPS_WALLCLOCK_S='$BUDGET_S' is not a positive integer -> falling back to 3600s (codex-scan QF8)"
    BUDGET_S=3600
    ;;
esac
log "starting brain (mode=$MODE, budget=${BUDGET_S}s): $BRAIN_CMD"
AGENT_OPS_MODE="$MODE" AGENT_OPS_CONFIG="$CONFIG" bash -c "$BRAIN_CMD" >>"$LOG" 2>&1 &
BRAIN_PID=$!

elapsed=0
while kill -0 "$BRAIN_PID" 2>/dev/null; do
  if [ "$elapsed" -ge "$BUDGET_S" ]; then
    log "wallclock budget exceeded -> killing brain"
    kill "$BRAIN_PID" 2>/dev/null || true
    sleep 2
    kill -9 "$BRAIN_PID" 2>/dev/null || true
    exit 70
  fi
  sleep 5
  elapsed=$((elapsed + 5))
done
wait "$BRAIN_PID"
rc=$?
log "brain exited rc=$rc"
exit "$rc"
