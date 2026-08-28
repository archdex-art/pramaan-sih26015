# 15 — The demo film

Two cuts, both rendered from the running console:

| File | Length | Size | Use |
|---|---|---|---|
| `docs/video/PRAMAAN_demo.mp4` | **93.8 s** | 14 MB | The full argument. Play in the pitch, narrate over it. |
| `docs/video/PRAMAAN_demo_45s.mp4` | **44.3 s** | 4.9 MB | Submission portals, WhatsApp, anywhere attention is short. |

1920×1080 · H.264 High · yuv420p · 30 fps · faststart. Plays in Keynote,
PowerPoint, QuickTime and a browser without transcoding. Verified by decoding
both files end to end.

Rebuild with `make video` (needs the stack up).

## Why this rather than a concept film

The reference this was modelled on was an AI-generated explainer with **mocked
interfaces** — invented screens, invented numbers, a literal ladder. It looked
good and it showed nothing.

Everything in this film is the interface that exists, driven through the real
API against real seeded data. **Every number on screen is one the system
computed.** A judge who suspects a mockup discounts everything around it; a judge
watching a real console discounts nothing.

The one thing a concept film has that this does not is aerial footage of a check
dam. That is worth having, and it is a camera problem rather than a software
one — if the team shoots it, it belongs in front of the first card.

## Structure

Card, then the screen it describes. A film that cuts straight between screens
leaves the viewer decoding an interface instead of following an argument.

| # | Beat | Source |
|---|---|---|
| 1 | "India solved collection. It has not solved interpretation." | card |
| 2 | "That verifies a photograph exists. Not that the structure works." | card |
| 3 | Claims register — 24 claims, 8/8 levels, 1 measured | **live** |
| 4 | "Level first. Then confidence." | card |
| 5 | Reconciliation detail — the verdict opening | **live** |
| 6 | "We never sample a single pixel." → the uncertainty disk | card + **live** |
| 7 | "Flagged before any model runs." → terrain expanded | card + **live** |
| 8 | The dissent panel and the disabled adjudication controls | **live** |
| 9 | **"A naive dashboard reports success here."** +0.1157 / +0.0901 / +0.026 | card |
| 10 | The temporal chart — site inside the control band | **live** |
| 11 | "Inconclusive. And we say so." | card |
| 12 | "The system prints its own method." → Method drawer | card + **live** |
| 13 | "Recomputed byte-identically." → the real API response | card + **live** |
| 14 | "The officer decides. The system shows its work." | card |

## Decisions worth recording

**No audio track.** The presenter narrates live, and a film with its own
voiceover competes with them. A stock music bed is also what makes a pitch film
sound like every other pitch film. `SIX_SLIDE_SCRIPT.md` has the narration if a
voiced version is ever wanted.

**Captured at 1360 px and upscaled to 1920.** The console is a dense dashboard at
density 8. Recorded 1:1 at 1080p its 12.5 px table text is unreadable on a
projector and a viewer sees "a busy screen" rather than the evidence. Capturing
narrower makes every glyph 1.41× larger without touching a single style, and
1360 stays above the 1024 px breakpoint so the full rail and the three-column
layout are still what the film shows.

**A drawn cursor, not the real pointer.** Playwright's recording does not include
the system cursor, and a demo where panels expand with nothing visibly touching
them reads as a slideshow. The drawn one also glides on a 620 ms ease, which is
more legible than a real hand.

**Motion on cards only.** A 4 % push over a typographic card reads as
intentional. The same push on a screen recording makes the interface look like a
photograph of software.

**Beats run 3–6 s, not 1.5 s.** The temptation in a short film is to cut fast. A
viewer who cannot finish reading a verdict learns nothing from having seen one.

**Scripted, not hand-recorded.** A hand-recorded take has mouse jitter,
inconsistent pacing, and has to be redone from scratch for any UI change.
`make video` produces the same film every time.

## One zoom, and one that was reverted

The expanded evidence tree is pushed to 1.5× because the terrain reason —
*"Siting is implausible for a check_dam: Strahler order >= 2 (disk max 0, median
0); distance to stream <= 30 m (disk min 247 m, median 277 m)"* — is the beat's
entire content, and it is unreadable at full frame.

The same treatment was tried on the temporal chart and reverted: at 1.25× the
crop clipped the *"rabi controls n=12 · site INSIDE band"* caption and the y
axis, so a zoom intended to aid reading removed the labels being read. Region
geometry for both was measured from the live page rather than eyeballed.

## What the film deliberately does not claim

No adjudication signature — the buttons appear disabled with the reason on
screen. No photo-model output. No map console. No accuracy figure. The verdict
shown is **N1 inconclusive at confidence 0.0615**, because that is what the
system computed from the real data.
