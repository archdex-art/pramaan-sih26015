# Six-slide script + demo walkthrough

For **`SIH2026_PRAMAAN_Idea_Submission_v2.pptx`** — the standard SIH template.

**Total speaking time: 5 minutes.** Then the demo: 4 minutes. Then questions.

Pronunciation: **PRAMAAN** = "pruh-MAAN" (प्रमाण). It means *proof*.

---

## ⚠ First: use the v2 deck

Slide 2 of the original showed the **old five-family weights**
(`PHOTO 0.15 · TERRAIN 0.25 · SATELLITE 0.25 · TEMPORAL+CONTROLS 0.25 ·
RAINFALL 0.10`) and called all five "independent".

Two things were wrong, and the second is serious:

1. **`control` is its own family**, not a suffix on `temporal`. It is the only
   family that separates the intervention from the weather.
2. **`photo` is not independent** — it is the claim's own source, which is
   exactly why it is weighted lowest. Listing it as independent inverts the
   argument.

The demo's **Method** panel reads the weights live from the running engine. Open
it with the old slide up and the numbers disagree on screen. Fixed in v2:

```
PHOTO 0.12 · TERRAIN 0.25 · SATELLITE 0.20 · TEMPORAL 0.20 · CONTROL 0.15 · CONTEXT 0.08
```

Sums to 1.00. Matches `/api/v1/method/weights` exactly.

---

## SLIDE 1 — Title *(25 s)*

> "Good morning. Problem Statement 26015 — using geospatial techniques to
> interpret geo-coded images and improve watershed development outcomes.
>
> Our solution is called **PRAMAAN**. It's Sanskrit for *proof*.
>
> One sentence: **we turn a geo-tagged field photograph into a claim that the
> satellite record can test — and we return a verdict with its confidence, its
> evidence, and everything that argues against it.**"

**Do:** say the name slowly, once. Then move.

---

## SLIDE 2 — Proposed Solution *(80 s)*

> "Start with the problem, because it isn't the one people expect.
>
> India has been remarkably good at **collection** — over a crore MGNREGA assets
> geo-tagged. Under WDC-PMKSY 2.0 there are **1,24,830** water-harvesting
> structures.
>
> But today each photograph is moderated **by eye** — an officer opens it and
> clicks accept or reject. **That verifies a photograph exists. It does not
> verify the structure works.** And one lakh twenty-four thousand structures
> cannot be eyeballed at scale.
>
> So PRAMAAN adds **one step** to DoLR's existing pipeline — after DRISHTI
> geo-tags, before SRISHTI moderation. We replace nothing.
>
> Every geotag becomes a testable claim, and **six evidence families** vote on
> it. Terrain from the elevation model, satellite indices at 30 metres, temporal
> trend, matched controls, rainfall context — and the photograph itself.
>
> Now the important detail: **five of those six are independent of the claim. The
> photograph is not** — it's the claim's own source. So it carries the **lowest**
> weight, 0.12. Independent evidence has to outrank self-report.
>
> Output is a verdict on a published **L0 to L4** ladder, with calibrated
> confidence and a **mandatory dissent panel**. Then a named officer accepts,
> edits or rejects, and that signature goes into an append-only ledger."

**The "photograph weighted lowest" answer is your best 10 seconds. Know it cold —
you will be asked.**

---

## SLIDE 3 — Technical Approach *(60 s)*

> "Python and FastAPI, Postgres with PostGIS, Celery workers, React front end.
> Deliberately boring choices.
>
> The geospatial stack is rasterio, GDAL, GeoPandas and **WhiteboxTools** for the
> hydrology.
>
> On AI — and this matters — we use a **zero-shot** vision-language model,
> SigLIP-2. Not because it's fashionable, but because **no labelled Indian
> watershed photograph corpus exists.** We probed ten possible sources to
> confirm that, and rejected every one on measured evidence.
>
> Data sources are all free and open: NASA HLS at 30 metres, Copernicus
> Sentinel-2, NRSC's Bhoonidhi API, Bhuvan's OGC services, NASADEM.
>
> Hardware: **one 8-core VM, no GPU.** We measured photo inference at 29 images
> per second on a laptop CPU.
>
> The one piece of architecture worth your attention: the **reconciliation
> engine is a pure function** — an evidence bundle in, a verdict out. No
> database, no clock, no randomness. That's what makes any verdict reproducible
> from its stored record, and I'll show you that in the demo."

