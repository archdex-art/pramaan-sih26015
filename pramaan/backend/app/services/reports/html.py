"""Rendering the Evidence Pack as one self-contained HTML document.

## Self-contained is a requirement, not a preference

design doc §38 requires the demonstration to survive the venue network being
physically disconnected, and `PRAMAAN_OFFLINE` exists for that. A report that
fetches a stylesheet, a webfont or an icon set from a CDN renders as unstyled
Times on the one occasion it matters most. So this module emits a single string
with one inline `<style>`, no `<link>`, no `@import`, no `url()` and no remote
`<img>`. The font stacks name local families only; a family that is not installed
falls back within the stack and never triggers a request.

The field photograph is deliberately not embedded. It lives in object storage,
and reaching for it here would give a report module a storage dependency and a
network call. Its image id is printed instead, from the photo family's own
lineage, which is what an auditor needs to retrieve it.

## Why this is not the app stylesheet

`docs/14-ui-design-system.md` governs the console: a 196 px rail, hover states,
one page-load reveal. None of that survives a page break. The typographic
register is kept — warm paper, serif body, mono for every number and identifier,
hairlines rather than cards, colour only where it carries verdict semantics — and
the layout is a print layout with `@page` margins and `break-inside` control. The
ladder hex values are duplicated here rather than imported because the backend
must not depend on the frontend tree; they are stated as literals in the design
document and are frozen there.

## Two documents, one renderer

An unadjudicated verdict is not styled as a signed record. The masthead band, the
title and the closing section all change, because a document that looks signed
and is not is worse than no document. This is the specific defect in the
reference prototype this pack is measured against, which prints "SIGNED RECORD"
above an officer decision of "UNDER REVIEW".
"""

from __future__ import annotations

from html import escape
from typing import Any

from app.services.reconcile.weights import ENGINE_VERSION
from app.services.reports.limitations import Limitation, limitations_for
from app.services.reports.pack import EvidencePack, FamilyRow, lineage_bundle

#: The epistemic ladder's colours, from `docs/14-ui-design-system.md` §3. The
#: only place colour carries meaning in this document; nothing decorative is
#: coloured, so when colour appears it is the level speaking.
_LEVEL_INK = {
    "L4": "#1F5C42",
    "L3": "#2F6B52",
    "L2": "#42705C",
    "L1": "#5E6B60",
    "L0": "#7A756C",
    "N1": "#96681A",
    "N2": "#A85D24",
    "N3": "#92321F",
}

#: What each family is asked. Printed beside the agreement value because a
#: number without its question is not evidence (design doc §14 honesty rule 6).
_FAMILY_QUESTION = {
    "terrain": "Is this site hydrologically capable of hosting this structure?",
    "satellite": "Is the observed surface state consistent with the expectation?",
    "temporal": "Did the surface change, same season, year on year?",
    "control": "Did it change more than comparable un-intervened land?",
    "context": "Can rainfall account for the change?",
    "photo": "What the field sent. Weighted lowest — it is the claim's own source.",
}

#: Marker for a value the record does not contain. One string, used everywhere,
#: so "absent" can never be confused with an empty cell that means zero.
ABSENT = "NOT RECORDED"

