# PRAMAAN — Presentation Script

**Read this once tonight. Read the bold lines twice. You do not need to understand
every technical detail to present this well — you need to understand three ideas
and be honest about the rest.**

Pronunciation: **PRAMAAN** = "pruh-MAAN" (प्रमाण). It means *proof* / *evidence*.
Say it slowly the first time and let it land — the name is the whole argument.

---

## PART 0 — DO THIS BEFORE YOU SLEEP

### 0.1 Fix Slide 9. It currently shows numbers we cannot defend.

Slide 9 says **"CORROBORATED · L4 · confidence 0.84"** and **"REQUIRES
VERIFICATION · N3 · confidence 0.71"**.

Those are **design examples**, not measurements. Two problems:

1. `0.71` is **arithmetically impossible** under our own formula. It was a bug in
   an early draft. The engine now computes **0.3371** for that case. If a mentor
   recomputes it, you lose the room.
2. We have **no real corroborated structure**. The one claim we ran end-to-end on
   real satellite and real terrain data came out **INCONCLUSIVE**.

**Replace the Slide 9 body with this — it is stronger, not weaker:**

> **ONE REAL CLAIM, RUN END TO END ON REAL DATA**
>
> Check dam · claimed complete 20-Nov-2023 · Marathwada
> **N1 · INCONCLUSIVE · confidence 0.0615 · coverage 0.80**
>
> - Satellite: 40 cloud-screened NASA HLS granules, 10 seasonal composites
> - Temporal: rabi NDVI **+0.1157** across the claim date — looks like success
> - **Controls: 12 sites matched on real DEM slope, elevation and stream
>   distance rose a median +0.0901. The site is at the 75th percentile —
>   inside the band. Differenced estimate +0.026.**
> - Terrain: Strahler order 0, 277 m from any drainage line → **implausible
>   siting for a check dam**
>
> **The engine refuses to resolve the conflict.** Terrain says this location
> cannot host a check dam; vegetation says something grew. It does not average
> them into a confident answer. `rule_path: N1_DEFAULT → conflicting_families`
>
> A naive NDVI dashboard reports success here.

And for the metrics block at the bottom of Slide 9, write exactly this:

| Metric | Value |
|---|---|
| Adjudication time reduction | **not measured** — A/B test not yet run |
| Photo model P/R per label | **not measured** — no labelled corpus exists yet (see Slide 12) |
| Calibration error (ECE) | **not measured** |
| Terrain screen precision | **not measured** — no reference set obtainable |
| Engine throughput | **~13 µs per verdict** · 1.24 lakh structures in **under 2 s** |
| Test suite | **458 tests · 100 % branch coverage · 23 golden cases** |

**Saying "not measured" five times on a slide is the single most powerful thing
in this deck.** Every other team will have invented numbers. You will be the only
one who marked the gaps. Lead into it with the line in §2.9.

### 0.2 Have these open in tabs, in this order

1. The deck
2. A terminal in `pramaan/` (for the two live commands in §3)
3. `docs/13-terrain.md` (the measurement write-up — your evidence file)
4. `docs/11-feasibility.md`

### 0.3 The three sentences to memorise

If you remember nothing else, say these:

1. **"India solved collection. It has not solved interpretation."**
2. **"We built the system that refuses to lie — and we can prove it refuses."**
3. **"Nothing becomes government evidence until a named human being says it does."**

---

## PART 1 — THE THREE IDEAS YOU MUST ACTUALLY UNDERSTAND

You will be asked about these. Learn them as stories, not as jargon.

### Idea 1 — Matched controls ("compare like with like")

**The problem:** A check dam is built. Next year the crops nearby are greener.
Did the dam do that — or did it just rain more?

**The answer:** Find 12 other places in the *same* small watershed that have the
*same* slope, the *same* elevation, the *same* distance to a stream — but where
**nothing was built**. They got the same rain. If our site improved *more* than
those 12, the dam did something. If they all improved equally, it rained.