---

## SLIDE 4 — Feasibility and Viability *(70 s)*

> "This is the slide we'd want to see if we were you.
>
> **Feasibility.** Every core data source is free, open and programmatic — and
> we **verified the endpoints rather than assuming them.** Eight probed live,
> results written into the repository including the failures. Zero rupees in data
> and licence cost.
>
> **Now the risks, honestly.**
>
> Many structures are **smaller than one 30-metre pixel**. A farm pond might be
> 625 square metres; a pixel is 900. So we compare footprint to sensor
> resolution **first**, and if it's below one pixel we switch off per-structure
> satellite evidence and escalate to a cluster. We would rather say *'we cannot
> see this'* than *'this does not exist'*.
>
> **Monsoon cloud** can destroy an entire kharif window. We measured it: on our
> demo area, kharif gives **zero to five** usable scenes a year against
> **23 to 34** for rabi. So rabi and summer carry the analysis, and cloud is
> scored per area of interest, not per scene.
>
> **Seasonality could be mistaken for impact.** That's what matched controls are
> for — same sub-watershed, same season, construction window excluded.
>
> And **a false negative could unfairly implicate a beneficiary.** So verdicts
> stay **provisional** until a named officer signs, and the strongest phrase the
> system can emit is *'requires physical verification'* — enforced by a linter
> in our build, not by a style guide.
>
> Finally: **we could not find a public SRISHTI–DRISHTI API. So we're not going
> to pretend we did.** We built to NRSC's published field schema, which makes
> integration a driver swap rather than a rewrite."

---

## SLIDE 5 — Impact and Benefits *(60 s)*

> "Watershed development already works. The ICRISAT meta-analysis across 636
> Indian micro-watersheds: benefit–cost ratio about 2, internal rate of return
> 27 per cent, cropping intensity up 35 per cent.
>
> **The constraint isn't whether it works. It's knowing which structures are
> working.**
>
> So the impact is redirecting scarce human attention. Of roughly 1,200 works in
> a district, some are assessable at 30 metres, a few hundred need a human
> decision, and a much smaller number need a field visit — **each with a
> documented reason.**
>
> That funnel is illustrative; the ratio is measured and reported per district,
> never assumed.
>
> Economically: **zero rupees** in data and licence cost, so the marginal cost of
> monitoring one more structure is CPU seconds — inside a ₹8,134 crore programme.
>
> Socially, the one I'd underline: **accountability without accusation.** The
> system never says fraud, and never says false. A named officer makes the
> finding.
>
> And institutionally — every officer correction becomes a labelled training
> sample. **The dataset is produced by the system's own use, by domain experts,
> for free.**"

---

## SLIDE 6 — Research and References *(25 s)*

> "Briefly, because it's a reading list.
>
> Two things I'd point at. First, the **WDC-PMKSY 2.0 Guidelines** already mandate
> NDVI/NDWI change detection and cross-verification of satellite images with
> ground interventions. We're implementing the Department's own written mandate,
> not selling a new idea.
>
> Second, everything here is traceable — the NRSC manuals, the Bhoonidhi API
> spec, NASA HLS, JRC Global Surface Water, the ICRISAT meta-analysis, and the
> Prithvi-EO foundation model as our research path.
>
> That's the idea. May I show you it running?"

---

# DEMO WALKTHROUGH — 4 minutes

## Before you start

```bash
cd pramaan
make demo-up      # ~15 s if images are built
make web
```

Open **<http://127.0.0.1:5173>** and leave it on the register.

Have a second terminal ready in `pramaan/`.

**If anything fails: "I'll show you the recorded run." Never debug live.**

---

## Step 1 — The register *(45 s)*

You are looking at a table of 24 claims.

> "This is the claims register. Twenty-four claims, and the level chips down the
> right show **all eight epistemic levels** — dark green at the top for Level 4,
> control-differenced, fading through grey, then amber and rust at the bottom for
> inconclusive and contradicted.
>
> Notice the colour is a **gradient, not a traffic light**. Level 1 is nearly grey
> because Level 1 nearly says nothing. You should feel the claim weakening before
> you read the label.
>
> Now the honest part. **One row says `MEASURED`.** That is the only claim in here
> computed from real imagery and a real elevation model. The other 23 are the
> golden test cases that gate every commit — synthetic inputs, engine-computed
> verdicts. They're here because they exercise all eight levels, and they're
> **badged** because a synthetic row must never be mistaken for a measurement."