_STYLE = """
@page { size: A4; margin: 16mm 14mm 18mm; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 28px 40px;
  background: #FAF8F4; color: #1A1815;
  font-family: "Source Serif 4", "Source Serif Pro", Georgia, "Times New Roman", serif;
  font-size: 10.5pt; line-height: 1.5;
}
@media print { body { background: #FFF; padding: 0; } }
.mono {
  font-family: "JetBrains Mono", ui-monospace, "SFMono-Regular", Menlo,
    Consolas, "Liberation Mono", monospace;
  font-variant-numeric: tabular-nums;
}
h1, h2, h3 { font-weight: 600; margin: 0; }
h1 { font-size: 17pt; letter-spacing: -0.01em; }
h2 {
  font-size: 11.5pt; margin: 22px 0 8px; padding-bottom: 4px;
  border-bottom: 1.5px solid #CFC6B8; text-transform: uppercase; letter-spacing: 0.06em;
}
h3 { font-size: 10.5pt; margin: 12px 0 3px; }
p { margin: 0 0 8px; }
.masthead { border-bottom: 2px solid #1A1815; padding-bottom: 10px; }
.wordmark { font-size: 13pt; letter-spacing: 0.02em; }
.wordmark span { color: #514B42; font-size: 10pt; letter-spacing: 0.14em; }
.sub { color: #514B42; font-size: 9pt; margin: 2px 0 0; }
.band {
  margin: 12px 0 0; padding: 9px 12px; font-size: 11pt; font-weight: 700;
  letter-spacing: 0.1em; text-transform: uppercase; border: 1.5px solid;
}
.band-signed { background: #F2EEE7; border-color: #1F5C42; color: #1F5C42; }
.band-provisional { background: #FBF3E0; border-color: #B07D1E; color: #7A5410; }
.band small {
  display: block; font-weight: 400; letter-spacing: 0; text-transform: none;
  font-size: 9pt; margin-top: 3px; color: #514B42;
}
table { width: 100%; border-collapse: collapse; font-size: 9pt; }
th, td { text-align: left; vertical-align: top; padding: 5px 8px 5px 0; }
thead th {
  border-bottom: 1px solid #CFC6B8; text-transform: uppercase;
  letter-spacing: 0.06em; font-size: 8pt; color: #514B42;
}
tbody tr + tr td { border-top: 1px solid #E0D9CE; }
td.num, th.num { text-align: right; padding-right: 12px; }
dl.kv { display: grid; grid-template-columns: auto 1fr; gap: 3px 16px; margin: 0; font-size: 9pt; }
dl.kv dt { color: #514B42; }
dl.kv dd { margin: 0; }
.limitation { break-inside: avoid; margin: 0 0 12px; padding-left: 11px;
  border-left: 3px solid #B07D1E; }
.limitation.standing { border-left-color: #CFC6B8; }
.limitation h3 { font-size: 10pt; }
.limitation p { margin: 0; font-size: 9.5pt; }
.limitation ul { margin: 5px 0 0; padding-left: 18px; font-size: 9.5pt; }
.limitation li + li { margin-top: 3px; }
.tag {
  display: inline-block; font-size: 7.5pt; letter-spacing: 0.08em;
  text-transform: uppercase; color: #514B42; border: 1px solid #CFC6B8;
  padding: 0 4px; margin-left: 6px; vertical-align: 1px;
}
.level { font-size: 13pt; font-weight: 700; letter-spacing: 0.02em; }
.figures { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 10px 0 0; }
.figures div { border-top: 1px solid #E0D9CE; padding-top: 4px; }
.figures dt { font-size: 7.5pt; letter-spacing: 0.08em; text-transform: uppercase; color: #514B42; }
.figures dd { margin: 1px 0 0; font-size: 13pt; }
.absent { color: #8A5A2B; font-size: 8.5pt; letter-spacing: 0.04em; }
.hash { word-break: break-all; font-size: 8.5pt; }
.note { font-size: 8.5pt; color: #514B42; margin: 6px 0 0; }
.dissent { background: #FBF3E0; border-left: 3px solid #B07D1E; padding: 9px 12px; }
.dissent ul { margin: 0; padding-left: 17px; font-size: 9.5pt; }
.dissent li + li { margin-top: 4px; }
section { break-inside: auto; }
.sig { break-inside: avoid; border-top: 1px solid #E0D9CE; padding-top: 8px; margin-top: 8px; }
footer {
  margin-top: 24px; border-top: 2px solid #1A1815; padding-top: 8px;
  font-size: 8.5pt; color: #514B42;
}
"""


def _e(value: object) -> str:
    """Escape for HTML text and attribute content.

    Applied to every interpolated value without exception, including values that
    "cannot" contain markup. `claims.detectability`, `interventions.unique_id`,
    an officer's `full_name` and every producer-authored `reason` are TEXT
    columns; one of them containing a `<` would otherwise silently corrupt the
    document, and one containing a `<script>` would do worse in a browser tab.
    """
    return escape(str(value), quote=True)


def _f(value: float | None, digits: int = 4) -> str:
    """A number, or the absent marker. Never a substituted zero."""
    if value is None:
        return f'<span class="absent">{ABSENT}</span>'
    return f'<span class="mono">{value:.{digits}f}</span>'


def _text_or_absent(value: object) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return f'<span class="absent">{ABSENT}</span>'
    return _e(value)


