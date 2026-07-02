#!/usr/bin/env bash
# heal_wrapper.sh — safe launcher for a headless LLM "brain" (agent-ops-kit).
#
# Order of gates, each one cheap and deterministic:
#   1. kill switch   config "enabled": false  -> exit (a corrupt/unreadable
#      config does NOT count as disabled: fail-open + loud warning, because
#      a silently-dead healer is worse than a wasted run)
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
CONFIG="${AGENT_OPS_CONFIG:-$KIT_DIR/config/agent_ops.json}"
LOG_DIR="${AGENT_OPS_LOG_DIR:-$KIT_DIR/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/heal.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG" >&2; }

[ -f "$CONFIG" ] || { log "FATAL: config missing: $CONFIG"; exit 78; }

# --- gate 1: kill switch (fail-open on unreadable config, loudly) ---------
enabled="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("enabled", True))' "$CONFIG" 2>/dev/null)" \
  || { log "WARN: config unreadable -> treating as ENABLED (fail-open; fix the config)"; enabled="True"; }
if [ "$enabled" = "False" ]; then
  log "kill switch: enabled=false -> exit"
  exit 0
fi
MODE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mode", "shadow"))' "$CONFIG" 2>/dev/null || echo shadow)"

# --- gate 2: single-instance lock ------------------------------------------
LOCK_DIR="${AGENT_OPS_LOCK_DIR:-$KIT_DIR/state/heal.lock}"
LOCK_STALE_MIN="${AGENT_OPS_LOCK_STALE_MIN:-120}"
mkdir -p "$(dirname "$LOCK_DIR")"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +"$LOCK_STALE_MIN" 2>/dev/null)" ]; then
    log "stale lock (>${LOCK_STALE_MIN}m) -> taking over"
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
