#!/usr/bin/env python3
"""Record a professional demo video of the real console.

## Why this and not a concept film

The reference video for this project was an AI-generated explainer with **mocked
interfaces** — invented screens, invented numbers. This records the interface
that actually exists, driven through the real API against real seeded data, so
every number on screen is one the system computed.

That is the only version worth showing. A judge who suspects a mockup discounts
everything around it; a judge watching a real console discounts nothing.

## How it works

Playwright drives the live console at deliberate, readable pacing and records
each beat as its own clip. Title and caption cards are rendered from HTML using
the product's own design tokens, so the film and the interface cannot look like
two different products. `compose_demo.sh` then assembles them with ffmpeg.

Scripted rather than hand-recorded on purpose: a hand-recorded take has mouse
jitter, inconsistent timing, and has to be redone from scratch for any change.
This is reproducible — `make video` gives the same film every time.

## Pacing

Each beat holds long enough to read the thing it is pointing at. The temptation
in a 60-second film is to cut every 1.5 s; a viewer who cannot finish reading a
verdict learns nothing from seeing it. Beats here run 3–6 s.

Requires the stack running (`make demo-up && make web`).

    uv run --with playwright python scripts/record_demo.py
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "build" / "video"
CLIPS = OUT / "clips"
CARDS = OUT / "cards"

BASE = "http://127.0.0.1:5173"

#: Delivery frame.
W, H = 1920, 1080

#: Capture viewport for screen beats — deliberately narrower than the delivery
#: frame. The console is a dense dashboard at density 8; recorded 1:1 at 1080p
#: its 12.5 px table text is unreadable on a projector, and a viewer sees "a
#: busy screen" instead of the evidence. Capturing at 1360 and upscaling makes
#: every glyph 1.41x larger without touching a style. 1360 stays above the
#: 1024 px breakpoint, so the full rail and the three-column detail layout are
#: still what the film shows.
CAP_W, CAP_H = 1360, 765

# A cursor drawn into the page. Playwright's recording does not include the real
# pointer, and a demo where things activate with nothing visibly touching them
# reads as a slideshow rather than a session.
CURSOR_JS = """
(() => {
  if (document.getElementById('__cur')) return;
  const c = document.createElement('div');
  c.id = '__cur';
  c.style.cssText = `position:fixed;z-index:99999;width:22px;height:22px;
    margin:-11px 0 0 -11px;border-radius:50%;pointer-events:none;
    border:2px solid #8a5a2b;background:rgba(138,90,43,.16);
    transition:transform 620ms cubic-bezier(.3,.7,.2,1),opacity 200ms;
    opacity:0;left:0;top:0`;
  document.body.appendChild(c);
  window.__moveCur = (x, y) => {
    c.style.opacity = '1';
    c.style.transform = `translate(${x}px,${y}px)`;
  };
  window.__clickCur = () => {
    c.animate(
      [{ transform: c.style.transform + ' scale(1)' },
       { transform: c.style.transform + ' scale(0.55)' },
       { transform: c.style.transform + ' scale(1)' }],
      { duration: 260, easing: 'ease-out' });
  };
})()
"""


@dataclass(frozen=True, slots=True)
class Card:
    """A full-frame typographic card, rendered from the product's own tokens."""

    name: str
    kicker: str
    headline: str
    sub: str = ""
    #: Optional stat row: (value, label) pairs.
    stats: tuple[tuple[str, str], ...] = ()
    dark: bool = False


