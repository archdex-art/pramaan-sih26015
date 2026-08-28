# 14 — UI/UX design system and screen plan

## The one-line direction

**The interface should look like a government evidence record, not a SaaS
dashboard.**

Everything below follows from that. PRAMAAN's entire argument is *"this output is
defensible; take it to an auditor"*. An interface that looks like a startup
analytics product undercuts that argument before a single number is read. An
interface that looks like a survey document reinforces it.

Aesthetic name: **Survey Record**.

---

## 1. Grounding

Queried `ui-ux-pro-max` for stack `react`, product "government evidence audit
dashboard", dials `--variance 3 --motion 3 --density 8`.

| Recommendation | Decision | Reason |
|---|---|---|
| Pattern: *Real-Time / Operations Landing*, CTA "Start trial / Contact" | **Rejected** | Misroute. This is an internal console for a named officer, not a marketing page. There is no trial and no signup. |
| Style: *Exaggerated Minimalism* — high contrast, negative space | **Adopted in part** | Keep the restraint and contrast. Reject `font-size: clamp(3rem, 10vw, 12rem)` and `font-weight: 900` — fashion-brand scale on a district officer's screen is noise. |
| Colour: blue `#1E40AF` primary + amber `#D97706` accent | **Amber adopted, blue rejected** | The amber is already our N1 colour and survives WCAG 3:1. A saturated blue primary is the generic-dashboard tell, and blue-as-primary collides with our rule that colour on this product means *verdict semantics only*. |
| Typography: Fira Code / Fira Sans | **Rejected** | Fira Sans is a common UI sans; `frontend-design` explicitly warns against generic sans defaults. |
| Typography (result 2): *Minimalist Monochrome Editorial* — Source Serif 4 + JetBrains Mono, "NO UI sans-serif" | **Adopted** | This is the register we want. Serif body reads as document; mono reads as machine-verifiable. |
| Motion: Scroll Reveal, 300–400 ms, `power1.out` | **Adopted, without GSAP** | One staggered page-load reveal in CSS. Adding a 70 kB animation library for one effect fails our own dependency test. |

Priority checks taken directly from the skill's rule table: contrast ≥ 4.5:1,
visible focus rings, 44 × 44 px targets, 150–300 ms transitions,
`prefers-reduced-motion`, semantic colour tokens (no raw hex in components), SVG
icons never emoji, chart meaning never carried by colour alone.

---

## 2. Typography

| Role | Font | Why |
|---|---|---|
| Body, headings | **Source Serif 4** (300/400/600/700) | A serif for UI is unusual and that is the point — it signals *record*, not *app*. Designed by Adobe for screen text, so it holds at 13 px where a display serif would not. |
| Numbers, IDs, hashes, coordinates, rule paths | **JetBrains Mono** (400/500/700) | Tabular figures by default. Every number in this product is evidence, and evidence should be alignable down a column. Also makes a 64-character digest readable. |

**No sans-serif anywhere.** Two families, five weights, self-hosted.

Scale (density 8 — dashboard):

```
--t-display  28px / 1.15 / -0.02em   screen titles
--t-h1       19px / 1.25 / -0.01em   section heads
--t-h2       15px / 1.35            card heads
--t-body     14px / 1.55            prose, reasons, dissent
--t-small    12.5px / 1.45          notes, captions
--t-micro    10.5px / 1.4 / 0.08em  uppercase mono labels
--t-figure   22px / 1.1             the one big number on a card
```

Base body is 14 px, not 16. Justified: this is a data-dense internal tool at
density 8, the serif has a large x-height, and every number is mono. Prose never
drops below 14 px and nothing user-facing drops below 12.5 px.

### Offline fonts

Self-hosted in `frontend/public/fonts/`, not loaded from Google. §38 requires the
demo to survive the venue network being physically disconnected; a webfont CDN
call is exactly the kind of dependency that turns into a fallback-to-Times
disaster on stage. `font-display: swap` and a serif fallback stack regardless.

---

## 3. Colour