def _short_level(level: str) -> str:
    return level.split("_")[0]


def _masthead(pack: EvidencePack) -> str:
    """The document's own claim about what it is.

    Two mutually exclusive bands. An unsigned pack carries the provisional band
    and the title "Provisional assessment"; only a ledger-backed signature earns
    the words "signed record".
    """
    if pack.signed:
        signature = pack.operative_signature
        assert signature is not None  # implied by `signed`
        band = (
            '<div class="band band-signed">Signed record'
            f"<small>Adjudicated {_e(signature.decision)} by "
            f"{_e(signature.officer_name)} ({_e(signature.officer_username)}, "
            f"{_e(signature.officer_role)}) on {_e(signature.decided_at)}. "
            "Ledger linkage in section 8.</small></div>"
        )
        title = "Evidence Pack — signed record"
    else:
        band = (
            '<div class="band band-provisional">Provisional — not a signed record'
            f"<small>No authorised officer has accepted, edited or rejected this "
            f"assessment. Stored verdict status: "
            f'<span class="mono">{_e(pack.verdict.status)}</span>. '
            "This document is not government evidence.</small></div>"
        )
        title = "Evidence Pack — provisional assessment"

    return (
        '<header class="masthead">'
        '<div class="wordmark">प्रमाण <span>PRAMAAN</span></div>'
        f"<h1>{_e(title)}</h1>"
        f'<p class="sub">Structure <span class="mono">{_e(pack.claim.unique_id)}</span>'
        f' · claim <span class="mono">{pack.claim.claim_id}</span>'
        f' · district LGD <span class="mono">{_e(pack.claim.district_lgd)}</span>'
        f' · verdict version <span class="mono">{pack.verdict.version}</span></p>'
        f"{band}"
        "</header>"
    )


def _limitations(pack: EvidencePack) -> str:
    """Section 1. First, because it is the section that bounds every other one."""
    blocks: list[str] = []
    for item in limitations_for(pack):
        tag = (
            '<span class="tag">applies to every assessment</span>'
            if item.universal
            else '<span class="tag">this claim</span>'
        )
        classes = "limitation standing" if item.universal else "limitation"
        listing = (
            "<ul>" + "".join(f"<li>{_e(entry)}</li>" for entry in item.items) + "</ul>"
            if item.items
            else ""
        )
        blocks.append(
            f'<div class="{classes}"><h3>{_e(item.heading)}{tag}</h3>'
            f"<p>{_e(item.body)}</p>{listing}</div>"
        )
    return (
        "<section><h2>1 · Limitations of this assessment</h2>"
        "<p>Read before the finding, not after it. Entries marked "
        "<em>this claim</em> are computed from this structure&rsquo;s own stored "
        "record; entries marked <em>applies to every assessment</em> are standing "
        "refusals of the engine and are printed on every Evidence Pack. This "
        "section is not removable.</p>" + "".join(blocks) + "</section>"
    )


def _the_claim(pack: EvidencePack) -> str:
    claim = pack.claim
    return (
        "<section><h2>2 · The claim</h2>"
        '<dl class="kv">'
        f'<dt>Structure unique id</dt><dd class="mono">{_e(claim.unique_id)}</dd>'
        f"<dt>Intervention type</dt><dd>{_e(claim.intervention_type.replace('_', ' '))}</dd>"
        f"<dt>Project</dt><dd>{_e(claim.project_name)} "
        f'(<span class="mono">{_e(claim.project_code)}</span>)</dd>'
        f'<dt>District LGD</dt><dd class="mono">{_e(claim.district_lgd)}</dd>'
        f"<dt>Village LGD</dt><dd>{_text_or_absent(claim.village_lgd)}</dd>"
        f"<dt>Survey number</dt><dd>{_text_or_absent(claim.survey_no)}</dd>"
        f"<dt>Asserted status</dt><dd>{_e(claim.asserted_status)}</dd>"
        f'<dt>Asserted date</dt><dd class="mono">{_e(claim.asserted_date)}</dd>'
        f"<dt>MIS work status</dt><dd>{_e(claim.work_status)}</dd>"
        f"<dt>MIS completion date</dt><dd>{_text_or_absent(claim.completed_date)}</dd>"
        f'<dt>Coordinate</dt><dd class="mono">{claim.lat:.5f}&deg;N '
        f"{claim.lon:.5f}&deg;E</dd>"
        f"<dt>Uncertainty disk radius</dt><dd>"
        + (
            f'<span class="mono">{claim.uncertainty_m:.1f}</span> m'
            if claim.uncertainty_m is not None
            else f'<span class="absent">{ABSENT}</span>'
        )
        + "</dd>"
        "<dt>Expected footprint</dt><dd>"
        + (
            f'<span class="mono">{claim.expected_footprint_m2:.0f}</span> m&sup2;'
            if claim.expected_footprint_m2 is not None
            else f'<span class="absent">{ABSENT}</span>'
        )
        + "</dd>"
        f"<dt>Detectability</dt><dd>{_text_or_absent(claim.detectability)}</dd>"
        f'<dt>Claim recorded at</dt><dd class="mono">{_e(claim.created_at)}</dd>'
        "</dl>"
        '<p class="note">Every terrain variable was read as a distribution over '
        "the uncertainty disk, never from the single pixel at its centre.</p>"
        "</section>"
    )