CARD_TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<style>
{tokens}
html,body{{margin:0;height:100%}}
body{{
  width:{w}px;height:{h}px;
  background:{bg};color:{fg};
  font-family:var(--font-body);
  display:flex;flex-direction:column;justify-content:center;
  padding:0 132px;box-sizing:border-box;
}}
.kicker{{
  font-family:var(--font-mono);font-size:19px;font-weight:500;
  letter-spacing:.24em;text-transform:uppercase;
  color:{kick};margin-bottom:26px;
}}
h1{{
  margin:0;font-size:{hsize}px;line-height:1.08;font-weight:600;
  letter-spacing:-.022em;max-width:26ch;
}}
.sub{{
  margin:30px 0 0;font-size:26px;line-height:1.5;
  color:{sub};max-width:52ch;
}}
.stats{{display:flex;gap:78px;margin:54px 0 0}}
.stat .v{{
  font-family:var(--font-mono);font-size:52px;font-weight:700;
  letter-spacing:-.02em;line-height:1;
}}
.stat .l{{
  font-family:var(--font-mono);font-size:15px;font-weight:500;
  letter-spacing:.14em;text-transform:uppercase;
  color:{sub};margin-top:12px;
}}
.rule{{height:2px;width:96px;background:var(--accent);margin:0 0 34px}}
</style></head><body>
<div class="rule"></div>
<div class="kicker">{kicker}</div>
<h1>{headline}</h1>
{sub_html}
{stats_html}
</body></html>"""


CARDS_SPEC: tuple[Card, ...] = (
    Card(
        name="00-open",
        kicker="Smart India Hackathon 2026 · PS 26015",
        headline="India solved collection.\nIt has not solved interpretation.",
        sub="1,24,830 water-harvesting structures under WDC-PMKSY 2.0. "
        "Today each photograph is moderated by eye — accept, or reject.",
        dark=True,
    ),
    Card(
        name="01-thesis",
        kicker="The gap",
        headline="That verifies a photograph exists.\nNot that the structure works.",
        sub="PRAMAAN — प्रमाण, proof — turns every geo-tagged photograph into a "
        "claim the satellite and terrain record can test.",
    ),
    Card(
        name="02-register",
        kicker="01 · Claims register",
        headline="Every claim on a published\nevidence ladder.",
        sub="Eight epistemic levels, L0 to L4 and N1 to N3. One row is MEASURED — "
        "computed from real imagery. The rest are badged test cases.",
        stats=(("24", "claims"), ("8/8", "levels present"), ("1", "measured")),
    ),
    Card(
        name="03-verdict",
        kicker="02 · Reconciliation",
        headline="Level first.\nThen confidence.",
        sub="Level says how strongly a thing is known. Confidence says how much "
        "of that level's evidence agreed. Six families vote; the photograph "
        "is weighted lowest, because it is the claim's own source.",
    ),
    Card(
        name="04-disk",
        kicker="03 · Location uncertainty",
        headline="We never sample\na single pixel.",
        sub="The GPS uncertainty disk, drawn to scale against the 30 m grid. On "
        "this claim flow accumulation varied 1 to 216 pixels across 15 metres.",
    ),
    Card(
        name="05-terrain",
        kicker="04 · Terrain plausibility",
        headline="Flagged before\nany model runs.",
        sub="Strahler order 0, 277 m from any drainage line — implausible siting "
        "for a check dam. Arithmetic on an elevation model. No AI, no training "
        "data, explainable in one line.",
    ),
    Card(
        name="06-payoff",
        kicker="05 · Matched controls",
        headline="A naive dashboard\nreports success here.",
        sub="The site's rabi NDVI rose +0.1157 across the claim date. Twelve "
        "control sites, matched on real DEM slope, elevation and stream "
        "distance, rose a median +0.0901.",
        stats=(
            ("+0.1157", "site"),
            ("+0.0901", "controls"),
            ("+0.026", "differenced"),
        ),
        dark=True,
    ),
    Card(
        name="07-refusal",
        kicker="The verdict",
        headline="Inconclusive.\nAnd we say so.",
        sub="The site sits at the 75th percentile of its own controls — inside "
        "the band. Terrain says this location cannot host a check dam; "
        "vegetation says something grew. The engine names the conflict "
        "instead of averaging it away.",
    ),
    Card(
        name="08-method",
        kicker="06 · Method",
        headline="The system prints\nits own method.",
        sub="Read from the running engine, not hardcoded. L5 — causal — is "
        "absent from the engine's level enum entirely, so no code path can "
        "construct it. The ceiling is L4, control-differenced.",
    ),
    Card(
        name="09-audit",
        kicker="07 · Audit defensibility",
        headline="Recomputed\nbyte-identically.",
        sub="Every verdict stores the exact inputs that produced it. An auditor "
        "can reproduce a decision made in 2026 when they open it in 2031.",
    ),
    Card(
        name="10-close",
        kicker="प्रमाण · PRAMAAN · proof",
        headline="The officer decides.\nThe system shows its work.",
        sub="Nothing here becomes government evidence until a named officer "
        "signs it. Free, verified data · no GPU on the core path · ₹0 licence "
        "cost.",
        dark=True,
    ),
)


def render_card_html(card: Card) -> str:
    tokens = (REPO_ROOT / "frontend" / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
    # The tokens sheet points at /fonts/…; for a file:// render it needs a path
    # that resolves on disk.
    fonts = (REPO_ROOT / "frontend" / "public" / "fonts").as_posix()
    tokens = tokens.replace('url("/fonts/', f'url("file://{fonts}/')

    bg = "var(--ink)" if card.dark else "var(--paper)"
    fg = "var(--paper)" if card.dark else "var(--ink)"
    sub = "rgba(250,248,244,.72)" if card.dark else "var(--ink-2)"
    kick = "rgba(250,248,244,.6)" if card.dark else "var(--ink-3)"

    headline = card.headline.replace("\n", "<br>")
    hsize = 96 if len(card.headline) < 56 else 82

    sub_html = f'<p class="sub">{card.sub}</p>' if card.sub else ""
    stats_html = ""
    if card.stats:
        cells = "".join(
            f'<div class="stat"><div class="v">{v}</div><div class="l">{label}</div></div>'
            for v, label in card.stats
        )
        stats_html = f'<div class="stats">{cells}</div>'

    return CARD_TEMPLATE.format(
        tokens=tokens,
        w=W,
        h=H,
        bg=bg,
        fg=fg,
        sub=sub,
        kick=kick,
        hsize=hsize,
        kicker=card.kicker,
        headline=headline,
        sub_html=sub_html,
        stats_html=stats_html,
    )


async def build_cards(browser) -> None:  # type: ignore[no-untyped-def]
    CARDS.mkdir(parents=True, exist_ok=True)
    page = await browser.new_page(viewport={"width": W, "height": H})
    for card in CARDS_SPEC:
        path = CARDS / f"{card.name}.html"
        path.write_text(render_card_html(card), encoding="utf-8")
        await page.goto(path.as_uri())
        await page.wait_for_timeout(420)  # let the webfonts land
        await page.screenshot(path=str(CARDS / f"{card.name}.png"))
        print(f"  card  {card.name}")
    await page.close()


async def move_to(page, selector: str, *, index: int = 0) -> None:  # type: ignore[no-untyped-def]
    """Glide the drawn cursor to an element's centre, then settle."""
    box = await page.locator(selector).nth(index).bounding_box()
    if box is None:
        return
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    await page.evaluate("([x,y]) => window.__moveCur(x,y)", [x, y])
    await page.wait_for_timeout(700)


