"""The mandatory limitations block. Page one, never an appendix.

FR-9.5: *"limitations section is not removable"*. design doc §35.3 is blunter —
a mandatory limitations block listing, in plain language, what the system could
not determine **about this specific structure**. Not a boilerplate disclaimer:
half of what follows is computed from this claim's own stored gates, families
and dissent, so two Evidence Packs do not carry the same limitations.

## Why it is first and not last

An appendix is a place to put something you have complied with rather than
communicated. The document's argument is that the assessment is bounded, and a
reader who meets the verdict before the bounds has already formed a view. This
is the same rule the detail screen enforces by refusing to collapse the dissent
panel.

## The two kinds of entry

`universal=True` entries are refusals of the engine itself — they would appear
on every pack ever produced, and they are printed anyway, because a reader
holding one document cannot know what is on the others.

`universal=False` entries are findings about this claim: which gate it failed,
which families were unavailable, what the engine dissented on, whether anyone
has signed it. These are the entries a boilerplate block would lose.

Sources: design doc §16.4 ("What we explicitly refuse to claim") for the four
refusals, §16.2 STEP 3 for the detectability gate, §21.3 for the ladder ceiling,
and `docs/17-roles-and-ledger.md` for what the hash chain does and does not
prove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.reconcile.types import Level
from app.services.reports.pack import EvidencePack, lineage_bundle

#: The nominal 30 m tier the problem statement names (HLS L30/S30, Landsat OLI,
#: Resourcesat LISS-III at 23.5 m resampled). Imported as a number rather than
#: written into prose so the sentence cannot drift from the gate that used it —
#: and read from the claim's own stored gates when those disagree with the
#: default, which is the case a hardcoded "30 m" would misreport.
PIXEL_AREA_30M_M2 = 900.0

#: The highest level the engine can emit. `Level` has no L5 member at all, so
#: this is a fact about the type rather than a configured cap.
CEILING = Level.L4_CONTROL_DIFFERENCED


@dataclass(frozen=True, slots=True)
class Limitation:
    """One entry in the limitations block.

    `items` carries list-shaped content — dissent lines, unavailable families —
    so the renderer can mark them up as a list instead of flattening evidence
    into a paragraph where the eye skips it.
    """

    heading: str
    body: str
    items: tuple[str, ...] = field(default_factory=tuple)
    #: True when the entry is a refusal of the engine, false when it is a
    #: finding about this claim. Both are printed; the distinction is labelled.
    universal: bool = True


def _gates(pack: EvidencePack) -> dict[str, Any]:
    """The stored gate record for this claim.

    Read from the lineage's canonical bundle, which is the copy the verdict's
    digest covers. `producers.gates` carries the same values plus
    `footprint_pixels`; it is merged under the canonical copy so the derived
    pixel count can be printed when it was recorded, without letting the
    uncovered copy override a digest-covered value.
    """
    canonical = lineage_bundle(pack).get("gates")
    canonical = canonical if isinstance(canonical, dict) else {}
    producers = pack.verdict.lineage.get("producers")
    extra = producers.get("gates") if isinstance(producers, dict) else None
    merged: dict[str, Any] = dict(extra) if isinstance(extra, dict) else {}
    merged.update(canonical)
    return merged


def _detectability(pack: EvidencePack) -> Limitation:
    """What the sensor could and could not have seen at this structure's size.

    The single most important gate in the system (design doc §16.2 STEP 3) and
    the one whose absence produces a false accusation against a named field
    officer: if a structure is below the detection limit then "we looked and saw
    nothing" carries no information, and the document has to say so in the
    structure's own numbers rather than as a general caveat.
    """
    gates = _gates(pack)
    passed = gates.get("detectability_passed")
    footprint = gates.get("expected_footprint_m2")
    pixel_area = gates.get("pixel_area_m2")
    pixels = gates.get("footprint_pixels")
    escalated = bool(gates.get("escalated_to_cluster"))

    if not isinstance(pixel_area, int | float):
        # The gate is not renderable in this claim's own numbers. Say that,
        # rather than printing the default 900 m2 as if it had been the value in
        # force — a plausible number nobody measured is the worst option here.
        return Limitation(
            heading="Sensor detectability limit — not recorded for this claim",
            body=(
                "This system assesses surface change at the 30 m tier, where one "
                f"pixel covers about {PIXEL_AREA_30M_M2:.0f} m². The pixel area "
                "actually applied to this claim was not recorded in the verdict's "
                "lineage, so whether this structure passed the detectability gate "
                "cannot be stated from the record. Absence of a satellite signature "
                "must not be read as evidence against the claim."
            ),
            universal=False,
        )

    arithmetic = f"{float(pixel_area):.0f} m² per pixel"
    if isinstance(footprint, int | float):
        ratio = (
            float(pixels)
            if isinstance(pixels, int | float)
            else float(footprint) / float(pixel_area)
        )
        arithmetic = (
            f"expected footprint {float(footprint):.0f} m² is {ratio:.2f} pixels at "
            f"{float(pixel_area):.0f} m² per pixel"
        )

    if passed is True:
        return Limitation(
            heading="Sensor detectability limit — this claim PASSED the gate",
            body=(
                f"Assessment is at the 30 m tier: {arithmetic}. The structure is large "
                "enough to be individually assessable, so a satellite finding about it "
                "carries information. This does not extend to anything smaller at the "
                "same site, and it does not make the imagery a measurement of the "
                "structure itself — it is a measurement of the surface over it."
            ),
            universal=False,
        )

    escalation = (
        " Per-structure satellite assessment was therefore disabled and the site was "
        "assessed as part of a cluster of neighbouring works instead, so any satellite "
        "or temporal finding below describes the cluster, not this structure."
        if escalated
        else " Per-structure satellite assessment was therefore disabled, and cluster "
        "escalation was not available, so no satellite finding about this structure was "
        "possible at all."
    )
    return Limitation(
        heading="Sensor detectability limit — this claim DID NOT PASS the gate",
        body=(
            f"Assessment is at the 30 m tier: {arithmetic}, below the minimum at which a "
            f"change in this structure could move a pixel's aggregate reflectance beyond "
            f"noise.{escalation} Absence of a satellite signature is not evidence that the "
            "structure is absent, and this report must not be read as if it were."
        ),
        universal=False,
    )


def _coverage(pack: EvidencePack) -> Limitation | None:
    """Which of the six families said nothing, and what that cost.

    Returned as a limitation rather than left to the evidence table because an
    unavailable family lowers coverage, coverage multiplies into confidence, and
    a reader looking at a low confidence figure deserves to know it reflects
    missing evidence rather than disagreeing evidence.
    """
    missing = tuple(f.family for f in pack.families if not f.available)
    if not missing:
        return None
    return Limitation(
        heading=f"{len(missing)} of {len(pack.families)} evidence families were unavailable",
        body=(
            "An unavailable family is not read as a neutral reading — it lowers "
            f"coverage, which is stored on this verdict as {pack.verdict.coverage:.4f} and "
            f"multiplies into its confidence of {pack.verdict.confidence:.4f}. The figure is "
            "low because evidence is missing, not because the evidence present "
            "disagreed. Each family's own recorded reason appears in the evidence "
            "section."
        ),
        items=missing,
        universal=False,
    )


def _dissent(pack: EvidencePack) -> Limitation:
    """The verdict's stored counter-evidence, verbatim.

    A verdict without stated counter-evidence is not shippable (design doc §16.2
    STEP 11), so an empty list here is a defect in the record and is printed as
    one. The alternative — omitting the section when the list is empty — would
    make the strongest evidence of a defective verdict invisible.
    """
    if pack.verdict.dissent:
        return Limitation(
            heading="Dissenting and counter-evidence recorded for this verdict",
            body=(
                "Stored on the verdict row and reproduced verbatim. These are the "
                "reasons the engine itself recorded against its own finding."
            ),
            items=tuple(pack.verdict.dissent),
            universal=False,
        )
    return Limitation(
        heading="No dissent was recorded for this verdict — this is a defect",
        body=(
            "Every verdict this system issues is required to carry stated "
            "counter-evidence. This one carries none, which is a fault in the record "
            "rather than a sign that no counter-evidence exists. Treat the finding "
            "below as unreviewed on that point."
        ),
        universal=False,
    )


def _provisional(pack: EvidencePack) -> Limitation | None:
    """PROVISIONAL, stated as a limitation and not only as a header band.

    Returns None once a named officer has signed, because the pack then prints
    the signature and the ledger linkage instead. The reference prototype this
    project is measured against prints "SIGNED RECORD" above an officer decision
    of "UNDER REVIEW"; the two statements cannot both be true, and the honest
    version of that document is this one.
    """
    if pack.signed:
        return None
    return Limitation(
        heading="PROVISIONAL — no officer has signed this assessment",
        body=(
            "This document reproduces an engine output that no authorised officer "
            "has accepted, edited or rejected. It is not a signed record, it is not "
            "government evidence, and it must not be cited as either. The "
            f"verdict's stored status is '{pack.verdict.status}'. A verdict becomes "
            "evidence only when an officer with the adjudication capability signs it, "
            "which appends a row to the hash-chained ledger and is the single write in "
            "this system that clears the word PROVISIONAL."
        ),
        universal=False,
    )


def _status_disagreement(pack: EvidencePack) -> Limitation | None:
    """The verdict status and the ledger do not agree — an integrity finding.

    Normally impossible: `ledger.append` writes the row and sets the status in
    one transaction. If it has happened, one of the two was written outside that
    path, and a document that quietly trusted whichever it preferred would be
    concealing exactly the kind of defect it exists to expose.
    """
    if not pack.status_disagrees:
        return None
    if pack.signed:
        detail = (
            f"the adjudication ledger holds {len(pack.signatures)} signature(s) for this "
            f"verdict, but its stored status is '{pack.verdict.status}' rather than "
            "'adjudicated'"
        )
    else:
        detail = (
            "the verdict's stored status is 'adjudicated', but the adjudication ledger "
            "holds no signature for it"
        )
    return Limitation(
        heading="Record integrity finding — status and ledger disagree",
        body=(
            f"On this record, {detail}. These two are written in a single transaction by "
            "the only code path that may sign a verdict, so a disagreement means one of "
            "them was written by something else. This report states the ledger's answer "
            "and flags the conflict rather than resolving it."
        ),
        universal=False,
    )


def _refusals() -> tuple[Limitation, ...]:
    """design doc §16.4, reproduced faithfully. These bound every finding here."""
    return (
        Limitation(
            heading=f"Ceiling is {CEILING.value} — no causal claim is made",
            body=(
                "The strongest level this system can reach is control-differenced "
                "observation: the site changed, same season, year on year, more than "
                "comparable un-intervened land did. That is not attribution. A rising "
                "vegetation index near a check dam does not prove the check dam caused "
                "it. There is no L5 and no causal level in this engine's ladder — the "
                "level is absent from the type itself, so no code path can construct "
                "one. Attribution requires a designed evaluation with field "
                "measurement, which this system does not perform and does not claim to."
            ),
        ),
        Limitation(
            heading="Absence of evidence is not evidence of absence",
            body=(
                "Where a structure is below the sensor's detection limit, or a season's "
                "imagery was unusable, the system observed nothing — which is not the "
                "same as observing that nothing is there. This is enforced as a hard "
                "gate rather than offered as guidance, because the failure mode is a "
                "false accusation against a named field officer."
            ),
        ),
        Limitation(
            heading="A single date is never a trend",
            body=(
                "The engine refuses to emit a temporal finding on fewer than the "
                "configured minimum of usable scenes per window, and comparisons are "
                "always same-season and year-over-year. A cross-season delta is not a "
                "weaker comparison, it is a category error, and the engine cannot "
                "construct one."
            ),
        ),
        Limitation(
            heading="Satellite agreement does not authenticate the photograph",
            body=(
                "Independent evidence validates the surface state, not the provenance "
                "of the field photograph. A genuine photograph can accompany an "
                "ineffective structure, and a fraudulent one can sit beside a site that "
                "changed for unrelated reasons. Both cases appear in the dissent above. "
                "The photograph is weighted lowest of the six families precisely "
                "because it is the claim's own source."
            ),
        ),
        Limitation(
            heading="The ledger proves integrity, not authenticity",
            body=(
                "Each adjudication row is SHA-256 over its own content plus the "
                "previous row's hash, which detects alteration, deletion or reordering "
                "of any historical row. It does not prove which officer physically "
                "pressed the key: per-officer signing keys are not implemented, and "
                "the word 'signed' in this document means 'attributed to a named "
                "authenticated account', not 'cryptographically non-repudiable'."
            ),
        ),
        Limitation(
            heading="No conclusion is drawn about any person",
            body=(
                "The strongest recommendation this system emits is that a site "
                "requires physical verification. It never concludes that a claim was "
                "falsified or that an individual acted improperly, and no part of this "
                "document may be presented as such a finding."
            ),
        ),
    )


def limitations_for(pack: EvidencePack) -> tuple[Limitation, ...]:
    """The complete limitations block for one pack, in reading order.

    Claim-specific entries come first — PROVISIONAL before anything else, so a
    reader who stops after one paragraph has read the one that matters most —
    then the engine's standing refusals. Nothing here is optional and nothing is
    suppressed when it happens to be inconvenient: an empty dissent list and a
    status/ledger disagreement both print as defects.
    """
    specific = (
        _provisional(pack),
        _status_disagreement(pack),
        _detectability(pack),
        _dissent(pack),
        _coverage(pack),
    )
    return tuple(item for item in specific if item is not None) + _refusals()