def _the_verdict(pack: EvidencePack) -> str:
    """Section 3. Level before confidence, per design doc §24.4.

    Level says how strongly a thing is known; confidence says how much of that
    level's evidence agreed. Printing confidence first invites reading 0.06 as
    "6 % likely true".
    """
    verdict = pack.verdict
    ink = _LEVEL_INK.get(_short_level(verdict.level), "#1A1815")
    action = verdict.recommended_action.get("action")
    priority = verdict.recommended_action.get("priority")
    return (
        "<section><h2>3 · The verdict, as stored</h2>"
        f'<p class="level mono" style="color:{ink}">{_e(verdict.level)} &middot; '
        f"{_e(pack.label)}</p>"
        '<dl class="figures">'
        f'<div><dt>score</dt><dd class="mono">{verdict.score:.4f}</dd></div>'
        f'<div><dt>confidence</dt><dd class="mono">{verdict.confidence:.4f}</dd></div>'
        f'<div><dt>coverage</dt><dd class="mono">{verdict.coverage:.4f}</dd></div>'
        f'<div><dt>data sufficiency</dt><dd class="mono">'
        f"{verdict.data_sufficiency:.4f}</dd></div>"
        "</dl>"
        '<dl class="kv" style="margin-top:12px">'
        f"<dt>Quality (metadata integrity &times; sufficiency)</dt>"
        f"<dd>{_f(verdict.quality)}</dd>"
        f'<dt>Rule path</dt><dd class="mono">'
        f"{_e(' -> '.join(verdict.rule_path)) or ABSENT}</dd>"
        f"<dt>Recommended action</dt><dd>{_text_or_absent(action)}"
        + (f' (priority <span class="mono">{_e(priority)}</span>)' if priority else "")
        + "</dd>"
        f'<dt>Stored status</dt><dd class="mono">{_e(verdict.status)}</dd>'
        f'<dt>Verdict version</dt><dd class="mono">{verdict.version}</dd>'
        "</dl>"
        '<p class="note">Confidence = |score| &times; coverage &times; quality. '
        "The label above is the only value on this page that is not read from a "
        "stored column: it is derived from the stored level and score by the "
        "engine&rsquo;s own labelling rule, which is the same rule the console "
        "displays. Nothing in this document was recomputed.</p>"
        "</section>"
    )


def _agreement_cell(row: FamilyRow) -> str:
    """An unavailable family renders as the word, never as 0.000.

    A zero would be indistinguishable from a measured neutral reading, and the
    difference between those two is the difference between low coverage and a
    genuine finding of no effect.
    """
    if not row.available or row.agreement is None:
        return '<span class="absent">unavailable</span>'
    return f'<span class="mono">{row.agreement:+.3f}</span>'