async def click_at(page, selector: str, *, index: int = 0) -> None:  # type: ignore[no-untyped-def]
    await move_to(page, selector, index=index)
    await page.evaluate("() => window.__clickCur()")
    await page.locator(selector).nth(index).click()
    await page.wait_for_timeout(220)


async def record_beat(browser, name: str, body) -> Path:  # type: ignore[no-untyped-def]
    """Record one interaction beat into its own clip."""
    target = CLIPS / name
    target.mkdir(parents=True, exist_ok=True)
    ctx = await browser.new_context(
        viewport={"width": CAP_W, "height": CAP_H},
        record_video_dir=str(target),
        record_video_size={"width": W, "height": H},
        device_scale_factor=1,
        # A recording that fires the page-load stagger on every navigation looks
        # busy; the film supplies its own motion.
        reduced_motion="no-preference",
    )
    page = await ctx.new_page()
    await body(page)
    await ctx.close()
    clip = next(target.glob("*.webm"))
    print(f"  beat  {name:<14} {clip.stat().st_size / 1e6:.1f} MB")
    return clip


TERMINAL_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
{tokens}
html,body{{margin:0;height:100%}}
body{{
  width:{w}px;height:{h}px;background:var(--ink);
  font-family:var(--font-mono);font-size:25px;line-height:1.62;
  padding:78px 96px;box-sizing:border-box;color:rgba(250,248,244,.9);
}}
.k{{
  font-size:16px;letter-spacing:.22em;text-transform:uppercase;
  color:rgba(250,248,244,.5);margin-bottom:34px;
}}
.p{{color:var(--accent)}}
.cmd{{color:#faf8f4}}
.out{{color:rgba(250,248,244,.62);white-space:pre}}
.hit{{color:#8fbfa2;font-weight:700}}
#c{{
  display:inline-block;width:12px;height:25px;
  background:var(--accent);vertical-align:-4px;
  animation:b 1s steps(2,start) infinite;
}}
@keyframes b{{50%{{opacity:0}}}}
</style></head><body>
<div class="k">Audit defensibility · POST /verdicts/1/recompute</div>
<div><span class="p">$</span> <span class="cmd" id="t"></span><span id="c"></span></div>
<div class="out" id="o"></div>
<script>
const CMD = {cmd!r};
const OUT = {out!r};
const t = document.getElementById('t'), o = document.getElementById('o'),
      c = document.getElementById('c');
let i = 0;
// Typed at reading speed, not machine speed: the viewer has to be able to see
// what was asked before the answer appears.
const tick = () => {{
  if (i <= CMD.length) {{ t.textContent = CMD.slice(0, i++); setTimeout(tick, 34); return; }}
  c.style.display = 'none';
  setTimeout(() => {{ o.innerHTML = OUT; }}, 420);
}};
tick();
</script></body></html>"""


async def build_terminal(browser) -> None:  # type: ignore[no-untyped-def]
    """Record the recompute proof, with the API's real response.

    Rendered rather than screen-captured from a shell so the typography matches
    the rest of the film — but the payload is fetched live, so the number on
    screen is the one the running engine produced.
    """
    import json
    import urllib.request

    url = "http://127.0.0.1:8000/api/v1/verdicts/1/recompute"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)

    keep = (
        "verdict_id",
        "hash_before",
        "hash_after",
        "identical",
        "engine_version_stored",
        "recomputed_level",
    )
    lines = []
    for key in keep:
        value = payload[key]
        shown = json.dumps(value)
        if key == "identical":
            lines.append(f'  "{key}": <span class="hit">{shown}</span>,')
        else:
            lines.append(f'  "{key}": {shown},')
    out = "{\n" + "\n".join(lines).rstrip(",") + "\n}"

    tokens = (REPO_ROOT / "frontend" / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
    fonts = (REPO_ROOT / "frontend" / "public" / "fonts").as_posix()
    tokens = tokens.replace('url("/fonts/', f'url("file://{fonts}/')

    html = TERMINAL_HTML.format(
        tokens=tokens,
        w=W,
        h=H,
        cmd="curl -sX POST localhost:8000/api/v1/verdicts/1/recompute",
        out=out,
    )
    path = CARDS / "term-recompute.html"
    path.write_text(html, encoding="utf-8")

    target = CLIPS / "recompute"
    target.mkdir(parents=True, exist_ok=True)
    ctx = await browser.new_context(
        viewport={"width": W, "height": H},
        record_video_dir=str(target),
        record_video_size={"width": W, "height": H},
    )
    page = await ctx.new_page()
    await page.goto(path.as_uri())
    await page.wait_for_timeout(6200)
    await ctx.close()
    clip = next(target.glob("*.webm"))
    print(
        f"  beat  recompute      {clip.stat().st_size / 1e6:.1f} MB "
        f"(identical={payload['identical']})"
    )


async def main() -> int:
    from playwright.async_api import async_playwright

    if OUT.exists():
        shutil.rmtree(OUT)
    CLIPS.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--force-device-scale-factor=1"])

        print("rendering cards")
        await build_cards(browser)

        print("recording beats")

        async def register(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/")
            await page.wait_for_selector("table.register tbody tr")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(1500)
            # Rest on the level column so the ladder gradient reads, then on the
            # single MEASURED badge — the honesty beat.
            await move_to(page, "table.register tbody tr .chip", index=0)
            await page.wait_for_timeout(900)
            await move_to(page, ".badge[data-prov=measured]")
            await page.wait_for_timeout(1600)

        async def open_claim(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/")
            await page.wait_for_selector(".badge[data-prov=measured]")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(600)
            row = "table.register tbody tr:has(.badge[data-prov=measured])"
            await click_at(page, row)
            await page.wait_for_selector(".fam")
            await page.wait_for_timeout(2100)
            await move_to(page, ".chip.lg")
            await page.wait_for_timeout(1300)

        async def disk(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/claim/1")
            await page.wait_for_selector(".disk svg")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(500)
            await move_to(page, ".disk svg")
            await page.wait_for_timeout(2400)

        async def terrain(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/claim/1")
            await page.wait_for_selector(".fam")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(500)
            await click_at(page, ".fam[data-dir=disagrees] .fam-head")
            await page.wait_for_timeout(2600)
            # Then the unavailable family: absence of evidence is not evidence.
            await move_to(page, ".fam[data-dir=unavailable] .fam-name")
            await page.wait_for_timeout(1500)

        async def dissent(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/claim/1")
            await page.wait_for_selector(".panel.dissent li")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(400)
            await move_to(page, ".panel.dissent")
            await page.wait_for_timeout(2300)
            await move_to(page, ".adjudication .btn-row")
            await page.wait_for_timeout(1700)

        async def temporal(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/temporal/1")
            await page.wait_for_selector(".chart svg")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(1700)
            await move_to(page, "g.ribbon .ribbon-label", index=0)
            await page.wait_for_timeout(2600)
            await move_to(page, "figcaption .note", index=0)
            await page.wait_for_timeout(1600)

        async def method(page):  # type: ignore[no-untyped-def]
            await page.goto(f"{BASE}/#/claim/1")
            await page.wait_for_selector(".head-actions .btn")
            await page.evaluate(CURSOR_JS)
            await page.wait_for_timeout(400)
            await click_at(page, ".head-actions .btn:has-text('Method')")
            await page.wait_for_selector(".drawer table.weights")
            await page.wait_for_timeout(2300)
            await move_to(page, ".refused")
            await page.wait_for_timeout(1900)
            await move_to(page, "table.weights tr", index=5)
            await page.wait_for_timeout(1900)

        beats = (
            ("register", register),
            ("claim", open_claim),
            ("disk", disk),
            ("terrain", terrain),
            ("dissent", dissent),
            ("temporal", temporal),
            ("method", method),
        )
        for name, body in beats:
            await record_beat(browser, name, body)

        await build_terminal(browser)

        await browser.close()

    print(f"\nwrote {OUT.relative_to(REPO_ROOT)}")
    print("next: bash scripts/compose_demo.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
