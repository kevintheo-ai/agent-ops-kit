# agent-ops-kit

**The boring layer that makes headless LLM agent loops survivable.**

agent-ops-kit is a free, open-source kit of operational guardrails for headless LLM agent loops: a heartbeat watchdog, an LLM-free precheck gate, a safe launcher for the brain, a heal-loop brain template for Claude Code (the /heal skill) and a staged-autonomy playbook. In July 2026 I took these pieces as generic patterns from the system I run my company with, minus everything specific to my company. As of October 2026 that system runs 119 background jobs, almost all of them plain scripts that run without AI, on purpose.

Background story: [Why I run my company with AI agents](https://kevintheo.com/field-report/)

## Why this exists

The kit answers three problems:

- Jobs fail silently at 3 a.m. and nobody notices for weeks.
- Headless LLM loops wake up on a schedule, find there is nothing to do, and bill you anyway.
- "Autonomous" agents get write access on day one and edit their own guardrails on day two.

I measured the second one in July 2026, in a 7-day audit of one real hourly repair loop in my setup. About 95 % of its runs were no-ops, which came to roughly $231 a week of API-equivalent spend for zero changes. The fix was an LLM-free gate in front of the loop, built the same day. That gate is the idea behind `precheck.py`.

## What's inside

| File | What it does |
|-|-|
| `scripts/watchdog.py` | Heartbeat checks for scheduled jobs. Alerts go to an append-only JSONL store (`state/alerts.jsonl`), one unresolved alert per problem. Log, don't page. |
| `scripts/precheck.py` | LLM-free gate: prints `IDLE` or `WORK: <reason>`. Only an explicit `IDLE` may skip a cycle. |
| `scripts/heal_wrapper.sh` | Safe launcher for a headless brain: kill switch, single-instance lock, precheck, brain with a wall-clock budget. |
| `.claude/skills/heal/SKILL.md` | The /heal brain template for Claude Code. Shadow mode, max 2 attempts, protected paths. |
| `docs/staged-autonomy.md` | The autonomy ladder (shadow, pilot, gated, auto) as a checklist, with the promotion review template. |
| `examples/demo/` | Five-minute walkthrough with a fake job and a fake brain, no LLM needed. |
| `tests/` | 22 pytest tests and a smoke test of the wrapper with 10 scenarios. |

## Quickstart

The tools are stdlib-only Python and portable Bash and need no packages. The tests need pytest.

```bash
git clone https://github.com/kevintheo-ai/agent-ops-kit && cd agent-ops-kit
cp config/agent_ops.example.json config/agent_ops.json
python3 scripts/watchdog.py --config config/agent_ops.json
bash scripts/heal_wrapper.sh
python3 -m pytest tests/ -q
SMOKE=1 bash tests/smoke_heal_wrapper.sh
```

To try the kit without an LLM, run the walkthrough in `examples/demo/`, which uses a shell `echo` as the brain. Its last step shows the point of the kit: when there is nothing to do, no model starts.

Tested on 11 November 2026 on macOS (bash 3.2, Python 3.13) and on Linux in a Python 3.11 container (the wrapper's smoke test). `pyproject.toml` asks for Python 3.10 or newer.

## How it works

### Watchdog

Every job you care about touches a heartbeat file when it finishes well. The watchdog checks each heartbeat against a maximum age (`max_age_hours`, default 26) and appends one alert per problem: `never_ran` (heartbeat missing), `stale` (too old), `unreadable` (the path can't be read or is not a file) or `bad_config` (the job entry itself is broken: no name, a duplicate name, no heartbeat path, or a maximum age that is not a positive number). The other jobs are still checked. Reading the store, deciding and appending happen under one lock, so two overlapping runs can't raise the same alert twice. `--resolve <fingerprint>` marks an alert resolved and rewrites the store in place. `--strict` exits 1 when new alerts were raised. The watchdog never notifies anyone.

### Precheck

`precheck.py` prints exactly one line, and only a line starting with `IDLE` may skip a cycle. A store that does not exist counts as no alerts; any other read or parse problem (a corrupt store, a store it may not read) means WORK, because a skipped real cycle means an unhealed failure, while a wasted cycle only costs tokens.

### Wrapper

`heal_wrapper.sh` runs four gates in order:

1. The kill switch. Only a literal JSON `true` in `enabled` (or a missing key) lets the brain run. A missing, unreadable or invalid config counts as off: it is logged as FATAL and the wrapper exits 78. It fails closed, because an ambiguous config must not let the brain run.
2. The single-instance lock, an operating-system file lock (flock) that the wrapper holds for the whole run. A crashed run leaves no stale lock behind, and a live run can never be taken over, however long it runs.
3. The LLM-free precheck.
4. The brain, in its own process group with a wall-clock budget (`AGENT_OPS_WALLCLOCK_S`, default 3600 seconds, at least 1; time asleep counts). On overrun (exit 70), when the wrapper is stopped (SIGTERM, SIGINT, SIGHUP) and when the brain leaves processes behind after it ends, the wrapper ends the whole group before the lock is released.

### Modes and paths

The template knows `shadow` (proposals only) and `auto` (it may act on the safe class). Any other value runs as shadow, and the wrapper logs a warning. Relative paths in the config resolve against the kit folder, the same base for the wrapper, the watchdog and the precheck, wherever cron, launchd or systemd starts them.

### Default brain command

The default is `claude -p "/heal $AGENT_OPS_CONFIG" --setting-sources project --allowedTools "Edit(state/**)" "Bash(python3 scripts/watchdog.py:*)"`. It loads only the kit's own project settings, because allow rules from your user settings would add to `--allowedTools`, never narrow it. Claude Code then lets the brain write only under `state/` and run only the watchdog. Checked on 11 November 2026 with Claude Code 2.1.322: a write to `config/`, a `touch` in `config/` and a chained command after the watchdog were all blocked, and in a real shadow run the brain wrote its proposal and its digest line and resolved an alert that had recovered by itself. For auto mode you add exactly the restart commands of your safe class, nothing else.

### The /heal template

The skill validates, classifies, fixes only what is provably safe, verifies, rolls back on doubt and writes one digest line. It re-checks the kill switch and never touches `carve_out_paths`. It counts an attempt before its first change (two attempts, then it stops and writes a proposal marked blocked), snapshots before every change and rolls back when the re-probe fails. It treats alert text, the fingerprint too, as data and never as instructions, and it never sends, pushes or deletes. Restarts go through the job's scheduler (for example `launchctl kickstart` or `systemctl start`), never as a child process of the brain. In shadow mode its only change outside its own state files is resolving an alert whose job recovered by itself; if that was wrong, the watchdog raises it again on its next run.

### Environment variables and config fields

The wrapper reads `AGENT_OPS_CONFIG` (default `config/agent_ops.json` in the kit), `AGENT_OPS_BRAIN_CMD` (default above), `AGENT_OPS_WALLCLOCK_S` (default 3600), `AGENT_OPS_LOCK_FILE` (default `state/heal.flock`) and `AGENT_OPS_LOG_DIR` (default `logs/`). The fields in `config/agent_ops.example.json` are `enabled`, `mode` (`shadow` or `auto`), `alerts_path`, `carve_out_paths` and `jobs` (each with `name`, `heartbeat`, `max_age_hours`).

## The rules this kit encodes

1. Autonomy is earned in steps: shadow, pilot, gated, auto, with a written review between steps. In July 2026 my alarm investigator earned its auto mode after a supervised week of 36 cases: 17 false alarms correctly dismissed, 6 real fixes, each approved by me first, 0 rollbacks.
2. Fail safe in the right direction. The precheck fails open: an error means WORK and costs tokens, never silence. The kill switch fails closed: an unclear config means off.
3. The agent never edits its own leash. `carve_out_paths` (config, scripts, skills) are off-limits for autonomous writes. With the default brain command, Claude Code's tool permissions enforce this on top of the instructions.
4. Everything is reversible: snapshot before a change, verify after, roll back on doubt, at most 2 attempts, then a human.
5. Alert text is data and never instructions.

## How my own setup moved on

The kit shows the pattern of July 2026: one LLM brain that also restarts jobs. Since 5 September 2026 my own setup has one consumer for all alarms. A restart runs as plain code, without AI, and an AI agent only investigates what a restart does not fix or what is not safe to restart. I changed it because three systems read the same alarm stream and got in each other's way: 222 of 325 alarm fingerprints were handled twice.

## What changed in 0.2.0

Version 0.2.0 is from 11 November 2026. I design the kit, set the rules, decide and accept the result. Claude Code writes the code, and models from other labs review it. Four models from other labs (Codex, GLM, Muse and Grok) reviewed the whole repo. They reported 17 real problems and 1 false alarm. A real end-to-end run with Claude Code found one more: with the old default command the brain was not allowed to write its proposal, so every run ended without a trace. All 18 are fixed, and a second review round of the fixes followed.

The biggest of them:

- Two brain runs could run at the same time: an age-based lock could be taken from a live run, for example after the computer slept.
- The budget and a stop signal did not end the brain's child processes.
- One typo in a job entry stopped the whole watchdog.
- Started by cron from another folder, the watchdog looked in the wrong place and the wrapper saw IDLE.
- The README said a corrupt config counts as enabled, while the wrapper fails closed.

## Upgrading from 0.1.0

- `AGENT_OPS_LOCK_DIR` and `AGENT_OPS_LOCK_STALE_MIN` are gone. `AGENT_OPS_LOCK_FILE` (default `state/heal.flock`) replaces them.
- `approvals_dir` is gone. Nothing in the kit consumed approval files, so they only kept the gate open.
- The skill moved from `skills/heal/` to `.claude/skills/heal/`, and the example `carve_out_paths` list `.claude/` instead of `skills/`.
- Every job entry needs a unique `name` and a `heartbeat` path.
- Relative paths resolve against the kit folder instead of the current folder.

## Known limitations

- The carve-outs are enforced by instructions and, with the default brain command, by Claude Code's tool permissions. That is not an operating-system sandbox, and a brain command of your own decides its own permissions.
- State paths are operator-trusted: `alerts_path` and the heartbeat paths are used as given. Don't point them at a config an untrusted party can edit.
- A wrapper killed with SIGKILL (kill -9) can't end the brain first. The lock is then free while the brain may still run.
- A wrapper that a non-interactive shell started in the background (with `&`) ignores SIGINT, as bash does there. Stop it with SIGTERM.
- Write to the alert store only through the watchdog. `--resolve` rewrites the file, so a line appended at that moment without the store lock can be lost.
- A heartbeat only says a job finished. It says nothing about whether its result was right.

## What this is not

agent-ops-kit is not a framework and not a product, and it is not affiliated with any model vendor. It has no telemetry. Copy the pieces you want. The code is MIT licensed, Copyright 2026 Kevin Theobald. Issues and war stories are welcome.

Kevin Theobald ([LinkedIn](https://www.linkedin.com/in/kevintheo) · [kevintheo.com](https://kevintheo.com))