def _evidence(pack: EvidencePack) -> str:
    if not pack.families:
        return (
            "<section><h2>4 · Evidence by family</h2>"
            f'<p class="absent">{ABSENT} &mdash; no evidence rows are stored for '
            "this claim. The verdict&rsquo;s own recorded inputs appear in "
            "section 7.</p></section>"
        )
    weights = pack.verdict.weights
    rows = "".join(
        "<tr>"
        f"<td>{_e(row.family)}"
        + ('<span class="tag">cluster scale</span>' if row.cluster_scale else "")
        + "</td>"
        f'<td class="num">{_agreement_cell(row)}</td>'
        f'<td class="num">{_f(weights.get(row.family), 2)}</td>'
        f"<td>{_e(_FAMILY_QUESTION.get(row.family, ''))}<br>"
        f"{_text_or_absent(row.reason)}</td>"
        "</tr>"
        for row in pack.families
    )
    available = sum(1 for row in pack.families if row.available)
    return (
        "<section><h2>4 · Evidence by family</h2>"
        '<table><thead><tr><th>Family</th><th class="num">Agreement</th>'
        '<th class="num">Weight</th><th>Question asked, and the producer&rsquo;s '
        "recorded reason</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f'<p class="note"><span class="mono">{available}</span> of '
        f'<span class="mono">{len(pack.families)}</span> families available. '
        "Weights are frozen by ADR-001 and are not fitted parameters. The "
        "photograph carries the lowest substantive weight of the six because it "
        "is the claim&rsquo;s own source and must not outvote independent "
        "evidence.</p>"
        "</section>"
    )


def _dissent(pack: EvidencePack) -> str:
    """Section 5. Never collapsed, never omitted, never summarised."""
    if pack.verdict.dissent:
        body = "<ul>" + "".join(f"<li>{_e(line)}</li>" for line in pack.verdict.dissent) + "</ul>"
    else:
        body = (
            "<p>No dissent is recorded on this verdict. Every verdict this system "
            "issues is required to carry stated counter-evidence, so this is a "
            "defect in the record and not an absence of counter-evidence.</p>"
        )
    return (
        "<section><h2>5 · Dissent and counter-evidence</h2>"
        f'<div class="dissent">{body}</div></section>'
    )


def _alternatives(pack: EvidencePack) -> str:
    """Section 6. Both N3 paths require an alternative to have been *excluded*.

    "We did not think of any" must not read the same as "we ruled them out", so
    the exclusion status is a column and an empty list is stated rather than
    skipped. `basis` is read from `producers.alternatives`, which retains it; the
    digest-covered copy drops it deliberately, because rewording a justification
    must not invalidate a stored verdict.
    """
    canonical = lineage_bundle(pack).get("alternatives")
    canonical_list: list[Any] = canonical if isinstance(canonical, list) else []
    producers = pack.verdict.lineage.get("producers")
    detailed = producers.get("alternatives") if isinstance(producers, dict) else None
    bases: dict[str, str] = {}
    if isinstance(detailed, list):
        for entry in detailed:
            if isinstance(entry, dict) and "description" in entry:
                bases[str(entry["description"])] = str(entry.get("basis", ""))

    if not canonical_list:
        return (
            "<section><h2>6 · Alternative explanations</h2>"
            f'<p class="absent">{ABSENT} &mdash; no competing explanation was '
            "recorded against this claim. An N3 contradicted verdict requires at "
            "least one alternative to have been actively excluded; absence here "
            "means none was considered, not that none exists.</p></section>"
        )
    rows = "".join(
        "<tr>"
        f"<td>{_text_or_absent(entry.get('description'))}</td>"
        f"<td>{'excluded' if entry.get('excluded') else 'NOT excluded'}</td>"
        f"<td>{_text_or_absent(bases.get(str(entry.get('description', ''))))}</td>"
        "</tr>"
        for entry in canonical_list
        if isinstance(entry, dict)
    )
    return (
        "<section><h2>6 · Alternative explanations</h2>"
        "<table><thead><tr><th>Explanation considered</th><th>Status</th>"
        "<th>Basis recorded</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></section>"
    )


def _merged_family_lineage(pack: EvidencePack) -> dict[str, dict[str, Any]]:
    """Per-family producer provenance, from both places it is stored.

    `family_lineage` at the lineage root is what `bundle_from_lineage` restores
    from; `producers.families[*].lineage` is the engine's own copy. They agree on
    a record written by the normal path, and reading only one of them would blank
    this section for a verdict written by the other.
    """
    merged: dict[str, dict[str, Any]] = {}
    producers = pack.verdict.lineage.get("producers")
    if isinstance(producers, dict):
        families = producers.get("families")
        if isinstance(families, dict):
            for family, node in families.items():
                if isinstance(node, dict) and isinstance(node.get("lineage"), dict):
                    merged[str(family)] = dict(node["lineage"])
    root = pack.verdict.lineage.get("family_lineage")
    if isinstance(root, dict):
        for family, node in root.items():
            if isinstance(node, dict):
                merged.setdefault(str(family), {}).update(node)
    return merged