**Our real result:** our site improved +0.116. The 12 matched sites improved
+0.090. **Not enough of a difference to claim anything.** So we don't.

> Say it like this: *"We don't measure whether it got greener. We measure whether
> it got greener than its neighbours who got the same monsoon."*

### Idea 2 — Terrain plausibility ("can water even reach it?")

**The problem:** Someone claims a check dam at a GPS point. Is that point even a
place a check dam could work?

**The answer:** A check dam must sit **on a drainage line** — water has to flow
into it. From the elevation map (DEM) we compute, for every 30 m pixel, how much
land drains into it and whether it sits on a channel. If the claimed point is on
a ridge with no water flowing in, we flag it **before running any AI at all**.

**Our real result:** the demo site is **277 metres from the nearest channel**,
with **Strahler order 0** — meaning not on a channel at all. Terrain says
**implausible**. No machine learning involved. Pure geometry from the elevation
data.

> Say it like this: *"This one is just arithmetic on an elevation map. No AI, no
> training data, and I can explain any single verdict to an auditor in one line."*

### Idea 3 — The detectability gate ("can the camera even see it?")

**The problem:** Free satellite imagery has 30-metre pixels. One pixel covers
900 m². A small farm pond might be 25 m × 25 m = **625 m²** — *smaller than one
pixel*.

**The answer:** Before we look at any imagery, we compare the structure's
expected size to the pixel size. If it's below one pixel, we **switch off**
per-structure satellite evidence and say so — visibly, in the interface and in
the report. We then try to assess a *cluster* of nearby structures instead.

**Why this wins the room:** *"Absence of evidence is not evidence of absence."* If
we can't see it, we must not report "not found" as "does not exist".

> Say it like this: *"A system that refuses to answer is one you can trust when
> it does."* (This line is already on Slide 8. Deliver it and pause.)

---

## PART 2 — SLIDE-BY-SLIDE SCRIPT

Total target: **8 minutes** of speaking. Timings are in brackets. If you are
running late, cut Slide 6 and Slide 10 — they are the most compressible.

---

### SLIDE 1 — Title *(30 s)*

> "Good morning. I'm [name], presenting **PRAMAAN** — Sanskrit for *proof*.
>
> Problem Statement 26015 asks us to interpret geo-coded images to improve
> watershed development outcomes.
>
> In one sentence: **PRAMAAN turns a geo-tagged field photograph into a
> machine-testable claim, checks that claim against satellite and terrain
> evidence, and gives an officer a verdict with its confidence, its evidence,
> and everything that argues against it.**"

**Do:** Point at the three panels — photo, satellite, verdict. Left to right.
**Don't** read the slide aloud. They can read.

---

### SLIDE 2 — The Problem *(60 s)*

> "India has been extraordinarily successful at **collection**. Over one crore
> MGNREGA assets are geo-tagged. Under WDC-PMKSY 2.0, more than **1,24,830**
> water harvesting structures, against an outlay of **₹8,134 crore**.
>
> But look at what happens to each photograph. On NRSC's SRISHTI portal, an
> officer opens it and clicks one of two buttons — **accept** or **reject**.
>
> That's it. Blue or red. **No outcome, no confidence, no evidence, no trend, and
> no record of why.**
>
> And here's the honest problem: that click verifies **a photograph exists**. It
> does not verify **the structure works**.
>
> One lakh twenty-four thousand structures is far past the point where any human
> being can eyeball an outcome."

**Pause after "It does not verify the structure works."** That is the thesis.

---

### SLIDE 3 — The Ecosystem *(45 s)*

> "This matters: **we are not replacing anything.**
>
> DoLR already has DRISHTI for geo-tagging, SRISHTI for moderation, and Bhuvan
> for imagery. The pipeline is plan → implement → geo-tag → moderate → evaluate.
>
> We insert **one arrow**, between geo-tag and moderate. Nothing else changes.
>
> And we're not inventing the requirement. The WDC-PMKSY 2.0 Guidelines
> **already mandate** this analysis — they say change detection should be used
> for NDVI and NDWI, and that cross-verification of satellite images with
> ground interventions should enable multiple levels of authentication.
>
> Those two sentences are already government policy. **Nobody has built the
> system that does them at scale.** That's what we built."