Ground is **warm paper**, not white and not blue-grey. Ink is near-black with a
faint warm cast. This is the "document" half of the direction.

```
--paper       #FAF8F4   page ground
--paper-2     #FFFDFA   raised surface (cards, table rows)
--paper-3     #F2EEE7   inset (table headers, code blocks)
--ink         #1A1815   primary text
--ink-2       #514B42   secondary text
--ink-3       #8A8175   tertiary / captions
--rule        #E0D9CE   hairlines
--rule-2      #CFC6B8   stronger hairlines
```

### Verdict semantics — the only place colour carries meaning

Colour is **reserved** for the epistemic ladder. Nothing decorative is coloured,
so when colour appears it means something.

| Level | Token | Hex | Reasoning |
|---|---|---|---|
| L4 control-differenced | `--l4` | `#1F5C42` | Deepest green. The **only** strongly positive colour in the product. |
| L3 multi-indicator | `--l3` | `#2F6B52` | |
| L2 corroborated | `--l2` | `#42705C` | |
| L1 observed | `--l1` | `#5E6B60` | Drifting to neutral as the claim weakens. |
| L0 recorded | `--l0` | `#7A756C` | Grey. Nothing was observed. |
| N1 inconclusive | `--n1` | `#96681A` | Amber. The DB offered `#D97706`/`#B07D1E` adjusted for **3:1**, which is the non-text threshold; chip text measured 3.62:1 and was darkened to 4.89:1. |
| N2 unsupported | `--n2` | `#A85D24` | |
| N3 contradicted | `--n3` | `#92321F` | Rust, never fire-engine red. |

**Two rules, enforced by review:**

1. **Green is never used for anything except L2–L4.** No green "success" toasts,
   no green buttons. A green tick anywhere else would teach the eye that green
   means "fine", and then an L4 chip stops meaning "control-differenced".
2. **The ladder is a gradient, not a traffic light.** L1 is nearly grey because
   L1 nearly says nothing. A user should feel the claim weakening before reading
   the label.

Functional accents, used sparingly:

```
--accent      #8A5A2B   links, focus ring, active nav (warm brown, not blue)
--warn-bg     #FBF3E0   provisional/caveat panels
--warn-edge   #B07D1E
```

Contrast, **measured in the browser rather than asserted**: `--ink` 16.7:1,
`--ink-2` 8.1:1, `--ink-3` 5.0:1 on paper; every chip's white-on-colour ≥ 4.58:1.

Two values in the first draft of this document were wrong and were caught by
measuring: `--ink-3` was `#8A8175` and claimed here as 4.6:1 — it measured
**3.32:1** and carries captions, mono labels and table headers, all of them text.
And `--n1` at `#B07D1E` measured **3.62:1** behind white chip text. Both
darkened. A contrast figure written from intuition is worth nothing.

---

## 4. Layout and materials

**Hairlines, not floating cards.** Documents have rules; apps have drop shadows.
One shadow token exists, used only for the one true overlay (the Method drawer).

```
--space: 4 · 8 · 12 · 16 · 24 · 32 · 48px      (density 8)
--radius: 3px (inputs, chips) · 6px (panels)   deliberately small
--shadow-overlay: 0 12px 40px rgb(26 24 21 / 14%)
```

Grid: a fixed **196 px** left rail, then fluid content with a **1320 px** max.
Breakpoints 1440 / 1024 / 768 / 375. Below 1024 the rail collapses to icons;
below 768 the three-column detail screen stacks in the order **claim → verdict →
dissent** so bad news is never below the fold on mobile.

---

## 5. Motion (dial 3 — subtle)

- One page-load reveal: `opacity 0→1, translateY 8px→0`, 260 ms `cubic-bezier(.2,.6,.2,1)`, staggered 40 ms per block, maximum six blocks.
- Hover/focus transitions 160 ms. Chip and row hover only.
- The confidence ring draws once on mount, 520 ms — the single "delight" moment, and it encodes data rather than decorating.
- Everything inside `@media (prefers-reduced-motion: reduce)` collapses to 0 ms.

