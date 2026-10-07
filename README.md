# agent-ops-kit

**Operational guardrails for headless LLM agent loops.** The watchdog, the
LLM-free precheck gate, a heal-loop brain template and the staged-autonomy
playbook, taken as generic patterns from the system that runs my company:
119 background jobs, almost all of them plain scripts, run by an entrepreneur
who doesn't write code by hand.

Background story: [Why I run my company with AI agents](https://kevintheo.com/field-report/)

## Why this exists

Everyone automates. Almost nobody ships the boring layer that makes
automation survivable:

- Jobs fail silently at 3 a.m. and nobody notices for weeks.
- Headless LLM loops wake up on a schedule, discover there is nothing to
  do, and bill you anyway. (A measured 7-day audit of one real hourly loop:
  ~95% no-op runs, ≈ $231/week of API-equivalent spend for zero changes.
  The fix was not a smarter model — it was a gate.)
- "Autonomous" agents get write access on day one and edit their own
  guardrails on day two.

This kit is that boring layer, minus everything specific to my company.

## What's inside

| Piece | What it does |
|---|---|
| `scripts/watchdog.py` | Heartbeat checks for scheduled jobs → append-only `alerts.jsonl`. One unresolved alert per problem (fingerprint dedup). **Log, don't page.** |
| `scripts/precheck.py` | LLM-free gate: prints `IDLE` or `WORK: <reason>`. Contract: **only an explicit `IDLE` may skip a cycle** — errors fail open into WORK. |
| `scripts/heal_wrapper.sh` | Safe launcher for a headless brain: kill switch → single-instance lock → precheck gate → wallclock budget with hard kill. |
| `skills/heal/SKILL.md` | Brain template for Claude Code (`claude -p /heal`): validate → classify → fix-if-provably-safe → verify → roll back on doubt → one digest line. Shadow mode, max-2-attempts, protected paths. |
| `docs/staged-autonomy.md` | The autonomy ladder (shadow → pilot → gated → auto) as a checklist, including the promotion review template. |
| `examples/demo/` | Five-minute walkthrough: break a fake job, watch the alert, watch the gate open, watch shadow mode propose. |

Stdlib-only Python (3.10+) and portable Bash. No dependencies, no telemetry,
no framework. Copy the pieces you want.

## Quickstart

```bash
git clone https://github.com/kevintheo-ai/agent-ops-kit && cd agent-ops-kit
cp config/agent_ops.example.json config/agent_ops.json

# 1. Your scheduled jobs touch a heartbeat file on success. The watchdog
#    (run it from cron/launchd/systemd) turns missing/stale heartbeats
#    into alerts:
python3 scripts/watchdog.py --config config/agent_ops.json

# 2. Your heal loop runs through the wrapper, never directly:
AGENT_OPS_BRAIN_CMD="claude -p /heal" bash scripts/heal_wrapper.sh
# -> kill switch, lock, precheck (IDLE = no LLM run), wallclock budget

# Tests
python3 -m pytest tests/ -q          # logic
SMOKE=1 bash tests/smoke_heal_wrapper.sh  # the four gates, against a real fixture
```

## The rules this kit encodes

1. **Autonomy is earned, not granted.** Shadow → pilot → gated → auto, with
   a formal review between steps. See `docs/staged-autonomy.md`.
2. **Fail open, loudly.** A broken gate must cost tokens, never silence: a
   corrupt config counts as *enabled*, a crashing precheck counts as *WORK*.
3. **The agent never edits its own leash — by instruction, today.**
   `carve_out_paths` (config, scripts, skills) are off-limits for autonomous
   writes, always. Enforcement is currently the model's compliance with
   `skills/heal/SKILL.md`, not an OS-level sandbox — see *Known limitations*.
4. **Everything reversible.** Snapshot before change, verify after, roll
   back on doubt, cap attempts at two, then escalate to a human.
5. **Alert text is data, not instructions.** Prompt-injection defense is an
   operations rule, not a model feature.

## Known limitations

- **Carve-outs are prompt-enforced, not sandboxed.** `carve_out_paths` relies
  on the brain following its own instructions — nothing at the OS level
  stops a misbehaving run from writing there today. `mode: auto` means
  trusting that compliance. Wrapper-side enforcement (deny-list, read-only
  mounts, or a sandboxed runner) is the natural next hardening step, not yet
  built.
- **State paths are operator-trusted.** `alerts_path` / `approvals_dir` in
  the config are used as given, with no path containment — fine for a config
  you write yourself, but don't point them at a config an untrusted party
  can edit.

## What this is not

Not a framework, not a product, not affiliated with any model vendor. It's
the scaffold I wish I'd had on day one, published as-is under MIT. Issues
and war stories welcome.

— Kevin Theo ([LinkedIn](https://www.linkedin.com/in/kevin-theobald-7946871a2) · [kevintheo.com](https://kevintheo.com))