> **This is your strongest political slide.** You are implementing the
> Department's own written mandate, not selling them an idea.

---

### SLIDE 4 — The Solution *(75 s)*

> "Every photograph becomes a testable claim, and then five **independent**
> families of evidence vote on it.
>
> **Terrain** — flow accumulation, stream order, slope. From the elevation map.
> Deterministic, no AI. Weight 0.25.
>
> **Satellite** — vegetation and water indices at 30 metres. Weight 0.25.
>
> **Temporal** — the same season, year on year, against matched control sites.
> Weight 0.25.
>
> **Context** — rainfall anomaly, land cover. Weight 0.10.
>
> And **photo** — what the field actually sent us. Weight **0.15 — the lowest**.
>
> Now, why is the photograph weighted lowest? **Because it is the claim's own
> source.** Independent evidence must outrank self-report. If the only thing
> agreeing with a claim is the photograph submitted with the claim, that is not
> corroboration.
>
> And every verdict carries a **dissent panel** — everything that argues the
> other way. Always shown. Never collapsible."

**The "why is the photograph weighted lowest" answer is a 10-second answer that
proves you thought about it. Expect to be asked. Know it cold.**

---

### SLIDE 5 — The Killer Workflow *(45 s)*

> "One deliberate decision: **one workflow built completely, rather than ten
> built partially.**
>
> Select a micro-watershed → ingest the geo-tags → interpret the photo →
> reconcile the evidence → an officer adjudicates → and out comes an Evidence
> Pack with every scene ID, model version and weight that was used.
>
> Everything in the product serves those six steps. Anything that didn't, we
> didn't build."

**Honesty note for you:** step 4 (reconcile) and step 6 (the record — the
append-only, hash-chained ledger, signed by a named officer) are **built and
tested**, as is access control and the priority alert queue. Steps 1–3 and the
Evidence Pack PDF are **not built** — `app/services/ingestion` and
`app/services/reports` are empty packages, and calling them "scaffolded" would
be generous. If asked *"is all six built?"* — see §4, Question 1. **Do not
claim all six.**

---

### SLIDE 6 — Architecture *(30 s — cuttable)*

> "Boring where it should be, careful where it matters. Postgres with PostGIS,
> Python, Celery workers, React.
>
> The one thing worth your attention is the amber core — the reconciliation
> engine. It has exactly **two** connections: an evidence bundle in, a verdict
> out. **No database, no network, no clock, no randomness.**
>
> That's enforced by a test that walks the code and fails the build if the engine
> imports anything it shouldn't. It has caught me twice."

---

### SLIDE 7 — The Reconciliation Mechanism *(45 s)*

> "Inside the engine. Follow the branch on the right — the one that **removes**
> an arrow.
>
> A 25 by 25 metre farm pond is 625 square metres. One 30-metre pixel is 900
> square metres. **The structure is smaller than the pixel.**
>
> So the detectability gate fails, and per-structure satellite evidence is
> **switched off** — visibly, in the interface and in the PDF. The claim escalates
> to a cluster assessment instead.
>
> We would rather return *'we cannot see this'* than *'this does not exist'*."

---

### SLIDE 8 — Why It Holds Up Scientifically *(75 s)*

> "Three ideas do the work here, and **none of them is deep learning.**
>
> **Matched controls.** Every structure is scored against 5 to 12 automatically
> matched sites in the same sub-watershed — same slope, same elevation, same
> stream distance, no intervention. Same rainfall, same season. **We measure the
> intervention, not the monsoon.**
>
> **Terrain plausibility.** A check dam claimed on a ridge with almost no water
> flowing into it gets flagged *before any model runs*, from elevation data
> alone. Deterministic. Zero training data. Explainable to an auditor in one line.
>
> **The detectability gate.** Footprint versus sensor resolution, checked first.
>
> **A system that refuses to answer is one you can trust when it does.**
>
> And AI appears in exactly **one** of the six families — a zero-shot
> vision-language model. Not because it's fashionable, but because **no labelled
> Indian watershed photograph corpus exists.** We checked ten sources. I'll come
> back to that."

