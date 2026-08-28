"""Verdict hashing — the mechanism behind the reproducibility guarantee.

docs §21.3 promises: *"A verdict can be recomputed byte-identically from its
lineage record. This is the single property that makes the output usable as
government evidence."*

A promise is not a property. This module makes it checkable:

* `bundle_digest` hashes an `EvidenceBundle` canonically, so two bundles that
  should produce the same verdict hash the same.
* `verdict_digest` hashes the verdict's decision-bearing fields.
* `POST /verdicts/{id}/recompute` re-runs the engine from the stored lineage and
  compares digests. Identical means the verdict is reproducible; different means
  something changed and the officer is told which.

## What is and is not in the digest

Included: everything the engine reads. Excluded: anything that varies between
two correct runs — computation timestamps, database ids, row versions.

That exclusion is the point. A digest over "everything" would differ on every
run and the guarantee would be vacuous while appearing rigorous.

## Why this lives in `audit`, not in `reconcile`

Hashing is deterministic and IO-free, so it would not break the engine's purity
guarantee in substance. But it needs `hashlib` and `json`, and the engine's
declared import surface is `dataclasses`/`enum`/`typing` and itself — asserted by
`tests/unit/test_engine_purity.py`.

Widening that surface to admit two harmless modules would make the assertion
weaker for every future edit, and the test would stop being a meaningful guard.
Hashing is an audit concern anyway: the engine decides, the audit layer attests.
So the module moved rather than the rule bending. The purity test caught this
during development, which is the entire point of having it.

## Why canonical JSON rather than pickle or repr

`repr` of a float is implementation-defined across versions, dict ordering is
insertion-ordered in CPython but not guaranteed by the data model, and pickle
embeds class paths so a module rename would break every stored digest. Canonical
JSON with sorted keys and a fixed float format is boring, portable, and
inspectable by an auditor with `jq`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.reconcile.types import EvidenceBundle, Verdict
from app.services.reconcile.weights import ENGINE_VERSION, EngineConfig

#: Floats are formatted to this many decimal places before hashing. 10 is far
#: beyond any meaningful precision in an index value and well inside float64's
#: exact range, so it removes last-bit noise without discarding signal.
FLOAT_PRECISION = 10

DIGEST_VERSION = "digest-v1"


def _canonical(value: Any) -> Any:
    """Recursively normalise a value for stable hashing."""
    if isinstance(value, bool):
        # Before the int branch: bool is a subclass of int in Python, and
        # True would otherwise canonicalise to "1.0000000000".
        return value
    if isinstance(value, float):
        if value != value:  # NaN
            return "NaN"
        if value in (float("inf"), float("-inf")):
            return "Infinity" if value > 0 else "-Infinity"
        return f"{value:.{FLOAT_PRECISION}f}"
    if isinstance(value, int):
        return value
    if isinstance(value, str) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return str(value)


def canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, fixed float format, no whitespace."""
    return json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bundle_payload(bundle: EvidenceBundle, cfg: EngineConfig) -> dict[str, Any]:
    """The complete engine input, as a hashable structure.

    Families are sorted by name: the engine's arithmetic is order-independent
    (a weighted sum), so two bundles differing only in family order must hash
    identically or the digest would report a spurious difference.
    """
    return {
        "digest_version": DIGEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "config_fingerprint": cfg.fingerprint(),
        "claim_id": bundle.claim_id,
        "intervention_type": bundle.intervention_type,
        "families": [
            {
                "family": f.family,
                "agreement": f.agreement,
                "available": f.available,
                "cluster_scale": f.cluster_scale,
                # `reason` is excluded on purpose: it is prose for humans and
                # rewording it must not invalidate a stored verdict. The
                # decision-bearing fields are agreement and availability.
            }
            for f in sorted(bundle.families, key=lambda f: f.family)
        ],
        "gates": {
            "detectability_passed": bundle.gates.detectability_passed,
            "expected_footprint_m2": bundle.gates.expected_footprint_m2,
            "pixel_area_m2": bundle.gates.pixel_area_m2,
            "escalated_to_cluster": bundle.gates.escalated_to_cluster,
            "scene_scale": bundle.gates.scene_scale,
            "terrain_plausibility": bundle.gates.terrain_plausibility,
        },
        "quality": {
            "metadata_integrity": bundle.quality.metadata_integrity,
            "data_sufficiency": bundle.quality.data_sufficiency,
        },
        "alternatives": [
            {"description": a.description, "excluded": a.excluded}
            for a in sorted(bundle.alternatives, key=lambda a: a.description)
        ],
        "limitations": sorted(bundle.limitations),
    }


def bundle_digest(bundle: EvidenceBundle, cfg: EngineConfig | None = None) -> str:
    """SHA-256 over the canonical engine input."""
    return _sha256(canonical_json(bundle_payload(bundle, cfg or EngineConfig())))


def verdict_payload(verdict: Verdict) -> dict[str, Any]:
    """The verdict's decision-bearing fields.

    `dissent` and `reason` prose are excluded for the same reason family reasons
    are: they are the explanation, not the decision. `rule_path` IS included,
    because a verdict reached by a different rule is a different verdict even at
    the same label and score.
    """
    return {
        "digest_version": DIGEST_VERSION,
        "engine_version": verdict.engine_version,
        "claim_id": verdict.claim_id,
        "label": verdict.label,
        "level": verdict.level.value,
        "score": verdict.score,
        "confidence": verdict.confidence,
        "coverage": verdict.coverage,
        "quality": verdict.quality,
        "data_sufficiency": verdict.data_sufficiency,
        "rule_path": list(verdict.rule_path),
        "recommended_action": verdict.recommended_action,
        "priority": verdict.priority,
        "weights": verdict.weights,
    }


def verdict_digest(verdict: Verdict) -> str:
    return _sha256(canonical_json(verdict_payload(verdict)))


class RecomputeResult:
    """Outcome of re-running the engine over a stored bundle."""

    __slots__ = ("differences", "identical", "recomputed_digest", "stored_digest")

    def __init__(self, stored_digest: str, recomputed_digest: str, differences: list[str]) -> None:
        self.stored_digest = stored_digest
        self.recomputed_digest = recomputed_digest
        self.identical = stored_digest == recomputed_digest
        self.differences = differences

    def as_dict(self) -> dict[str, Any]:
        return {
            "identical": self.identical,
            "stored_digest": self.stored_digest,
            "recomputed_digest": self.recomputed_digest,
            "differences": self.differences,
        }


def compare_verdicts(stored: Verdict, recomputed: Verdict) -> RecomputeResult:
    """Diff two verdicts field by field, not just by digest.

    A digest mismatch alone tells an officer nothing actionable. Naming the
    changed fields is what makes a failed recompute a diagnosis instead of an
    alarm.
    """
    a, b = verdict_payload(stored), verdict_payload(recomputed)
    differences = [
        f"{key}: stored={a[key]!r} recomputed={b[key]!r}"
        for key in sorted(set(a) | set(b))
        if a.get(key) != b.get(key)
    ]
    return RecomputeResult(
        stored_digest=verdict_digest(stored),
        recomputed_digest=verdict_digest(recomputed),
        differences=differences,
    )