def _first(lineage: dict[str, dict[str, Any]], key: str) -> Any:
    """The first non-empty value for `key` across every family's lineage.

    Families stamp shared provenance — the analysis grid, the index name — into
    their own lineage dict, so the value is wherever the first producer that ran
    put it. Scanning rather than hardcoding a family means a producer being
    disabled does not blank an item that another producer also recorded.
    """
    for node in lineage.values():
        value = node.get(key)
        if value not in (None, "", [], {}, "unknown"):
            return value
    return None


def _lineage_row(requirement: str, value: str, note: str) -> str:
    return f"<tr><td>{_e(requirement)}</td><td>{value}</td><td>{_e(note)}</td></tr>"


def _absent(reason: str) -> str:
    return f'<span class="absent">{ABSENT}</span> &mdash; {_e(reason)}'


def _scene_table(pack: EvidencePack) -> str:
    """Granule ids with whatever dates and cloud fractions are on record.

    An id whose registry row is missing prints with the absent marker in both
    columns. The alternative — dropping it, or filling the date from the
    granule's own name — would make the table look complete and put an unstated
    inference on a government document.
    """
    if not pack.scenes:
        return (
            "<p>"
            + _absent(
                "no satellite granule id is recorded in this verdict's lineage, so "
                "no scene, date or cloud fraction can be listed"
            )
            + "</p>"
        )
    rows = "".join(
        "<tr>"
        f'<td class="mono">{_e(scene.scene_id)}</td>'
        f"<td>{_text_or_absent(scene.source)}</td>"
        f'<td class="mono">{_text_or_absent(scene.sensed_at)}</td>'
        f'<td class="num">'
        + (
            f'<span class="mono">{scene.cloud_pct:.2f}</span>'
            if scene.cloud_pct is not None
            else f'<span class="absent">{ABSENT}</span>'
        )
        + "</td>"
        '<td class="num">'
        + (
            f'<span class="mono">{scene.gsd_m:.1f}</span>'
            if scene.gsd_m is not None
            else f'<span class="absent">{ABSENT}</span>'
        )
        + "</td>"
        "</tr>"
        for scene in pack.scenes
    )
    unregistered = sum(1 for scene in pack.scenes if not scene.registered)
    note = (
        f'<p class="note"><span class="mono">{unregistered}</span> of '
        f'<span class="mono">{len(pack.scenes)}</span> granules named in this '
        "verdict&rsquo;s lineage have no row in the scene registry, so their "
        "acquisition date and cloud fraction are not known to this system. They "
        "are listed rather than omitted.</p>"
        if unregistered
        else ""
    )
    return (
        "<table><thead><tr><th>Granule id</th><th>Source</th>"
        '<th>Sensed at</th><th class="num">Cloud %</th><th class="num">GSD m</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>{note}"
    )