**Pause after the "refuses to answer" line.** Longest pause in the talk.

---

### SLIDE 9 — Demo & Results *(90 s — YOUR MOST IMPORTANT SLIDE)*

Use the replacement content from §0.1. Deliver it like this:

> "Here is one real claim, run end to end on real data. Not a mock-up.
>
> Forty cloud-screened NASA HLS granules. Ten seasonal composites over five
> years. A real elevation model — six NASADEM tiles.
>
> The site's winter-crop vegetation index rose **+0.116** across the claim date.
> On its own, **that reads as success**, and I want to be clear: a naive
> NDVI-difference dashboard would report success here.
>
> But we matched **12 control sites** on real terrain — slope within 2 degrees,
> elevation within 5 metres, stream distance within 8 metres. Those 12 rose a
> median **+0.090**. Our site sits at the **75th percentile of its own controls
> — inside the band.**
>
> So the differenced estimate is **+0.026**. That is not a result. And the
> terrain check says this location is **277 metres from any drainage line**,
> stream order zero — **implausible siting for a check dam.**
>
> Verdict: **INCONCLUSIVE.** Confidence **0.06**.
>
> And notice *why*: terrain says this cannot be a working check dam, vegetation
> says something grew. **The engine does not average those into a confident
> answer.** It names the conflict and stops.
>
> Now — the metrics at the bottom. Five of them say **'not measured'.** That is
> deliberate. We have not run the adjudication-time A/B test. We have no photo
> model accuracy, because we have no labelled corpus yet. **I would rather stand
> here and tell you which numbers don't exist than show you numbers I can't
> defend.**"

**That last sentence is your winning line. Say it slowly. Then stop talking.**

---

### SLIDE 10 — Novelty *(45 s — cuttable)*

> "What is genuinely new, and what is not.
>
> **Not new, and we don't pretend otherwise:** NDVI change detection. Mapping
> geotags on imagery. GIS dashboards. Computer vision on photographs.
> Treated-versus-control watershed studies. All of it exists.
>
> **New in this combination:** claim-level reconciliation on a published
> epistemic ladder. Matched-control differencing **per individual structure**,
> not per programme. Terrain screening of geotags *before* any model runs. An
> explicit refusal gate tied to sensor resolution. And an append-only
> adjudication ledger that becomes the next training set.
>
> We are not claiming a first. We're claiming these five have not been assembled
> into one government-workflow-native system. **And we checked.**"

---

### SLIDE 11 — Impact *(45 s)*

> "Watershed development already works. The ICRISAT meta-analysis across 636
> Indian micro-watersheds: benefit-cost ratio about 2, internal rate of return
> 27 per cent, cropping intensity up 35 per cent, runoff down 45 per cent.
>
> **The constraint isn't whether it works. It's knowing which structures are
> working.**
>
> So the impact is redirecting scarce human attention: of 1,200 works in a
> district, some are assessable at 30 metres, a few hundred need a human
> decision, and a much smaller number need a field visit. **That funnel is what
> we produce** — and the ratio is measured and reported, never assumed.
>
> And the data cost is **zero**. Every source on the core path is free, open and
> verified. The marginal cost of monitoring one more structure is CPU seconds."

---

### SLIDE 12 — Feasibility & Honesty *(60 s)*

