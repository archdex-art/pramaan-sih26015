"""Photo labels -> engine evidence, including the scene-scale gate.

This module implements **docs §16.2 STEP 5**, which was specified but not
previously enforced anywhere in the engine: `scene_scale` was recorded in the
verdict's lineage and then ignored. It now acts.

## What the scene-scale gate is for

A close-up photograph of a pipe outlet with water flowing out of it is strong
evidence that water was present at that pipe at that moment. It is *not*
evidence about a 900 m² pixel, and it cannot be cross-checked against one. The
design document puts it plainly: "a close-up cannot be satellite-corroborated →
its satellite agreement contribution is nulled, not counted as disagreement."

The subtlety is *where* to apply it. PRAMAAN's six families are scored
independently against the expected signature, never against each other, and that
independence is what makes `coverage` interpretable. So the gate must not reach
across and mutate the satellite family — that would break the model and make two
families covary silently.

Instead it attenuates the **photo** family, which is the family whose claim is
scale-limited. A close-up gets a reduced-magnitude agreement, because it
evidences existence at capture time but says nothing about extent, persistence
or location within the uncertainty disk. The attenuation is recorded in the
evidence reason and in the lineage, so an officer reading the Evidence Pack sees
why the photograph counted for less.

## Why the photo family can never carry a contradiction alone

`photo` is the only family that is not independent of the claim — the
photograph *is* the claim's source (ADR-001). A negative photo reading is
therefore capped in magnitude: it can withhold support, but it must not by
itself drive a claim towards CONTRADICTED. The engine's N3 paths already require
independent families, and this cap is the producer-side half of that guarantee.
"""

from __future__ import annotations

from app.services.photo.types import PhotoLabels
from app.services.reconcile.signatures import Signature, signature_for
from app.services.reconcile.types import FamilyEvidence

#: Multiplier applied to a close-up's agreement magnitude. Not zero: a close-up
#: still evidences that something consistent with the claim existed. Not one: it
#: cannot speak to extent or persistence.
CLOSE_UP_ATTENUATION = 0.4

#: Applied when the model is unsure of its own scene-scale call. Treated as
#: close-up-like, because guessing "mid" must never be the cheap way to unlock
#: full-weight photo evidence.
UNKNOWN_SCALE_ATTENUATION = 0.5

#: Below this, the scene_scale call is not trusted and the unknown-scale
#: attenuation applies regardless of what the model said.
SCENE_SCALE_MIN_CONFIDENCE = 0.6

#: Ceiling on how negative the photo family may go. `photo` is not independent
#: of the claim, so it withholds support rather than contradicting.
MAX_NEGATIVE_AGREEMENT = -0.5

#: Structure classes that corroborate each intervention type. The photo model
#: distinguishes fewer classes than the DRISHTI taxonomy on purpose (a farm pond
#: and a percolation tank are indistinguishable at close range), so this maps
#: many intervention types onto one visual class.
_TYPE_TO_VISUAL_CLASS: dict[str, tuple[str, ...]] = {
    "check_dam": ("masonry_check_dam", "earthen_bund"),
    "percolation_tank": ("excavated_pond_or_tank", "earthen_bund"),
    "farm_pond": ("excavated_pond_or_tank",),
    "nala_bund": ("earthen_bund", "masonry_check_dam"),
    "earthen_bund": ("earthen_bund",),
    "contour_bund": ("earthen_bund", "trench_or_contour_work"),
    "contour_trench": ("trench_or_contour_work",),
    "staggered_trench": ("trench_or_contour_work",),
    "gully_plug": ("gully_plug", "masonry_check_dam"),
    "plantation": ("plantation",),
    "horticulture": ("plantation",),
    "waterbody_renovation": ("excavated_pond_or_tank",),
    "recharge_shaft": ("well_or_borewell", "other_structure"),
    "dug_well": ("well_or_borewell",),
    "borewell": ("well_or_borewell",),
    "livestock": (),
    "livelihood": (),
    "other": (),
}


def _scale_factor(labels: PhotoLabels) -> tuple[float, str]:
    """Attenuation multiplier for this frame, and the reason to print."""
    if labels.scene_scale_confidence < SCENE_SCALE_MIN_CONFIDENCE:
        return (
            UNKNOWN_SCALE_ATTENUATION,
            f"scene scale reported as '{labels.scene_scale}' but only at "
            f"confidence {labels.scene_scale_confidence:.2f}, below the "
            f"{SCENE_SCALE_MIN_CONFIDENCE:.2f} trust floor, so the frame is "
            f"treated as scale-indeterminate",
        )
    if labels.scene_scale == "close_up":
        return (
            CLOSE_UP_ATTENUATION,
            "close-up frame: evidences that something consistent with the claim "
            "existed at capture time, but cannot speak to extent or persistence "
            "and cannot be cross-checked against a 30 m pixel",
        )
    if labels.scene_scale == "unknown":
        return (
            UNKNOWN_SCALE_ATTENUATION,
            "scene scale could not be determined, so the frame is treated as "
            "scale-indeterminate rather than assumed to be site-scale",
        )
    return 1.0, ""


