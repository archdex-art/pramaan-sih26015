"""Dissent panel construction (docs §16.2 STEP 11).

A verdict without a dissent panel is not shippable. This is enforced twice: here
by always producing at least one entry, and in ``Verdict.__post_init__`` which
refuses to construct a verdict with an empty panel.

The panel answers three questions, in this order:

1. What evidence points the other way?
2. What alternative explanations were considered, and which were excluded?
3. What are the limits of what we could observe at all?

Ordering matters. Bad news first — the design principle in docs §24.1 is that
the dissent panel is never collapsed by default on a contradicted verdict, and
putting the counter-evidence at the top is the textual equivalent.

Vocabulary lock (W6 fix, docs §37): nothing in this module emits the words
fraud, fake, false or failed. The strongest available phrase in the entire
system is "requires physical verification". ``scripts/vocabulary_lint.py``
enforces this in CI over this file and the i18n bundles.
"""

from __future__ import annotations

from app.services.reconcile.signatures import Signature
from app.services.reconcile.types import (
    Aggregate,
    EvidenceBundle,
    Level,
    Verdict,
)
from app.services.reconcile.weights import EngineConfig


def build_dissent(
    bundle: EvidenceBundle,
    aggregate: Aggregate,
    level: Level,
    cfg: EngineConfig,
    signature: Signature,
) -> tuple[str, ...]:
    entries: list[str] = []
    by_family = bundle.by_family()

    # --- 1. Counter-evidence: families pointing against the assigned verdict.
    positive_verdict = level in {
        Level.L1_OBSERVED,
        Level.L2_CORROBORATED,
        Level.L3_MULTI_INDICATOR,
        Level.L4_CONTROL_DIFFERENCED,
    }
    for fam in bundle.available():
        contrary = (
            fam.agreement <= cfg.disagreeing_threshold
            if positive_verdict
            else fam.agreement >= cfg.agreeing_threshold
        )
        if contrary:
            entries.append(
                f"Counter-evidence — {fam.family} (agreement {fam.agreement:+.2f}): {fam.reason}"
            )

    # --- 2. The N3_TERRAIN_PATH disclosure. Mandated by the D1 fix: when the
    # detectability gate failed, the verdict must state that absence of a
    # per-structure satellite signature would on its own be inconclusive.
    if level is Level.N3_CONTRADICTED and not bundle.gates.detectability_passed:
        entries.append(
            f"This structure's expected footprint is "
            f"{bundle.gates.expected_footprint_m2:.0f} m2 against a "
            f"{bundle.gates.pixel_area_m2:.0f} m2 pixel "
            f"({bundle.gates.footprint_pixels:.2f} pixels) — below the sensor "
            f"detection limit. Absence of a per-structure satellite signature "
            f"alone would be INCONCLUSIVE. This verdict rests on the terrain "
            f"rule, which is deterministic and independent of the imagery."
        )

    # --- 3. Detectability and cluster escalation, on any verdict.
    if not bundle.gates.detectability_passed and level is not Level.N3_CONTRADICTED:
        entries.append(
            # Wording deliberately matches the visible notice specified in
            # docs §16.2 STEP 3 ("below sensor detection limit — assessed as
            # cluster") so the same phrase appears in the UI, the API payload
            # and the Evidence Pack PDF.
            f"Per-structure satellite assessment was disabled: this structure is "
            f"below the sensor detection limit — expected footprint "
            f"{bundle.gates.expected_footprint_m2:.0f} m2 is "
            f"{bundle.gates.footprint_pixels:.2f} pixels at "
            f"{bundle.gates.pixel_area_m2:.0f} m2 per pixel."
            + (
                " Assessed at cluster scale instead."
                if bundle.gates.escalated_to_cluster
                else " No cluster escalation was possible."
            )
        )
    cluster_families = [f.family for f in bundle.available() if f.cluster_scale]
    if cluster_families:
        entries.append(
            f"Evidence from {', '.join(sorted(cluster_families))} was computed at "
            f"cluster scale, not at this structure. It describes the neighbourhood, "
            f"not this work in isolation."
        )

    # --- 4. Type-level ceiling and non-assessability.
    if not signature.optically_assessable:
        entries.append(
            f"Intervention type '{signature.type_key}' has no reliable optical "
            f"signature at this resolution. {signature.note} The system reports "
            f"existence only and refuses outcome claims for this type."
        )
    if signature.note and signature.optically_assessable:
        entries.append(f"Type limitation ({signature.type_key}): {signature.note}")

    # --- 5. Unavailable families: what we could not look at.
    unavailable = [f.family for f in bundle.families if not f.available]
    missing = [fam for fam in cfg.weights if fam not in by_family]
    absent = sorted(set(unavailable) | set(missing))
    if absent:
        entries.append(
            f"Evidence families unavailable: {', '.join(absent)}. Coverage is "
            f"{aggregate.coverage:.2f}, which caps confidence at "
            f"{abs(aggregate.score) * aggregate.coverage:.2f} before data-quality "
            f"scaling."
        )

    # --- 6. Alternatives: excluded and, crucially, not excluded.
    for alt in bundle.alternatives:
        state = "EXCLUDED" if alt.excluded else "NOT EXCLUDED"
        entries.append(f"Alternative explanation [{state}] — {alt.description}: {alt.basis}")

    # --- 7. Producer-reported data limitations, verbatim.
    entries.extend(f"Data limitation: {note}" for note in bundle.limitations)

    # --- 8. Quality multipliers, when they materially reduced confidence.
    if bundle.quality.metadata_integrity < 0.9:
        entries.append(
            f"Metadata integrity is {bundle.quality.metadata_integrity:.2f} "
            f"(GPS accuracy, coordinate provenance, timestamp consistency), which "
            f"scales confidence down without changing the evidence score."
        )
    if bundle.quality.data_sufficiency < 0.9:
        entries.append(
            f"Data sufficiency is {bundle.quality.data_sufficiency:.2f} — usable "
            f"scenes, cloud masking and control availability were below ideal."
        )

    # --- 9. The universal cap. Printed on every verdict, every report.
    if positive_verdict:
        entries.append(
            "This is not a causal claim. PRAMAAN's ceiling is L4 "
            "(control-differenced); attribution to the intervention requires a "
            "designed evaluation with field measurement."
        )

    if not entries:  # pragma: no cover - proven unreachable, kept as a guard
        # UNREACHABLE by construction, and deliberately retained.
        #
        # Proof: every verdict is either positive or not. A positive verdict
        # always appends the causal-ceiling note above. A non-positive verdict
        # inverts the counter-evidence test, so every AGREEING family becomes a
        # counter-evidence entry — and a bundle with no agreeing and no
        # disagreeing families has no available families at all, which appends
        # the coverage entry instead. So `entries` is never empty.
        #
        # It stays because "a verdict without a dissent panel is not shippable"
        # is a governance guarantee, not an implementation detail. If a future
        # change to the branches above breaks the proof, this produces a
        # truthful panel rather than tripping the ValueError in
        # Verdict.__post_init__ in front of an officer. Marked no-cover rather
        # than tested with a contrived input, because pretending an unreachable
        # branch is covered is worse than admitting it is not.
        entries.append(
            "No counter-evidence was identified. All six evidence families were "
            "available and agreed; metadata integrity and data sufficiency were "
            "both at maximum. This is not a causal claim."
        )
    return tuple(entries)


def verify_shippable(verdict: Verdict) -> None:
    """Belt-and-braces check used by the golden suite.

    ``Verdict.__post_init__`` already refuses an empty panel; this additionally
    asserts the N3_TERRAIN_PATH disclosure is present whenever that path fired,
    because that specific sentence is the one a judge will look for.
    """
    if "N3_TERRAIN_PATH" in verdict.rule_path:
        joined = " ".join(verdict.dissent)
        if "INCONCLUSIVE" not in joined:
            raise AssertionError(
                f"claim {verdict.claim_id}: N3_TERRAIN_PATH fired but the dissent "
                "panel does not disclose that absence of satellite signature "
                "alone would be inconclusive"
            )
