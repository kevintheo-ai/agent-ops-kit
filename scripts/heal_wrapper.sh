#!/usr/bin/env bash
# heal_wrapper.sh: safe launcher for a headless LLM "brain" (agent-ops-kit).
#
# Order of gates, each one cheap and deterministic:
#   1. kill switch   only a literal JSON `true` in "enabled" (or a missing key)
#      lets the brain run; a missing, unreadable or invalid config counts as
#      OFF (fail CLOSED, loudly: an ambiguous config must not let the brain run)
#   2. single-instance lock   an OS file lock (flock) that this wrapper holds
#      for the whole run; a crashed run leaves no stale lock behind, and a live
#      run can never be taken over, however long it runs
#   3. LLM-free precheck   ONLY an explicit "IDLE" line skips the cycle
#   4. brain run in its own process group with a wallclock budget; on overrun,
#      or when this wrapper is stopped (SIGTERM, SIGINT, SIGHUP), the whole
#      group ends before the lock is released (a wrapper that a non-interactive
#      shell started with `&` ignores SIGINT, as bash does; stop it with SIGTERM)
#
# Configure via environment:
#   AGENT_OPS_CONFIG       path to config JSON (default: <kit>/config/agent_ops.json)
#   AGENT_OPS_BRAIN_CMD    command to run       (default: DEFAULT_BRAIN_CMD below)
#   AGENT_OPS_WALLCLOCK_S  brain budget seconds (default: 3600)
#   AGENT_OPS_LOCK_FILE / AGENT_OPS_LOG_DIR
set -u

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Run from the kit root: relative paths in the config resolve against it, the
# same base watchdog.py and precheck.py use (cron/launchd/systemd rarely set a
# predictable cwd).
cd "$KIT_DIR" || { echo "FATAL: cannot cd into kit dir: $KIT_DIR" >&2; exit 78; }
CONFIG="${AGENT_OPS_CONFIG:-$KIT_DIR/config/agent_ops.json}"
LOG_DIR="${AGENT_OPS_LOG_DIR:-$KIT_DIR/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/heal.log"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG" >&2; }

[ -f "$CONFIG" ] || { log "FATAL: config missing: $CONFIG"; exit 78; }

# --- gate 1: kill switch (fail CLOSED on unreadable/ambiguous config) -----
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
case "$MODE" in
  shadow|auto) ;;
  *) log "WARN: mode '$MODE' is not shadow or auto -> running as shadow"; MODE=shadow ;;
esac

# --- gate 2: single-instance lock (released by the OS when this process ends) --
LOCK_FILE="${AGENT_OPS_LOCK_FILE:-$KIT_DIR/state/heal.flock}"
mkdir -p "$(dirname "$LOCK_FILE")"
if ! { exec 9>>"$LOCK_FILE"; } 2>/dev/null; then
  log "FATAL: cannot open lock file: $LOCK_FILE"
  exit 78
fi
python3 -c 'import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(3)'
case $? in
  0) ;;
  3) log "another run holds the lock -> exit"; exit 0 ;;
  *) log "FATAL: cannot take the lock: $LOCK_FILE"; exit 78 ;;
esac

# Signals from here on: end the brain (once it runs) before this wrapper exits.
BRAIN_PID=""
# End the brain's whole process group: TERM, then KILL whatever is left after 2 s.
stop_brain() {
  # A signal can land between the brain's start and BRAIN_PID=$!; $! holds its PID then.
  if [ -z "$BRAIN_PID" ]; then set +u; BRAIN_PID=$!; set -u; fi
  [ -n "$BRAIN_PID" ] || return 0
  kill -TERM -- "-$BRAIN_PID" 2>/dev/null || return 0
  for _ in 1 2; do
    sleep 1
    kill -0 -- "-$BRAIN_PID" 2>/dev/null || return 0
  done
  kill -KILL -- "-$BRAIN_PID" 2>/dev/null || true
}
# shellcheck disable=SC2329  # called by the traps below
on_signal() { log "wrapper stopped by signal -> ending the brain if it runs"; stop_brain; exit "$1"; }
trap 'on_signal 143' TERM
trap 'on_signal 130' INT
trap 'on_signal 129' HUP

# --- gate 3: LLM-free precheck (only explicit IDLE suppresses) --------------
PRECHECK_OUT="$(python3 "$KIT_DIR/scripts/precheck.py" --config "$CONFIG" 2>>"$LOG" 9>&- || true)"
log "precheck: ${PRECHECK_OUT:-<empty output>}"
case "$PRECHECK_OUT" in
  IDLE*)
    log "no actionable work -> brain not started (one LLM run saved)"
    exit 0
    ;;
esac

# --- gate 4: brain in its own process group, with a wallclock budget --------
# Default brain: the /heal skill in .claude/skills, allowed to write under
# state/ and to run the watchdog (for --resolve), nothing else. It loads only
# the kit's project settings: allow rules from your user settings would add to
# --allowedTools, never narrow it.
# shellcheck disable=SC2016  # $AGENT_OPS_CONFIG expands later, inside `bash -c`
DEFAULT_BRAIN_CMD='claude -p "/heal $AGENT_OPS_CONFIG" --setting-sources project --allowedTools "Edit(state/**)" "Bash(python3 scripts/watchdog.py:*)"'
BRAIN_CMD="${AGENT_OPS_BRAIN_CMD:-$DEFAULT_BRAIN_CMD}"
BUDGET_S="${AGENT_OPS_WALLCLOCK_S:-3600}"
if ! [ "$BUDGET_S" -ge 1 ] 2>/dev/null; then
  log "WARN: AGENT_OPS_WALLCLOCK_S='$BUDGET_S' is not a positive whole number -> falling back to 3600s"
  BUDGET_S=3600
fi
log "starting brain (mode=$MODE, budget=${BUDGET_S}s): $BRAIN_CMD"
set -m # job control: the brain gets its own process group
AGENT_OPS_MODE="$MODE" AGENT_OPS_CONFIG="$CONFIG" bash -c "$BRAIN_CMD" >>"$LOG" 2>&1 9>&- &
BRAIN_PID=$!
set +m

start=$SECONDS # wall clock, so time spent asleep counts against the budget
while kill -0 "$BRAIN_PID" 2>/dev/null; do
  if [ $((SECONDS - start)) -ge "$BUDGET_S" ]; then
    log "wallclock budget exceeded -> ending the brain's process group"
    stop_brain
    exit 70
  fi
  sleep 1
done
wait "$BRAIN_PID"
rc=$?
if kill -0 -- "-$BRAIN_PID" 2>/dev/null; then
  log "brain left processes behind -> ending them"
  stop_brain
fi
log "brain exited rc=$rc"
exit "$rc"