> "This slide is here because it's the one we'd want to see if we were you.
>
> We could not find a public SRISHTI–DRISHTI API. **So we're not going to pretend
> we did.** We built to NRSC's *published field schema* instead — which makes
> integration a configuration change on the day the Department grants access,
> rather than a rewrite. **One driver class.**
>
> Everything else on the core path is verified and open: NASA HLS at 30 metres,
> Copernicus Sentinel-2, Bhuvan's OGC services, JRC Global Surface Water,
> NASADEM. We probed **eight endpoints live** and wrote the results — including
> the failures — into the repository.
>
> And we went looking for a labelled photograph dataset. **Ten sources probed.
> None usable.** OpenStreetMap returns **five** check dams for all of India.
> Mapillary: we pulled twelve frames from our districts and every one was
> highway dashcam footage — no structures at all. We rejected both, on measured
> evidence, and wrote down why.
>
> **No GPU needed.** Photo inference measured at **29 images per second on this
> laptop's CPU** — and we re-ran the arithmetic for a demo machine five times
> slower, which still gives about **6 per second**. Either way a 1,200-image
> corpus is minutes, not hours, and there is no GPU in the requirement."

---

### SLIDE 13 — Closing *(45 s)*

> "The officer decides. The system shows its work.
>
> A verdict is **PROVISIONAL** until a named officer accepts, edits or rejects
> it. That signature goes into an append-only, hash-chained ledger — and that
> permission is enforced by the **database**, not by our code. The application's
> user has INSERT and SELECT. It cannot UPDATE or DELETE a signed decision even
> if we asked it to.
>
> PRAMAAN caps itself **below causal claims** — enforced in code, printed on
> every report. We will never tell you the check dam *caused* the change.
>
> It implements two written mandates from the Department's own guidelines. It
> adopts the government's own IDs, roles and statuses. It runs on free, verified
> data.
>
> One lakh twenty-four thousand structures. Free satellite data. **And the
> officer still decides.**
>
> प्रमाण — PRAMAAN — proof. Thank you."

---

## PART 3 — TWO LIVE COMMANDS (only if they ask for a demo)

Practise these twice tonight. If they fail, say *"I'll show you the recorded
run"* and move on. **Never debug live.**

### Command 1 — the audit-defensibility proof *(the impressive one)*

```
make test-db
```

Say while it runs:

> "This spins up a real PostGIS database, applies every migration from scratch,
> **rolls them all back and reapplies them** — because a migration that only
> works forwards can't be shipped to a district — and then runs 39 tests that
> write a verdict through the database and recompute it from its own stored
> record."

When it prints `39 passed`:

> "The important one is the recompute. We store the exact inputs of every
> verdict. Point the endpoint at any verdict and it re-runs the frozen engine and
> gives you back a hash. **Same hash means byte-identical.** That's what makes
> this usable as government evidence — an auditor can reproduce a decision from
> 2026 in 2031."

### Command 2 — the full test suite *(fast, safe)*

```
make check
```

> "458 tests, 100 per cent branch coverage on the deterministic core, strict type
> checking across 43 files, and a check that the worked examples printed in our
> design document are still reproducible from the engine — so the document can't
> drift from the code.
>
> There's also a linter that fails the build if the system ever emits accusatory
> language. It can say *'requires physical verification'*. It can never say
> *'fraud'* or *'false'*. **The strongest phrase in the system is chosen, not
> accidental.**"

---

## PART 4 — HARD QUESTIONS, WITH ANSWERS

Read these twice. **The honest answer is always the winning answer here.**

### Q1. "Is the whole system built? Can I use it today?" ⚠️ MOST LIKELY QUESTION

> "No, and let me be precise about what is and isn't.
>
> **Built, tested and running:** the reconciliation engine, all six evidence
> producers, the database schema with partitioning, the verdict API, the
> recompute proof, the temporal analysis screen, role-based access control, the
> append-only adjudication ledger, and the priority alert queue. 556 tests, 100
> per cent branch coverage on the deterministic core and on the ledger.
>
> **Not built:** the photo upload pipeline and the Evidence Pack PDF. The
> database tables and the contracts exist; the code doesn't. I'd rather say not
> built than scaffolded — `app/services/ingestion` is an empty package.
>
> **Not started:** the photo model itself, because it needs a labelled dataset
> that doesn't exist. That's our next two weeks."

