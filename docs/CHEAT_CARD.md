# PRAMAAN — one-page cheat card

*Print this. Hold it. The 680-line script is for tonight; this is for the room.*

---

## THE THREE SENTENCES

1. **"India solved collection. It has not solved interpretation."**
2. **"We built the system that refuses to lie — and we can prove it refuses."**
3. **"Nothing becomes government evidence until a named human being says it does."**

---

## SLIDE CUES — one line each

| # | Slide | Say this, then move on |
|---|---|---|
| 1 | Title | "PRAMAAN — Sanskrit for *proof*. We turn a geo-tagged photo into a testable claim." |
| 2 | Problem | "1 crore assets geo-tagged. An officer clicks accept or reject. **That verifies a photo exists, not that the structure works.**" |
| 3 | Ecosystem | "We insert **one arrow**. Nothing replaced. And WDC-PMKSY 2.0 **already mandates** this analysis." |
| 4 | Solution | "Five independent families. **Photo weighted lowest — it's the claim's own source.**" |
| 5 | Workflow | "One workflow built completely, not ten built partially." |
| 6 | Architecture | "The engine has two connections: bundle in, verdict out. No DB, no clock, no randomness." |
| 7 | Mechanism | "625 m² pond, 900 m² pixel. Gate fails, satellite evidence switches **off**, visibly." |
| 8 | Science | "Three ideas, **none of them deep learning.** A system that refuses to answer is one you can trust when it does." |
| 9 | **RESULTS** | **See box below. Slow down here.** |
| 10 | Novelty | "Not claiming a first. Claiming these five haven't been assembled before. **And we checked.**" |
| 11 | Impact | "Watershed development already works. The constraint is knowing **which** structures work." |
| 12 | Feasibility | "No public DRISHTI API. **So we're not going to pretend we did.** Built to their published schema." |
| 13 | Closing | "The officer decides. The system shows its work." |

---

## ⭐ SLIDE 9 — SAY IT IN THIS ORDER

1. "One real claim. Real data. Not a mock-up."
2. "40 NASA HLS granules, 10 seasonal composites, five years, real elevation model."
3. "Winter-crop NDVI rose **+0.116** across the claim date. **On its own that reads as success** — a naive dashboard would report success here."
4. "We matched **12 control sites** on real terrain. They rose a median **+0.090**."
5. "Our site is at the **75th percentile of its own controls — inside the band.** Differenced: **+0.026**. That is not a result."
6. "And terrain says the point is **277 m from any drainage line, stream order zero** — implausible for a check dam."
7. "Verdict: **INCONCLUSIVE. Confidence 0.06.**"
8. "Terrain says impossible, vegetation says something grew. **The engine does not average them. It names the conflict and stops.**"
9. **"Five of these metrics say *not measured*. I would rather tell you which numbers don't exist than show you numbers I can't defend."**
10. **STOP TALKING.**

---

## NUMBERS — only these, and they're all real

| | |
|---|---|
| Structures under WDC-PMKSY 2.0 | **1,24,830** · ₹8,134 cr |
| Our verdict | **N1 INCONCLUSIVE · confidence 0.0615 · coverage 0.80** |
| Site rise / control median | **+0.1157 / +0.0901** → differenced **+0.026**, 75th percentile |
| Terrain | Strahler **0** · **277 m** from drainage → implausible |
| Matched controls | **12** of **342** candidates |
| Tests | **458** · **100 %** branch coverage · **23** golden cases |
| Engine speed | **~13 µs** per verdict · 1.24 lakh in **under 2 s** |
| Imagery | **1.8 GB** for 5 years (naive would be 78 GB) |
| Photo model | **29 img/s on CPU** · **no GPU** |
| Sources probed | **8** endpoints · **10** datasets rejected |
| Data cost | **₹0** |

**Not measured (say it plainly):** adjudication-time A/B · photo P/R · ECE · terrain precision.

---

## THE THREE IDEAS — if asked, answer in one line

**Matched controls** → *"We don't measure whether it got greener. We measure whether it got greener than its neighbours who got the same monsoon."*

**Terrain plausibility** → *"Arithmetic on an elevation map. No AI, no training data, explainable to an auditor in one line."*

**Detectability gate** → *"Absence of evidence is not evidence of absence. If we can't see it, we don't say it isn't there."*

---

## TOP 4 QUESTIONS

**"Is it all built?"**
> "Built and tested: engine, all six evidence producers, database, verdict API, recompute proof, temporal screen, access control, the append-only ledger, the alert queue — 556 tests. Not built: upload, the Evidence Pack PDF. Not started: the photo model — it needs a dataset that doesn't exist."

**"Inconclusive means it failed?"**
> "The opposite. Vegetation genuinely rose. A system that wanted to look good would report success. Ours checked 12 matched neighbours and refused. **A system that reports 30 % inconclusive is more trustworthy than one that reports 100 % conclusive.**"

**"Where's the AI?"**
> "One of six families, weighted lowest, deliberately. A government verdict has to be explainable. And it's zero-shot — no training data — because no labelled Indian watershed photo corpus exists. We probed ten sources."

**"Why should we trust your numbers?"**
> "Don't — check them. `make series` rebuilds the satellite measurement, `make terrain` the elevation analysis, `make check` the tests. The failures are written down too."

---

## NEVER SAY

~~"fully working"~~ · ~~"95 % accurate"~~ · ~~"the AI detects check dams"~~ · ~~"proves it works"~~ · ~~"confidence 0.84"~~ · ~~"the dam caused it"~~ · ~~"we integrated with DRISHTI"~~

**Don't know?** → *"I don't have that measured. I can tell you what we do have."* Costs nothing. A bluff costs everything.

---

## IF THEY WANT A DEMO

```
make test-db      # 39 tests: writes a verdict, recomputes it byte-identically
make check        # 458 tests, 100 % coverage
```

While it runs: *"It applies every migration from scratch, rolls them all back and reapplies them — a migration that only works forwards can't be shipped to a district — then writes a verdict and recomputes it from its own stored record. Same hash means byte-identical. An auditor can reproduce a 2026 decision in 2031."*

**If it fails: "I'll show you the recorded run." Never debug live.**

---

## LAST LINE

> "One lakh twenty-four thousand structures. Free satellite data. **And the officer still decides.**
> प्रमाण — PRAMAAN — proof. Thank you."
