# Anti-slop repair heuristics

Slop is the gap between code's apparent delivery value and its demonstrated
lifecycle value. A working happy path can still impose recurring change cost.

## Diagnose the emitting system

Before fixing repeated findings, ask:

1. What change or policy caused these symptoms to repeat?
2. Which boundary should own that decision?
3. What behavior must stay invariant during the repair?
4. Is the proposed abstraction used by real callers, or only imagined ones?
5. What targeted test would fail if the root cause returned?

Prefer one root-cause cluster over many symptom edits when evidence shows a
shared cause. Prefer the local edit when occurrences are coincidental or a
shared abstraction would add coupling.

## Prioritize interest, not principal alone

Move work earlier when it:

- changes often or blocks several planned changes;
- has broad blast radius, hidden side effects, or security implications;
- obscures ownership or forces readers to relearn a convention;
- prevents meaningful tests or makes failures hard to diagnose;
- is a prerequisite for safely removing several downstream symptoms.

Issue count and raw lines changed are weak prioritization signals.

## Minimize net complexity

A repair is simpler when it reduces the concepts, paths, states, or conventions
a maintainer must hold at once. Count the cost of new files, configuration,
indirection, generic types, extension points, and compatibility branches.

Do not add a layer for a single implementation unless it enforces a real policy,
isolates a volatile dependency, or creates a proven test seam. Delete obsolete
machinery when behavior proof makes removal safe.

## Preserve behavior at seams

For structural work, pin observable behavior where modules hand off:

- input/output and error contracts;
- authorization and validation decisions;
- ordering, idempotency, and retry behavior;
- persistence and migration semantics;
- public imports, CLI flags, and configuration compatibility.

Test outcomes, not the refactor's private shape.

## Resist metric theater

Never improve a score by hiding authored code, mass-suppressing findings,
splitting code solely to satisfy thresholds, manufacturing low-value tests, or
renaming without resolving misleading behavior. Record intentional debt and the
reason it remains. A strict-score decrease can be honest progress if a better
blind review exposes real debt; explain it and continue from evidence.