**Do:** point at the `MEASURED` badge. Then click that row.

---

## Step 2 — The verdict *(60 s)*

> "One claim. A check dam, claimed complete November 2023, in Marathwada.
>
> **Level first, then confidence** — that ordering is deliberate. Level says how
> strongly a thing is known; confidence says how much of that level's evidence
> agreed. Confidence-first invites reading 0.06 as '6 per cent likely true',
> which is not what it means.
>
> Verdict: **N1, inconclusive. Confidence 0.06. Coverage 0.80** — four of six
> evidence families available."

**Do:** point at the small square diagram on the left.

> "This is the one I'd like you to look at. That's the **GPS uncertainty disk
> drawn to scale** against the 30-metre pixel grid, with the structure's expected
> footprint dashed around it.
>
> Two of our central claims are invisible as text and obvious as a picture. First,
> we never sample a single pixel — the disk visibly straddles pixel boundaries.
> Second, you can see immediately whether a structure is bigger or smaller than
> one pixel.
>
> On this claim, flow accumulation varied from **1 to 216 pixels across 15
> metres**. A single-pixel reading said 160. The disk median is 46. Sampling one
> pixel would have handed the rule engine a precision it hadn't earned."

---

## Step 3 — The evidence tree *(45 s)*

**Do:** click **Terrain** to expand it.

> "Six families, each with an arrow — agrees, neutral, disagrees, or unavailable.
>
> Terrain **disagrees, minus one**. And here's why, in the system's own words:
> Strahler stream order zero, 277 metres from any drainage line. **Implausible
> siting for a check dam.**
>
> That's arithmetic on an elevation model. No AI, no training data, and I can
> explain any single verdict to an auditor in one line."

**Do:** point at **Context — unavailable**.

> "And notice this one says **unavailable**, not zero. That distinction is
> load-bearing. Zero means 'we measured it and it's neutral'. Unavailable means
> 'we never got it', and it lowers coverage instead. Absence of evidence is not
> evidence of absence."

**Do:** point at the **rule path** at the bottom.

> "`N1_DEFAULT → conflicting_families → agreeing=temporal → disagreeing=terrain`.
>
> Terrain says this location cannot host a check dam. Temporal says vegetation
> grew. **The engine does not average those into a confident answer.** It names
> the conflict and stops."

---

## Step 4 — The dissent panel *(20 s)*

**Do:** point right.

> "Dissent, always shown, **never collapsible**. Everything arguing the other
> way. A verdict without stated counter-evidence isn't shippable in this system.
>
> And the adjudication buttons below are **deliberately disabled with the reason
> printed**: the append-only ledger table exists, the database already refuses
> UPDATE and DELETE to the application role — but the signing endpoint isn't
> built. So every verdict stays **PROVISIONAL**, which is the correct state."

---

## Step 5 — Temporal analysis *(50 s)* ← the payoff

**Do:** click **Temporal analysis**.

> "This is the chart that matters.
>
> Bold line is the site's winter-crop vegetation index. Forty cloud-screened NASA
> HLS granules, ten seasonal composites over five years. The hatched band is the
> construction period, which we exclude from both windows.
>
> The site rose **+0.116** across the claim date. **On its own, that reads as
> success** — and I want to be clear: a naive NDVI-difference dashboard reports
> success here.
>
> But the shaded ribbon is **twelve control sites**, matched on real elevation
> data — same slope within 2 degrees, same elevation within 5 metres, same
> distance to a stream — with no intervention. Same rainfall, same season.
>
> Those twelve rose a median **+0.090**. Our site sits at the **75th percentile of
> its own controls — inside the band.**
>
> So the differenced estimate is **+0.026**. **That is not a result, and we say
> so.**"

---

## Step 6 — Method *(30 s)*

**Do:** click **Method**.