def _raw_agreement(labels: PhotoLabels, signature: Signature) -> tuple[float, list[str], int]:
    """Score the photo against the type's expected visual signature.

    Returns (agreement before attenuation, reasons, number of decided labels).
    """
    reasons: list[str] = []
    score = 0.0
    weight = 0.0
    decided = 0

    expected_classes = _TYPE_TO_VISUAL_CLASS.get(signature.type_key, ())

    # --- Structure presence and type: the primary signal.
    if labels.decided("structure_present"):
        decided += 1
        weight += 0.5
        if labels.says_yes("structure_present"):
            observed_value = str(labels.extra.get("structure_type_value", "")) or None
            # The visual class travels in `extra` because structure_type is an
            # enum, not a probability, and LabelPrediction models binary heads.
            if observed_value and expected_classes:
                if observed_value in expected_classes:
                    score += 0.5
                    reasons.append(
                        f"structure visible and its visual class "
                        f"'{observed_value}' matches what a "
                        f"{signature.type_key} looks like"
                    )
                elif observed_value in ("uncertain", "none"):
                    reasons.append("structure visible but its class could not be determined")
                else:
                    score -= 0.25
                    reasons.append(
                        f"structure visible but its visual class "
                        f"'{observed_value}' is not consistent with a "
                        f"{signature.type_key}"
                    )
            else:
                score += 0.3
                reasons.append("a built structure is visible")
        else:
            score -= 0.5
            reasons.append("no built structure is visible in the frame")
    else:
        reasons.append("structure presence abstained")

    # --- Water: only meaningful for types whose signature expects it.
    expects_water = any(
        idx in ("MNDWI", "water_persistence_months", "max_water_extent")
        for idx in signature.expect_increase
    )
    if expects_water and labels.decided("water_present"):
        decided += 1
        weight += 0.35
        if labels.says_yes("water_present"):
            score += 0.35
            reasons.append("standing or flowing water is visible, as expected for this type")
        else:
            # Dry is weak negative evidence, not proof: a check dam photographed
            # in summer is legitimately dry.
            score -= 0.15
            reasons.append(
                "no water visible — weak evidence only, since this type is "
                "legitimately dry outside the post-monsoon window"
            )
    elif expects_water:
        reasons.append("water presence abstained")

    # --- Vegetation: for types whose signature expects an NDVI rise.
    expects_veg = "NDVI" in signature.expect_increase
    veg_value = str(labels.extra.get("vegetation_density_value", "")) or None
    if expects_veg and veg_value and veg_value != "uncertain":
        decided += 1
        weight += 0.25
        if veg_value in ("moderate", "dense"):
            score += 0.25
            reasons.append(f"vegetation cover is {veg_value}, consistent with this type")
        elif veg_value == "none":
            score -= 0.15
            reasons.append("no vegetation cover visible where this type expects cover")
        else:
            reasons.append(f"vegetation cover is {veg_value}")

    # --- Erosion: contradicts soil-conservation types when still active.
    expects_bsi_drop = "BSI" in signature.expect_decrease
    if expects_bsi_drop and labels.decided("erosion_visible"):
        decided += 1
        weight += 0.2
        if labels.says_yes("erosion_visible"):
            score -= 0.2
            reasons.append(
                "active erosion is still visible at a site whose intervention "
                "is intended to arrest it"
            )
        else:
            score += 0.1
            reasons.append("no active erosion visible")

    if weight == 0.0:
        return 0.0, reasons, decided
    # Normalise to [-1, 1] by the weight actually exercised, so a frame that
    # answered two questions is not penalised against one that answered four.
    return max(-1.0, min(1.0, score / weight)), reasons, decided


def to_family_evidence(
    labels: PhotoLabels,
    intervention_type: str,
) -> FamilyEvidence:
    """Build the `photo` evidence family. Pure; no IO.

    Returns an *unavailable* family when every label the type cares about
    abstained. Unavailable, not neutral: an abstaining model has told us nothing,
    and coverage must record that rather than reading it as agreement of zero
    (ADR-001, invariant I4).
    """
    signature = signature_for(intervention_type)

    if not signature.optically_assessable and signature.type_key in ("livestock", "livelihood"):
        return FamilyEvidence(
            family="photo",
            agreement=0.0,
            available=False,
            reason=(
                f"Intervention type '{intervention_type}' has no visual signature "
                f"this label set can score. {signature.note}"
            ),
            lineage=labels.lineage(),
        )

    raw, reasons, decided = _raw_agreement(labels, signature)

    if decided == 0:
        return FamilyEvidence(
            family="photo",
            agreement=0.0,
            available=False,
            reason=(
                "Every label relevant to this intervention type abstained "
                f"(abstained: {', '.join(labels.abstained_keys()) or 'none decided'}). "
                "Reported unavailable rather than neutral, so coverage records "
                "that the photograph told us nothing."
            ),
            lineage=labels.lineage(),
        )

    factor, scale_reason = _scale_factor(labels)
    agreement = raw * factor
    agreement = max(MAX_NEGATIVE_AGREEMENT, min(1.0, agreement))

    parts = list(reasons)
    if scale_reason:
        parts.append(f"Scene-scale gate applied ({factor:.2f}x): {scale_reason}")
    if raw < 0 and agreement == MAX_NEGATIVE_AGREEMENT:
        parts.append(
            f"Negative photo agreement capped at {MAX_NEGATIVE_AGREEMENT}: the "
            "photograph is the claim's own source, so it may withhold support "
            "but must not by itself drive a contradicted verdict"
        )

    lineage = labels.lineage()
    lineage.update(
        {
            "raw_agreement": raw,
            "scene_scale_factor": factor,
            "labels_decided": decided,
            "negative_cap": MAX_NEGATIVE_AGREEMENT,
        }
    )

    return FamilyEvidence(
        family="photo",
        agreement=round(agreement, 4),
        available=True,
        reason="; ".join(parts),
        lineage=lineage,
    )