### Q2. "Your demo says inconclusive. Doesn't that mean it failed?"

> "It means the opposite. The site's vegetation genuinely rose — a system that
> wanted to look good would have reported success. Ours checked 12 matched
> neighbours, found they rose almost as much, and concluded the change isn't
> distinguishable from the surrounding area.
>
> **A system that reports 30 per cent inconclusive is more trustworthy than one
> that reports 100 per cent conclusive.** And note our engine can produce all
> eight verdict levels — we have 23 golden test cases proving it, including both
> paths to a contradicted verdict. This particular claim is inconclusive because
> the evidence is."

### Q3. "Where's the AI? This sounds like plain GIS."

> "That's a fair reading, and it's deliberate. AI is one of six evidence
> families, and it's the **lowest-weighted** one.
>
> The reason is that a government verdict has to be explainable. Terrain
> plausibility is arithmetic on an elevation model — I can explain any verdict in
> one line. If we'd made a neural network the centre of this, nobody could audit
> it.
>
> Where we do use AI, it's a zero-shot vision-language model — it needs no
> training data — because **no labelled Indian watershed photograph corpus
> exists.** We probed ten sources to confirm that. It runs on CPU at 29 images
> per second, so there's no GPU in our requirement."

### Q4. "How do you know your matched controls are valid?"

> "They're matched on measured terrain, not assumed. From a real elevation model
> we compute slope, elevation, distance to stream and stream order for every
> candidate pixel, then apply eight rules — including that a control must be at
> least 250 metres from *any* intervention, not just this one.
>
> In our run, 342 candidates went in, **12** came out. 276 were rejected for
> stream-distance mismatch, 11 for slope. Every rejection is counted by reason
> and stored with the verdict.
>
> What we **don't** have yet is land-cover and soil matching — Bhuvan publishes
> those as a map service, not a downloadable layer. So those two fields are
> explicitly marked *unknown* and the matcher treats them as uninformative rather
> than as agreeing."

### Q5. "What if the GPS coordinate is wrong?"

> "We never sample a single pixel. Every terrain variable is read as a
> distribution over an **uncertainty disk** — radius is the larger of the
> recorded GPS accuracy and 15 metres.
>
> This matters more than it sounds. In our real run, flow accumulation varied
> from 1 to 216 pixels **across 15 metres** — a two-hundred-fold spread, because
> the site sits beside a channel edge. A single-pixel reading said 160. The disk
> median is 46. **Single-pixel sampling would have handed the rule engine a
> precision it hadn't earned.**"

### Q6. "Can this scale to 1.24 lakh structures?"

> "The decision layer is free: **about 13 microseconds per verdict**, so all
> 1,24,830 structures reconcile in **under two seconds**. One district of 1,200
> works is 16 milliseconds.
>
> The real cost is fetching imagery, and we measured that exactly rather than
> estimating. Reading only the pixels we need costs **1.4 per cent** of a
> satellite band at site scale and **9.5 per cent** at sub-watershed scale.
> Whole-district composites cost 73.7 per cent — so we don't build them. The
> demo corpus is **1.8 gigabytes** for five years of imagery."

### Q7. "How is this different from what NRSC already does?"

> "SRISHTI shows an officer a photograph and takes a yes or no. It's a
> **moderation** tool and a good one.
>
> We add interpretation: is this location hydrologically capable of hosting this
> structure? Did the surface actually change? Did it change *more than comparable
> land that got the same rain*? What's the confidence, and what argues against
> this verdict?
>
> We consume Bhuvan's layers rather than re-deriving them. **We're a layer, not
> a replacement.**"

### Q8. "You claim you never make causal claims. Why not?"

> "Because we can't, honestly. Our ceiling is **Level 4 — control-differenced**.
> That means: the change is present at the site and absent at matched controls.
> That's strong evidence of association.
>
> **Attribution** to the intervention would need a designed evaluation with
> field measurement. So the cap is enforced in code and printed on every report.
>
> NDVI rising near a check dam does not prove the check dam caused it. Saying so
> is the difference between evidence and advocacy."

