"""Tests for verdict persistence and lineage reconstruction.

The property under test is docs §21.3: *"a verdict can be recomputed
byte-identically from its lineage record."* These tests exercise it without a
database; `tests/integration/` covers the same path through real Postgres.

Two failure modes get specific attention because both would produce false
reassurance rather than an error:

* a lineage that rebuilds a *slightly different* bundle and still reports
  "identical";
* a recompute under today's engine config reporting "identical" for a verdict
  computed under different weights.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import all_agreeing, bundle, fam, gates  # noqa: E402

from app.services.audit import (  # noqa: E402
    LineageIncomplete,
    bundle_from_lineage,
    compare_verdicts,
    config_from_lineage,
    evidence_rows,
    verdict_row,
)
from app.services.reconcile import Alternative, EngineConfig, reconcile  # noqa: E402


def built(**kw: object):  # type: ignore[no-untyped-def]
    b = bundle(**kw)  # type: ignore[arg-type]
    return b, reconcile(b)


# --- the round trip ------------------------------------------------------


def test_verdict_recomputes_identically_from_its_lineage() -> None:
    """The core guarantee, without a database in the way."""
    b, v = built(families=all_agreeing(1.0))
    row = verdict_row(v, b, claim_id=1, version=1)
    recomputed = reconcile(bundle_from_lineage(row["lineage"]), config_from_lineage(row["lineage"]))
    result = compare_verdicts(v, recomputed)
    assert result.identical
    assert result.differences == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"families": all_agreeing(1.0)},
        {"families": all_agreeing(-1.0)},
        {"families": (fam("terrain", -1.0), fam("photo", 0.4))},
        {"families": all_agreeing(0.5), "metadata_integrity": 0.31},
        {"families": all_agreeing(0.0)},
    ],
)
def test_round_trip_holds_across_verdict_shapes(kwargs: dict) -> None:
    """Positive, negative, sparse, low-quality and neutral bundles alike."""
    b = bundle(**kwargs)
    v = reconcile(b)
    row = verdict_row(v, b, claim_id=7, version=3)
    assert compare_verdicts(v, reconcile(bundle_from_lineage(row["lineage"]))).identical


def test_round_trip_preserves_gates_and_cluster_scale() -> None:
    """A cluster-scale reading must not silently become a per-structure one."""
    b = bundle(
        families=(
            fam("terrain", -1.0),
            fam("satellite", -1.0, cluster_scale=True),
            fam("temporal", -1.0, cluster_scale=True),
            fam("photo", 0.4),
        ),
        gate=gates(passed=False, footprint_m2=625.0, escalated=True),
        alternatives=(Alternative(description="gps error", excluded=True, basis="14 m vs 340 m"),),
    )
    v = reconcile(b)
    row = verdict_row(v, b, claim_id=1, version=1)
    rebuilt = bundle_from_lineage(row["lineage"])

    assert not rebuilt.gates.detectability_passed
    assert rebuilt.gates.escalated_to_cluster
    assert rebuilt.gates.expected_footprint_m2 == 625.0
    assert {f.family for f in rebuilt.families if f.cluster_scale} == {
        "satellite",
        "temporal",
    }
    assert rebuilt.excluded_alternatives()
    assert compare_verdicts(v, reconcile(rebuilt)).identical


def test_round_trip_preserves_the_n3_terrain_path() -> None:
    """The flagship demo verdict must survive storage exactly."""
    b = bundle(
        claim_id="GOLD-21",
        intervention_type="farm_pond",
        families=(
            fam("terrain", -1.0),
            fam("satellite", -1.0, cluster_scale=True),
            fam("temporal", -1.0, cluster_scale=True),
            fam("control", 0.0),
            fam("photo", 0.4),
            fam("context", 0.0),
        ),
        gate=gates(passed=False, footprint_m2=625.0, escalated=True),
        metadata_integrity=0.70,
        data_sufficiency=0.80,
        alternatives=(
            Alternative(description="gps error", excluded=True, basis="cannot move 340 m"),
        ),
    )
    v = reconcile(b)
    assert "N3_TERRAIN_PATH" in v.rule_path
    row = verdict_row(v, b, claim_id=21, version=1)
    recomputed = reconcile(bundle_from_lineage(row["lineage"]))
    assert "N3_TERRAIN_PATH" in recomputed.rule_path
    assert compare_verdicts(v, recomputed).identical


# --- what the row contains ----------------------------------------------


def test_row_carries_everything_the_schema_needs() -> None:
    b, v = built(families=all_agreeing(1.0))
    row = verdict_row(v, b, claim_id=42, version=2)
    for key in (
        "claim_id",
        "version",
        "level",
        "rule_path",
        "score",
        "confidence",
        "coverage",
        "quality",
        "data_sufficiency",
        "dissent",
        "recommended_action",
        "engine_version",
        "weights",
        "lineage",
        "bundle_digest",
        "verdict_digest",
    ):
        assert key in row, key
    assert row["claim_id"] == 42
    assert row["version"] == 2
    assert row["status"] == "pending", "a fresh verdict is PROVISIONAL until adjudicated"


def test_quality_is_stored_so_the_formula_can_be_verified() -> None:
    """Migration 0002 added this: only data_sufficiency was stored before, so an
    auditor recomputing confidence by hand would find a term missing."""
    b, v = built(families=all_agreeing(1.0), metadata_integrity=0.95, data_sufficiency=0.88)
    row = verdict_row(v, b, claim_id=1, version=1)
    assert row["quality"] == pytest.approx(0.95 * 0.88, abs=1e-4)
    # confidence = |score| * coverage * quality must hold on the stored numbers.
    assert row["confidence"] == pytest.approx(
        abs(row["score"]) * row["coverage"] * row["quality"], abs=1e-3
    )


def test_recommended_action_keeps_its_priority() -> None:
    b = bundle(
        families=(
            fam("terrain", -1.0),
            fam("satellite", -1.0, cluster_scale=True),
            fam("temporal", -1.0, cluster_scale=True),
            fam("photo", 0.4),
        ),
        gate=gates(passed=False, footprint_m2=625.0, escalated=True),
        alternatives=(Alternative(description="x", excluded=True, basis="y"),),
    )
    row = verdict_row(reconcile(b), b, claim_id=1, version=1)
    assert row["recommended_action"]["action"] == "physical_verification"
    assert row["recommended_action"]["priority"] == 1


def test_lineage_keeps_prose_for_the_evidence_pack() -> None:
    """Excluded from the digest, retained for the report."""
    b, v = built(families=all_agreeing(1.0))
    lineage = verdict_row(v, b, claim_id=1, version=1)["lineage"]
    assert "family_reasons" in lineage
    assert set(lineage["family_reasons"]) == {f.family for f in b.families}
    assert lineage["dissent"] == list(v.dissent)


# --- evidence rows -------------------------------------------------------


def test_evidence_rows_carry_the_partition_key() -> None:
    """`evidence` is LIST-partitioned by district_lgd (migration 0001).

    Omitting it routes every row to the DEFAULT partition and quietly defeats
    the partitioning plan, so it is a required argument rather than optional.
    """
    b, _ = built(families=all_agreeing(1.0))
    rows = evidence_rows(b, claim_id=5, district_lgd="520")
    assert len(rows) == 6
    assert all(r["district_lgd"] == "520" for r in rows)
    assert {r["family"] for r in rows} == {
        "terrain",
        "satellite",
        "temporal",
        "control",
        "photo",
        "context",
    }


def test_evidence_rows_preserve_cluster_scale_in_the_payload() -> None:
    b = bundle(families=(fam("satellite", -1.0, cluster_scale=True),))
    row = evidence_rows(b, claim_id=1, district_lgd="520")[0]
    assert row["payload"]["cluster_scale"] is True
    assert row["payload"]["reason"]


def test_evidence_rows_respect_the_agreement_check_constraint() -> None:
    """Migration 0001 has CHECK (agreement BETWEEN -1 AND 1)."""
    b, _ = built(families=all_agreeing(1.0))
    for row in evidence_rows(b, claim_id=1, district_lgd="520"):
        assert -1.0 <= row["agreement"] <= 1.0


# --- refusals ------------------------------------------------------------


def test_a_lineage_without_a_bundle_is_refused() -> None:
    """Distinct from a digest mismatch: the record itself is unusable."""
    with pytest.raises(LineageIncomplete, match="missing lineage.bundle"):
        bundle_from_lineage({"producers": {}})


def test_a_truncated_bundle_payload_is_refused() -> None:
    with pytest.raises(LineageIncomplete, match="missing bundle.gates"):
        bundle_from_lineage(
            {"bundle": {"claim_id": "X", "intervention_type": "check_dam", "families": []}}
        )


def test_the_refusal_explains_why_a_pre_migration_row_cannot_recompute() -> None:
    with pytest.raises(LineageIncomplete, match="migration 0002"):
        bundle_from_lineage({})


def test_a_drifted_engine_config_is_refused_not_silently_accepted() -> None:
    """The false-reassurance case this guarantee exists to rule out.

    A verdict computed under different weights must not be recomputed under
    today's and reported 'identical'.
    """
    b, v = built(families=all_agreeing(1.0))
    row = verdict_row(v, b, claim_id=1, version=1, cfg=EngineConfig(agreeing_threshold=0.9))
    with pytest.raises(LineageIncomplete, match="does not match the current"):
        config_from_lineage(row["lineage"])


def test_an_absent_fingerprint_falls_back_to_the_current_config() -> None:
    """Tolerated for rows written before fingerprints were stored, and the
    absence is visible in the lineage rather than papered over."""
    b, v = built(families=all_agreeing(1.0))
    lineage = verdict_row(v, b, claim_id=1, version=1)["lineage"]
    del lineage["bundle"]["config_fingerprint"]
    assert config_from_lineage(lineage).fingerprint() == EngineConfig().fingerprint()


def test_reasons_missing_from_lineage_get_an_explicit_placeholder() -> None:
    """Never an empty string: FamilyEvidence forbids it, and a silent
    placeholder would make a recomputed Evidence Pack read as authoritative."""
    b, v = built(families=all_agreeing(1.0))
    lineage = verdict_row(v, b, claim_id=1, version=1)["lineage"]
    del lineage["family_reasons"]
    rebuilt = bundle_from_lineage(lineage)
    assert all(f.reason == "reason not retained in lineage" for f in rebuilt.families)
    # And the verdict is still identical, because prose is not decision-bearing.
    assert compare_verdicts(v, reconcile(rebuilt)).identical


def test_extra_lineage_is_merged_for_producer_provenance() -> None:
    """The analysis grid and scene ids ride along here."""
    b, v = built(families=all_agreeing(1.0))
    row = verdict_row(
        v,
        b,
        claim_id=1,
        version=1,
        extra_lineage={"analysis_grid": {"epsg": 32643, "width": 1065}},
    )
    assert row["lineage"]["analysis_grid"]["epsg"] == 32643
    # Extra lineage must not disturb the digest-bearing bundle payload.
    assert compare_verdicts(v, reconcile(bundle_from_lineage(row["lineage"]))).identical


# --- the producer -> task wire format ------------------------------------


def test_wire_payload_has_the_same_shape_as_lineage() -> None:
    """One structure crosses the broker and lands in the `lineage` column.

    If these diverge, the payload proven to reconstruct a byte-identical verdict
    is not the payload the task actually receives, and the recompute tests stop
    covering the path that runs in production.
    """
    from app.services.audit import wire_payload

    b, v = built(families=all_agreeing(1.0))
    wire = wire_payload(b)
    lineage = verdict_row(v, b, claim_id=1, version=1)["lineage"]

    assert set(wire) <= set(lineage), "wire keys must all exist in lineage"
    assert wire["bundle"] == lineage["bundle"]
    assert wire["family_reasons"] == lineage["family_reasons"]


def test_a_bundle_survives_the_wire_unchanged() -> None:
    from app.services.audit import wire_payload

    b, v = built(families=all_agreeing(1.0))
    rebuilt = bundle_from_lineage(wire_payload(b))
    assert compare_verdicts(v, reconcile(rebuilt)).identical


def test_wire_payload_carries_reason_prose() -> None:
    """`bundle_payload` excludes prose so it cannot affect a digest; the
    persisted evidence rows still need it, because that is what the UI's
    evidence tree shows. An earlier draft of the task dropped it."""
    from app.services.audit import wire_payload

    b = bundle(families=all_agreeing(1.0))
    reasons = wire_payload(b)["family_reasons"]
    assert set(reasons) == {f.family for f in b.families}
    assert all(reasons.values())
    assert "bundle" in wire_payload(b)
    assert "reason" not in json.dumps(wire_payload(b)["bundle"]), (
        "prose must stay out of the digest-bearing half"
    )


def test_wire_payload_records_the_config_fingerprint() -> None:
    """Otherwise a recompute cannot distinguish "written under today's config"
    from "config was never recorded"."""
    from app.services.audit import wire_payload

    b = bundle(families=all_agreeing(1.0))
    assert wire_payload(b)["bundle"]["config_fingerprint"]
    drifted = wire_payload(b, EngineConfig(agreeing_threshold=0.9))
    assert (
        drifted["bundle"]["config_fingerprint"] != wire_payload(b)["bundle"]["config_fingerprint"]
    )
    with pytest.raises(LineageIncomplete, match="does not match the current"):
        config_from_lineage(drifted)
