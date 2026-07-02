# Staged autonomy — the ladder

Autonomy is a privilege a subsystem earns with evidence, never a launch
setting. Four rungs, one direction, and a formal review between each rung.

```
shadow  ->  pilot  ->  gated  ->  auto
(propose    (real data,  (every action  (narrow, defined
 only)       tiny scope)  needs a tap)   action class only)
```

## Rung definitions

**Shadow.** The system runs its full loop against real inputs but executes
nothing. Every action it *would* take is written to a proposals directory.
You read them. Weeks of "would have been correct" build the case.

**Pilot.** Real execution on a deliberately tiny, low-blast-radius slice
(one job, one mailbox, one campaign). Everything else stays shadow.

**Gated.** Full scope, but every action requires an explicit human approval
(a button tap counts; silence never does).

**Auto.** Only for a *named, closed list* of action classes that are all
three of: safe (no data loss possible), confident (validated by a re-probe,
not by model output), reversible (snapshot + rollback exist). Everything
outside the list stays gated — permanently.

## Promotion review (template)

Run this review before every rung change. Write the answers down; the
review that isn't written down didn't happen.

- Observation window: ____ days, ____ total cases.
- False-alarm handling: ____ of ____ correctly dismissed (target: all).
- Real actions: ____ proposed / ____ approved / ____ correct in hindsight.
- Rollbacks needed: ____ (target: 0; each one gets a written post-mortem).
- Incidents during window: ____ → each converted into a written rule? y/n
- Kill switch tested this window (actually flipped, observed to stop)? y/n
- Carve-out zone violation attempts: ____ (target: 0, verified from logs).
- Decision: promote / hold / demote — by WHOM (a human), dated.

Real-world reference point: the newest autonomous subsystem in my own
production setup earned its auto mode with a supervised week of 36 cases —
17 false alarms correctly dismissed, 6 real fixes (each human-approved),
0 rollbacks. That's the kind of receipt a promotion should have.

## Demotion is normal

Any incident that survives to production demotes the subsystem one rung
until the class of failure is structurally prevented (a test, a gate, a
rule — not a promise). Demotion is not punishment; it's how the ladder
stays meaningful.
