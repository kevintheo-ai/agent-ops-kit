# Five-minute demo

Watch the whole loop work against a fake job — no LLM needed (the "brain"
is a shell echo; swap in `claude -p /heal` later).

```bash
cd "$(git rev-parse --show-toplevel)"
cp config/agent_ops.example.json config/agent_ops.json

# 1. Simulate a job that ran fine yesterday... and then stopped.
mkdir -p state/heartbeats
touch state/heartbeats/weekly-report.json           # fresh -> healthy
touch -t 202606010000 state/heartbeats/nightly-export.json   # ancient -> stale

# 2. Watchdog: turns the stale heartbeat into exactly one alert.
python3 scripts/watchdog.py --config config/agent_ops.json
#   ALERT nightly-export:stale: heartbeat ...h old (max 26h)
python3 scripts/watchdog.py --config config/agent_ops.json
#   OK: no new alerts        <- dedup: same problem never alerts twice

# 3. Precheck: the gate is open because there is real work.
python3 scripts/precheck.py --config config/agent_ops.json
#   WORK: 1 unresolved alert(s)

# 4. Wrapper: run the loop with a fake brain (shadow-style, zero cost).
AGENT_OPS_BRAIN_CMD='echo "brain: I would restart nightly-export now"' \
  bash scripts/heal_wrapper.sh
cat logs/heal.log

# 5. Pretend the fix worked and close the loop.
python3 scripts/watchdog.py --config config/agent_ops.json --resolve nightly-export:stale

# 6. The gate closes: next cycle costs nothing.
python3 scripts/precheck.py --config config/agent_ops.json
#   IDLE
AGENT_OPS_BRAIN_CMD='echo should-not-run' bash scripts/heal_wrapper.sh
#   log: "no actionable work -> brain not started (one LLM run saved)"
```

That last line is the entire economic argument of this kit.