def _data_lineage(pack: EvidencePack) -> str:
    """Section 7 — design doc §21.3, item by item, absences marked as absences.

    §21.3 enumerates what every verdict must store: scene ids, their dates and
    cloud fractions, the DEM product and version, the index formulas' version,
    the model names and version tags, the engine version, the control-site ids
    and the computation timestamp. Each is looked up in the stored record and
    printed, or printed as `NOT RECORDED` with the reason. A missing row is the
    honest outcome and it is more useful than a blank cell, because it tells a
    reader that the item was required and looked for.
    """
    bundle = lineage_bundle(pack)
    families = _merged_family_lineage(pack)
    verdict = pack.verdict

    control_ids = _first(families, "control_ids")
    control_cell = (
        f'<span class="mono">{_e(", ".join(str(item) for item in control_ids))}</span>'
        if isinstance(control_ids, list) and control_ids
        else _absent(
            "no matched control site id is recorded, so the control-differenced "
            "comparison cannot be traced to specific sites"
        )
    )

    dem_product = _first(families, "dem_product")
    dem_version = _first(families, "dem_version")
    dem_cell = (
        f'<span class="mono">{_e(dem_product)} / {_e(dem_version)}</span>'
        if dem_product is not None and dem_version is not None
        else _absent("the terrain producer did not stamp a DEM product and version")
    )

    model_name = _first(families, "model_name")
    model_version = _first(families, "model_version")
    model_cell = (
        f'<span class="mono">{_e(model_name)} {_e(model_version)}</span>'
        if model_name is not None
        else _absent(
            "no photo-interpretation model tag is recorded; either no model ran or "
            "the producer did not stamp one"
        )
    )

    index_version = _first(families, "index_formula_version")
    index_name = _first(families, "index")
    index_cell = (
        f'<span class="mono">{_e(index_version)}</span>'
        if index_version is not None
        else _absent(
            "no producer stamped an index formula version into this verdict's "
            "lineage" + (f"; the index used was recorded as {index_name}" if index_name else "")
        )
    )

    grid = _first(families, "analysis_grid")
    grid_cell = (
        '<span class="mono">'
        + _e(", ".join(f"{key}={value}" for key, value in sorted(grid.items())))
        + "</span>"
        if isinstance(grid, dict) and grid
        else _absent("no analysis grid is recorded, so the sampling raster cannot be restated")
    )

    fingerprint = bundle.get("config_fingerprint")
    provenance = pack.verdict.lineage.get("provenance")

    rows = "".join(
        (
            _lineage_row(
                "Engine version",
                f'<span class="mono">{_e(verdict.engine_version)}</span>',
                f"stored column; this API runs {ENGINE_VERSION}",
            ),
            _lineage_row(
                "Engine config fingerprint",
                f'<span class="mono">{_e(fingerprint)}</span>'
                if fingerprint
                else _absent("no config fingerprint was stored with the canonical input"),
                "the frozen weights and thresholds in force when this verdict was computed",
            ),
            _lineage_row(
                "Computation timestamp",
                f'<span class="mono">{_e(pack.computed_at)}</span>',
                "stored column; not part of the verdict digest, which must not vary "
                "between two correct runs",
            ),
            _lineage_row(
                "Canonical input digest (SHA-256, 64 hex)",
                f'<span class="mono hash">{_e(verdict.bundle_digest)}</span>'
                if verdict.bundle_digest
                else _absent("this row predates digest storage (migration 0002)"),
                "SHA-256 over the canonical engine input",
            ),
            _lineage_row(
                "Verdict digest (SHA-256, 64 hex)",
                f'<span class="mono hash">{_e(verdict.verdict_digest)}</span>'
                if verdict.verdict_digest
                else _absent("this row predates digest storage (migration 0002)"),
                "SHA-256 over the decision-bearing fields",
            ),
            _lineage_row("DEM product and version", dem_cell, "terrain family provenance"),
            _lineage_row("Index formula version", index_cell, "satellite index derivation"),
            _lineage_row("Model name and version tag", model_cell, "photo family provenance"),
            _lineage_row("Control site ids", control_cell, "matched-control comparison"),
            _lineage_row("Analysis grid", grid_cell, "the raster the samples were taken on"),
            _lineage_row(
                "Data provenance",
                _text_or_absent(provenance),
                "whether these numbers came from measured imagery or a synthetic case",
            ),
            _lineage_row(
                "Frozen family weights",
                '<span class="mono">'
                + _e(
                    ", ".join(
                        f"{family} {weight:.2f}"
                        for family, weight in sorted(verdict.weights.items())
                    )
                )
                + "</span>"
                if verdict.weights
                else _absent("no weights were stored with this verdict"),
                "ADR-001; a documented assumption, not a fitted parameter",
            ),
        )
    )

    return (
        "<section><h2>7 · Data lineage</h2>"
        "<p>Everything this verdict recorded about how it was produced. Items "
        "required by the design&rsquo;s lineage guarantee that this record does "
        "not contain are printed as "
        f'<span class="absent">{ABSENT}</span> with the reason, never left blank '
        "and never inferred.</p>"
        "<table><thead><tr><th>Required item</th><th>Recorded value</th>"
        f"<th>What it is</th></tr></thead><tbody>{rows}</tbody></table>"
        "<h3>Satellite granules used</h3>"
        f"{_scene_table(pack)}"
        "</section>"
    )


