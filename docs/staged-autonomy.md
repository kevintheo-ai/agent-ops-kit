# Staged autonomy: the ladder

An agent earns autonomy with evidence. It is never a launch setting. The usual failure is an agent that gets write access on day one and edits its own guardrails on day two. So there are four rungs, you climb them in this order, and a written review sits between every two of them.

shadow  ->  pilot  ->  gated  ->  auto

A human decides each move and dates the decision.

## Rung definitions

- Shadow: the loop runs on real inputs and executes nothing. Every action it would take is written down as a proposal.
- Pilot: real execution on a deliberately tiny slice. With this kit, set `mode` to `auto` and give the config one small job. In the allowed tools of the brain command, add exactly the restart commands of your safe class, nothing else. Restarts go through the job's scheduler (for example `launchctl kickstart` or `systemctl start`), never as a child process of the brain. The carve-outs are enforced by the /heal instructions and, with the default brain command, by Claude Code's tool permissions. That is not an operating-system sandbox; a brain command of your own decides its own permissions.
- Gated: full scope, but every action needs an explicit human approval. Silence never counts as approval. With this kit, set `mode` to `shadow` and apply each proposal yourself. In shadow mode the brain's only change outside its own state files is resolving an alert whose job recovered by itself. If that check was wrong, the watchdog raises the alert again on its next run.
- Auto: only for a named, closed list of action classes. Each class must be safe (no data loss possible), confident (validated by a re-probe, never by what the model says) and reversible (a snapshot and a rollback exist). Everything else stays gated.

## Promotion review (template)

Fill this in, on paper or in a file, before every rung change. A human decides and signs it.

- Subsystem and rung change: ____ to ____
- [ ] Observation window and number of cases: ____
- [ ] False alarms correctly dismissed: ____
- [ ] Actions proposed: ____
- [ ] Actions approved: ____
- [ ] Actions correct in hindsight: ____
- [ ] Rollbacks (target 0), each with a post-mortem: ____
- [ ] Incidents turned into written rules: ____
- [ ] Kill switch actually tested (date and result): ____
- [ ] Carve-out violation attempts, checked in the logs (target 0): ____
- Decision (promote, hold or demote): ____
- Decided by and date: ____

## A reference point from July 2026

In July 2026 my alarm investigator earned its auto mode. Before that it ran a supervised week of 36 cases: 17 false alarms correctly dismissed, 6 real fixes, each one approved by me first, and 0 rollbacks. That is one setup and one week, so take it as one data point and write your own thresholds into the review.

## Demotion is normal

An incident that reaches production moves the subsystem one rung down. It stays there until a test, a gate or a rule prevents that class of failure. A promise to be more careful doesn't count. After that you run the promotion review again.