### Q9. "What's your biggest risk?"

> "Collecting the labelled photographs. We need roughly 600 to 800 field photos,
> labelled, with GPS intact, to calibrate the photo model.
>
> We went looking for an existing corpus and rejected every candidate on measured
> evidence — Mapillary gave us highway dashcam frames, OpenStreetMap has five
> check dams for the whole country, one dataset was licence-blocked.
>
> So our team has to shoot them. That's a people problem, not a code problem, and
> it's why it's our next two weeks rather than something I'm claiming today."

### Q10. "Why should we trust your numbers?"

> "Don't trust them — check them. Every number in this deck comes from a script
> in the repository that you can re-run. `make series` rebuilds the satellite
> measurement. `make terrain` rebuilds the elevation analysis. `make check` runs
> the test suite.
>
> The failures are written down too — `docs/09` records which government
> endpoints we probed and which ones we couldn't reach; `docs/10` records the ten
> datasets we rejected and why.
>
> And where a number doesn't exist, the slide says **not measured**."

---

## PART 5 — NEVER SAY THESE

| Don't say | Say instead |
|---|---|
| "It's fully working / production ready" | "The engine, evidence layer, access control, ledger and alert queue are built and tested; the upload and reporting layers are not built." |
| "Accuracy is 95 %" or any invented figure | "Not measured yet — here's what we did measure." |
| "The AI detects check dams" | "One of six evidence families uses a vision model, weighted lowest." |
| "It proves the structure works" | "It reports whether the evidence is consistent with it working, with confidence." |
| "confidence 0.84" (the old Slide 9 number) | The real numbers in §0.1. |
| "The check dam caused the improvement" | "Associated with. We cap below causal claims." |
| "We integrated with DRISHTI/SRISHTI" | "We built to their published schema. No public API exists." |

**If you don't know an answer:** *"I don't have that measured. I can tell you
what we do have, or I can find out and follow up."* That answer costs you
nothing. A bluff costs you everything.

---

## PART 6 — THE 60-SECOND VERSION

If they cut you to one minute:

> "PRAMAAN — Sanskrit for *proof*.
>
> India has geo-tagged over a crore of rural assets. Today an officer opens each
> photograph and clicks accept or reject. That verifies a photograph exists. It
> doesn't verify the structure works.
>
> We turn each photograph into a testable claim and check it against five
> independent evidence families — terrain, satellite, temporal, context, and the
> photo itself, weighted lowest because it's the claim's own source.
>
> The centrepiece is matched controls: we compare each structure to 12 sites in
> the same watershed with the same slope and the same rainfall and no
> intervention. **We measure the intervention, not the monsoon.**
>
> We ran it end to end on real satellite and elevation data. The verdict was
> **inconclusive** — because the site improved no more than its neighbours. A
> naive dashboard would have called that success.
>
> Every verdict can be recomputed byte-identically from its stored record, and
> nothing becomes government evidence until a named officer signs it.
>
> One lakh twenty-four thousand structures. Free data. The officer still
> decides."

---

## PART 7 — TOMORROW MORNING CHECKLIST

- [ ] Slide 9 replaced with the real numbers from §0.1
- [ ] The three sentences in §0.3 said out loud, from memory
- [ ] The three ideas in Part 1 explained out loud to someone, without notes
- [ ] `make check` run once, successfully
- [ ] Q1, Q2 and Q3 answered out loud — these three are near-certain
- [ ] Water. Slides on a USB stick **and** in email. Laptop charged.

**Final note.** Every other team will tell the mentors their system works. You
are going to tell them where it doesn't, and show them the measurements that
prove you looked. In a room full of confident demos, **the team that marked its
own gaps is the team that gets believed.**

Deliver the "not measured" line on Slide 9 with a straight back. It's the best
thing in this deck.
