# Five-minute demo

This walkthrough uses a fake job and a fake brain, so you don't need an LLM: the brain is a shell `echo`. Swap in the default command later.

```bash
cd "$(git rev-parse --show-toplevel)"
cp config/agent_ops.example.json config/agent_ops.json
mkdir -p state/heartbeats
touch state/heartbeats/weekly-report.json
touch -t 202606010000 state/heartbeats/nightly-export.json
python3 scripts/watchdog.py --config config/agent_ops.json
#   ALERT nightly-export:stale: heartbeat 3931.0h old (max 26.0h)
python3 scripts/watchdog.py --config config/agent_ops.json
#   OK: no new alerts
python3 scripts/precheck.py --config config/agent_ops.json
#   WORK: 1 unresolved alert(s)
AGENT_OPS_BRAIN_CMD='echo "brain: I would restart nightly-export now"' bash scripts/heal_wrapper.sh
cat logs/heal.log
python3 scripts/watchdog.py --config config/agent_ops.json --resolve nightly-export:stale
#   resolved 1 alert(s) for nightly-export:stale
python3 scripts/precheck.py --config config/agent_ops.json
#   IDLE
AGENT_OPS_BRAIN_CMD='echo should-not-run' bash scripts/heal_wrapper.sh
#   no actionable work -> brain not started (one LLM run saved)
```

The hours in the first ALERT line depend on the day you run the demo, because the stale heartbeat is dated 1 June 2026.

The last line is the whole economic argument of the kit: when there is nothing to do, no model starts.
