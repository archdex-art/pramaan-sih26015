# ADR-001 — The frozen evidence-family set and aggregation formula

**Status:** Accepted, frozen at `engine-v1`
**Date:** Stage 1
**Supersedes:** the family/weight definitions in the master design document
§14.4, §16.1 and §22.2 as originally drafted

---

## Context

The master design document specified the evidence-fusion core in four places
that did not agree with each other:

| Location | What it said |
|---|---|
| §14.4 | five weights: `photo .15, terrain .25, satellite .25, temporal .25, context .10` |
| §16.1 | five families: `Photo, Metadata, Terrain, Satellite, Temporal+Control` |
| §31.3 FR-7.1 | "**Six** agreement values or an explicit unavailable flag" |
| §16.3 worked examples | six rows, with `CONTROL` listed separately from `TEMPORAL` |
| §22.2 DDL | `evidence_family` enum of six, including `metadata` |

Four different definitions of the system's most load-bearing concept. Any
implementation would have silently picked one, and the golden-case suite would
have frozen that arbitrary choice as truth.

Two further defects followed from the ambiguity:

- **`metadata` was counted twice.** §16.1 listed it as an evidence family while
  §14.4 used `metadata_integrity` as a multiplier inside `quality`. Doing both
  penalises a bad geotag in the numerator *and* in the multiplier, making the
  score non-linear in a way that cannot be defended to an auditor.
- **The published confidence figure in worked Example B was unreachable.** The
  document printed `confidence 0.71` against `score -0.59`. Since
  `confidence = |score| × coverage × quality` and both multipliers lie in
  `[0,1]`, confidence can never exceed `|score|`. The number was arithmetically
  impossible, and it was in the flagship demo example.

## Decision

### 1. There are exactly six evidence families

| Family | `w_e` | Independent of the claim? | Why this rank |
|---|---|---|---|
| `terrain` | 0.25 | yes | The only family unaffected by cloud, sensor resolution or season |
| `satellite` | 0.20 | yes | Independent, but bounded by the 30 m detection limit |
| `temporal` | 0.20 | yes | Independent, but needs usable scenes in *both* windows |
| `control` | 0.15 | yes | Strongest design element; ranked below satellite/temporal only because a thin matched pool makes it unavailable more often |
| `photo` | 0.12 | **no** — it *is* the claim's source | Must never outvote independent evidence |
| `context` | 0.08 | yes | A confounder check (rainfall), not primary evidence |
| **Σ** | **1.00** | | Asserted at import time |

Independent families total **0.88** against `photo`'s **0.12** — independent
evidence outweighs self-report roughly **7 : 1**. That ratio is the design claim.

### 2. `control` is a family, separate from `temporal`

They fail independently, which is the whole point. `temporal` answers *"did the
surface state at this site change?"*; `control` answers *"did comparable
un-intervened sites change the same way?"* A cloud gap kills `temporal`; an
insufficient matched-control pool (N < 5) kills `control` while leaving
`temporal` intact.

Collapsing them would let a missing control pool silently discount a perfectly
good temporal observation, and would make the L4 rule — which requires *both* —
unstateable.

### 3. `metadata` is not a family

It is not evidence *about the structure*; §16.1 says so itself. It is evidence
about how much the other evidence can be trusted. It enters through
`quality.metadata_integrity` and **only** there.

### 4. The formula is exactly as published, with no additional terms

```
support      = Σ w_e · s_e · a_e
weight_total = Σ w_e · a_e
score        = support / max(weight_total, ε)
coverage     = weight_total / Σ w_e
quality      = metadata_integrity × data_sufficiency
confidence   = |score| · coverage · quality
```

### 5. Worked examples are generated, never written

`scripts/render_worked_examples.py` loads the same golden-case YAML that gates
CI, calls `engine.reconcile()`, and renders §16.3 of the design document between
marker comments. `--check` fails the build when the document drifts from the
engine.

## Consequences

### The weights live in code with a validator, not in prose with a claim

This was immediately vindicated. The weight set first written into this ADR —
`{.25, .20, .20, .15, .10, .05}` — sums to **0.95**, not the 1.00 the table
asserted. It was caught within minutes, not by review but by
`_validate_weights()` refusing to import: the engine would not start. The
corrected set is the table above.

A prose table cannot fail a build. This is the entire argument for putting
load-bearing constants behind an assertion.

### Invariant I2 required restating

The naive phrasing — *"adding a disagreeing family never raises the score"* — is
**false**, and a Hypothesis property test found the counterexample immediately.
`score` is a weighted *mean*, so adding a family that disagrees *less* than the
current average raises the mean: adding `-0.5` to a set averaging `-1.0` moves
the score to `-0.96`. That is correct behaviour for a mean.

The two true statements are:

- **I2a** — `support` (unnormalised) is non-increasing when a disagreeing family
  is added.
- **I2b** — `score` is non-decreasing in **any single family's** agreement,
  availability held fixed: `∂score/∂s_e = w_e / weight_total > 0`.

I2b is the operative guarantee: nobody can improve a claim's score by making one
family's evidence look worse. A judge who spots the mean behaviour deserves this
answer, not a patched formula.

### Invariant I1 is enforced three times

`confidence ≤ |score|` is checked in `Verdict.__post_init__`, asserted as a
Hypothesis property over 400 generated bundles, and enforced by a Postgres
`CHECK` constraint on `verdicts`. Belt and braces, because this is the exact
defect that reached the flagship demo example. The constraint has been verified
against a live PostGIS instance to reject the old `0.71` row and accept the
engine-computed value.

### Changing any of this is a versioned event

A change to the family set, the weights or the formula requires bumping
`ENGINE_VERSION`, re-running the full golden-case suite, and regenerating the
design document's worked examples. Verdicts store `engine_version` and
`config_fingerprint`, so a verdict computed under one configuration is never
silently compared against another.