> "Last thing. This panel is read from the **running engine**, not hardcoded in
> the interface.
>
> The full epistemic ladder. The ceiling is **L4, control-differenced** — and
> notice **L5, causal, is refused**: it isn't in the engine's level enum at all,
> so no code path can construct it. We will never tell you the check dam
> *caused* the change.
>
> And the six weights, with the reason each is weighted as it is. Terrain
> heaviest at 0.25 because it's independent and unaffected by cloud or season.
> Photograph lowest at 0.12 because it's the claim's own source.
>
> If someone changes a weight, this panel changes with it. **The interface cannot
> disagree with the code.**"

---

## Step 7 — The audit proof *(30 s, optional but strong)*

In the second terminal:

```bash
curl -s -X POST localhost:8000/api/v1/verdicts/1/recompute | python3 -m json.tool
```

> "This re-runs the frozen engine over that verdict's own stored record and gives
> back a hash. **`identical: true`** — byte-identical.
>
> That's what makes this usable as government evidence: an auditor can reproduce
> a decision made in 2026 when they look at it in 2031."

---

## Closing line

> "One lakh twenty-four thousand structures. Free satellite data. **And the
> officer still decides.**
>
> प्रमाण — PRAMAAN — proof. Thank you."

---

# THE FOUR LIKELY QUESTIONS

**"Is it all built?"**
> "No, and let me be precise. **Built and tested:** the engine, all six evidence
> producers, the database, the verdict API, the recompute proof, and the console
> you just saw — 458 tests, 100 per cent branch coverage on the deterministic
> core. **Scaffolded:** photo upload, access control, the adjudication signing
> endpoint, the Evidence Pack PDF. **Not started:** the photo model itself,
> because it needs a labelled dataset that doesn't exist."

**"Your one real claim came out inconclusive. Isn't that a failure?"**
> "It's the opposite. The vegetation genuinely rose — a system that wanted to look
> good would report success. Ours checked twelve matched neighbours, found they
> rose almost as much, and refused to claim anything. **A system that reports
> 30 per cent inconclusive is more trustworthy than one that reports 100 per cent
> conclusive.**"

**"Where's the AI? This looks like GIS."**
> "Fair, and deliberate. AI is one of six families and the **lowest weighted**.
> A government verdict has to be explainable, and terrain plausibility is
> arithmetic on an elevation model — I can explain any verdict in one line. Where
> we do use a model it's zero-shot, because no labelled Indian watershed photo
> corpus exists. We probed ten sources."

**"Why should we trust your numbers?"**
> "Don't — check them. Every number comes from a script in the repository you can
> re-run: `make series` rebuilds the satellite measurement, `make terrain` the
> elevation analysis, `make check` the test suite. And the failures are written
> down too — which government endpoints we couldn't reach, and the ten datasets
> we rejected with the reason for each."

---

# NEVER SAY

| Don't | Do |
|---|---|
| "fully working / production ready" | "engine and evidence layer built and tested; upload and reporting scaffolded" |
| "95 % accurate" or any invented figure | "not measured yet — here's what we did measure" |
| "the AI detects check dams" | "one of six families uses a vision model, weighted lowest" |
| "it proves the structure works" | "it reports whether the evidence is consistent with it working" |
| "5 independent evidence families" | "six families, five of them independent of the claim" |
| "the check dam caused the improvement" | "associated with — we cap below causal claims" |
| "we integrated with DRISHTI" | "we built to their published schema; no public API exists" |

**Don't know?** → *"I don't have that measured. I can tell you what we do have."*
Costs nothing. A bluff costs everything.

---

# 60-SECOND VERSION

> "PRAMAAN — Sanskrit for *proof*.
>
> India has geo-tagged over a crore of rural assets. Today an officer opens each
> photograph and clicks accept or reject. That verifies a photograph exists. It
> doesn't verify the structure works.
>
> We turn each photograph into a testable claim and check it against six
> independent evidence families — terrain, satellite, temporal, matched controls,
> rainfall, and the photo itself, weighted lowest because it's the claim's own
> source.
>
> The centrepiece is matched controls: we compare each structure to twelve sites
> in the same watershed, same slope, same rainfall, no intervention. **We measure
> the intervention, not the monsoon.**
>
> We ran it end to end on real satellite and elevation data. The verdict was
> **inconclusive** — the site improved no more than its neighbours. A naive
> dashboard would have called that success.
>
> Every verdict recomputes byte-identically from its stored record, and nothing
> becomes government evidence until a named officer signs it.
>
> One lakh twenty-four thousand structures. Free data. The officer still decides."