def _ledger(pack: EvidencePack) -> str:
    """Section 8. The signature, or a plain statement that there is none.

    Hashes print at their stored 64-character length and are labelled SHA-256
    because that is what `services.audit.ledger.digest` computes. A digest shown
    at 16 characters is not the digest, and a digest labelled as an algorithm it
    is not makes the whole chain unverifiable by anyone who trusts the label.
    """
    if not pack.signatures:
        return (
            "<section><h2>8 · Ledger linkage</h2>"
            "<p>There is no ledger row for this verdict, therefore there is no "
            "signature, no signing officer and no row hash to print. This "
            "document is a provisional engine output. It becomes government "
            "evidence only when an officer holding the adjudication capability "
            "signs it, which appends a hash-chained row attributing the decision "
            "to a named account.</p></section>"
        )
    blocks = "".join(
        '<div class="sig">'
        f'<h3>Ledger row <span class="mono">{signature.id}</span> &mdash; '
        f"{_e(signature.decision)}</h3>"
        '<dl class="kv">'
        f"<dt>Signing officer</dt><dd>{_e(signature.officer_name)} "
        f'(<span class="mono">{_e(signature.officer_username)}</span>, '
        f"{_e(signature.officer_role)})</dd>"
        f"<dt>Decision</dt><dd>{_e(signature.decision)}</dd>"
        f"<dt>Corrected level</dt><dd>{_text_or_absent(signature.corrected_level)}</dd>"
        f"<dt>Reason</dt><dd>{_text_or_absent(signature.reason)}</dd>"
        f'<dt>Decided at</dt><dd class="mono">{_e(signature.decided_at)}</dd>'
        f'<dt>Row hash (SHA-256)</dt><dd class="mono hash">{_e(signature.row_hash)}</dd>'
        '<dt>Previous row hash (SHA-256)</dt><dd class="mono hash">'
        + (
            _e(signature.prev_hash)
            if signature.prev_hash is not None
            else "genesis &mdash; this is the first row in the chain"
        )
        + "</dd></dl></div>"
        for signature in pack.signatures
    )
    return (
        "<section><h2>8 · Ledger linkage</h2>"
        "<p>Each row is SHA-256 over its own content plus the previous "
        "row&rsquo;s hash. Both values are printed in full at their stored "
        "64-character length. UPDATE and DELETE on the ledger table are revoked "
        "from the application role in the database, so altering a historical row "
        "requires bypassing that control and leaves every subsequent link "
        "broken.</p>"
        f"{blocks}</section>"
    )


def _footer(pack: EvidencePack) -> str:
    return (
        "<footer>"
        f'<p>Generated <span class="mono">{_e(pack.generated_at)}</span> for '
        f'<span class="mono">{_e(pack.generated_for)}</span> from claim '
        f'<span class="mono">{pack.claim.claim_id}</span>, verdict '
        f'<span class="mono">{pack.verdict.id}</span> version '
        f'<span class="mono">{pack.verdict.version}</span>.</p>'
        "<p>This document reads stored evidence only. No verdict, confidence, "
        "agreement value or digest on these pages was recomputed while producing "
        "it, so it cannot disagree with the record it documents. To test whether "
        "the stored verdict still reproduces under today&rsquo;s engine, use the "
        "recompute endpoint, which is read-only and writes no new verdict.</p>"
        "</footer>"
    )


def render_html(pack: EvidencePack) -> str:
    """One self-contained HTML document. No network resource of any kind.

    Section order is the argument: limitations, then the claim, then the verdict,
    then the evidence, then the dissent, then the provenance, then the signature.
    A reader who stops early has read the caveats rather than only the finding.
    """
    title = f"PRAMAAN Evidence Pack — {pack.claim.unique_id}"
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title>"
        f"<style>{_STYLE}</style></head><body>"
        f"{_masthead(pack)}"
        f"{_limitations(pack)}"
        f"{_the_claim(pack)}"
        f"{_the_verdict(pack)}"
        f"{_evidence(pack)}"
        f"{_dissent(pack)}"
        f"{_alternatives(pack)}"
        f"{_data_lineage(pack)}"
        f"{_ledger(pack)}"
        f"{_footer(pack)}"
        "</body></html>"
    )


__all__ = ["ABSENT", "Limitation", "render_html"]