No parallax, no scroll-jacking, no skeleton shimmer. A verdict screen that
animates while an officer is reading it is disrespectful of the task.

---

## 6. Screens

Five screens. Enough to tell the whole story, and each one is a real view of real
stored data.

### S0 · Shell
Left rail: wordmark **प्रमाण / PRAMAAN**, nav (Register · Claim · Temporal ·
Method), and a footer stating engine version and offline state read from
`/healthz`. Top bar per screen: title, then the claim's unique ID in mono.

### S1 · Claims register *(entry point)*
Dense table: unique ID · type · claimed date · **level chip** · confidence ·
coverage · families available · provenance badge.

**Every row is labelled `MEASURED` or `GOLDEN`.** The register is seeded with the
23 golden cases alongside the one real claim, so the table demonstrates **all
eight epistemic levels** — and the badge makes it impossible to mistake a
synthetic case for a measurement. A register showing one row would look broken;
a register showing 24 unlabelled rows would be dishonest. This is the third
option.

Filters: level, provenance, type. Sort by confidence.

### S2 · Reconciliation detail *(the daily-use screen, docs §24 S9)*
Three columns.

- **Left — the claim.** Type, unique ID, claimed date, coordinate in mono, GPS accuracy, and the **uncertainty disk drawn to scale** against a 30 m pixel grid. That small diagram is the most under-rated thing in the product: it makes "we never sample a single pixel" visible in one glance.
- **Centre — the verdict.** Level chip (largest element on the screen), confidence ring, score, coverage, quality. Then the **evidence tree**: one row per family with an agreement arrow (▲ agrees / ▬ neutral / ▼ disagrees / ○ unavailable), the agreement value in mono, and an expander revealing that family's reason and its lineage. Then the `rule_path` in mono — the audit trail of which rule fired.
- **Right — dissent and action.** Dissent panel, bordered, **always expanded, never collapsible**. Then recommended action, then the adjudication controls: **Accept · Edit · Reject**.

Level is rendered **before** confidence, per §24.4: level says how strongly a
thing is known, confidence says how much of that level's evidence agreed.
Confidence-first invites reading 0.06 as "6 % likely true".

### S3 · Temporal analysis *(the hero, already built)*
Restyled to the new system. Adds the season legend and a control-basis line the
first version lacked.

### S4 · Method *(a drawer, reachable from any verdict)*
Reads `/api/v1/method/{ladder,weights,thresholds,signatures}` **at runtime, from
the engine**. The epistemic ladder as a table, the six family weights with the
reason each is weighted as it is, the thresholds, and the per-type expected
signatures.

This exists because judges will click it, and because a system that can print its
own method from its own running configuration cannot have a slide that disagrees
with its code.

---

## 7. Honesty rules that are UI, not copy

1. **Unavailable ≠ zero.** An unavailable family renders as `○ unavailable` with its reason, never as a `0.00` bar. A zero bar reads as "measured, neutral".
2. **A gap in a chart is a gap.** Lines break across unobserved seasons; they are never interpolated.
3. **`PROVISIONAL` is on screen until adjudicated.** Not a tooltip.
4. **Synthetic data is badged in the same type size as the level chip.** Not in a footnote.
5. **A disabled control says why.** No dead buttons.
6. **Numbers never appear without their unit or their basis.** `+0.1157` is meaningless; `NDVI +0.1157 rabi 2022→2025` is evidence.

---

## 8. Build order

1. Tokens, self-hosted fonts, shell, rail — the system
2. S1 register (needs a claims list endpoint)
3. S2 reconciliation detail (needs an evidence endpoint)
4. S4 method drawer (endpoints already exist)
5. S3 temporal restyle
6. Browser verification at 1440 / 1024 / 768 / 375, plus contrast and keyboard pass

## 9. Explicitly not built

Map console (S3 in the doc), indicator dashboard, alerts, Evidence Pack PDF,
photo upload. They need data or services that do not exist yet, and a
convincing-looking empty map would be the most dishonest thing in this product.
