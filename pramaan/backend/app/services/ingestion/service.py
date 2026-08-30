"""Turn one uploaded photograph into a claim, or refuse it with a reason.

This is the orchestration the four sibling modules exist to serve: `store`
sniffs and re-encodes the bytes, `exif` reads the headers, `quality` gates them,
`dedupe` fingerprints them. Nothing here re-implements any of that; this module
only decides the ORDER, and the order is the whole correctness of the thing.

## The order, and why it is this one

1. **Sniff and decode before anything else.** A payload that is not an image
   must never reach EXIF parsing, a hashing pass, or the object store.
2. **Gate on quality before storing.** A blurred photograph that enters the
   record pollutes it permanently: the ledger is append-only, and an officer who
   later signs a verdict built on an unreadable image cannot un-sign it. Cheap
   refusal now beats expensive doubt later.
3. **Resolve the coordinate before storing.** An image with no resolvable
   position is not evidence about a place, so there is nothing to file.
4. **Check for a duplicate before storing.** Re-uploading the same photograph
   under a second `unique_id` would create two structures from one observation
   and inflate every count downstream — the exact failure this system exists to
   detect in someone else's data.
5. **Store the object, then write the rows.** Never the reverse. A database row
   pointing at an object that does not exist is a broken record that reads as a
   real one; an orphaned object that nothing points at is merely wasted bytes.
6. **Commit the three inserts in one transaction.** A `field_images` row without
   its claim is an image nobody will ever look at, and a claim without its image
   is a claim with no evidence behind it.

## What this deliberately does NOT do

It does not reconcile. The created claim has **no verdict**, and the response
says so. Reconciliation reads satellite and terrain evidence, takes seconds to
minutes, and belongs to a worker; doing it inline would make an upload
button block on NASA's STAC endpoint. A field officer's upload succeeding and
a verdict existing are two separate facts, and conflating them in one response
would invite the UI to display a verdict that had not been computed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.ingestion.dedupe import (
    DUPLICATE_MAX_DISTANCE,
    hamming,
    phash,
    to_signed,
    to_unsigned,
)
from app.services.ingestion.exif import ImageMetadata, read_metadata
from app.services.ingestion.quality import QualityReport, assess
from app.services.ingestion.store import StoredImage, decode, put_image, sniff
from app.services.reconcile.signatures import SIGNATURES


class IngestRejected(ValueError):
    """The upload was refused for a stated, reportable reason.

    Carries the reason as prose because the field officer standing in a field is
    the person who has to act on it, and "422" is not an instruction.
    """


class DuplicateUpload(IngestRejected):
    """The perceptual hash matches an image already on file."""

    def __init__(self, existing_unique_id: str, distance: int) -> None:
        super().__init__(
            f"this photograph is a perceptual duplicate of {existing_unique_id} "
            f"(Hamming distance {distance}); re-filing it would create a second "
            f"structure from one observation"
        )
        self.existing_unique_id = existing_unique_id
        self.distance = distance


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What was created. Deliberately carries no verdict — see module docstring."""

    claim_id: int
    unique_id: str
    image_id: str
    district_lgd: str
    intervention_type: str
    asserted_date: date
    lat: float
    lon: float
    coord_provenance: str
    gps_accuracy_m: float | None
    quality: QualityReport
    stored: StoredImage
    duplicate_of: str | None


#: The uncertainty radius used when nothing better is known, in metres.
#:
#: §16.2 STEP 2 samples terrain over a disk of `max(gps_accuracy, 15 m)`, so 15
#: is the floor the engine already assumes and using it here keeps one number in
#: one place. A photograph whose header carried no accuracy is not thereby
#: precise, and defaulting to 0 would tell the terrain sampler to read a single
#: pixel — the exact mistake `DiskStat` exists to prevent.
DEFAULT_UNCERTAINTY_M = 15.0


def _resolve_position(
    meta: ImageMetadata,
    *,
    lat: float | None,
    lon: float | None,
    gps_accuracy_m: float | None,
) -> tuple[float, float, str, float | None]:
    """Decide the coordinate and record how it was obtained.

    Form values win over EXIF. An officer correcting a bad fix — or pinning a
    structure whose camera recorded no GPS at all — is legitimate and common.
    But the correction is recorded as `manual_pin`, never as `exif_gps`: an
    auditor's first question about a coordinate is whether the camera produced
    it or a person typed it, and a schema that cannot answer that has lost the
    only thing making the geotag worth more than a spreadsheet cell. The EXIF
    pair, if any, survives in `raw_exif` regardless, so the substitution is
    always visible.
    """
    if lat is not None and lon is not None:
        # An accuracy the officer did not state is unknown, not the camera's:
        # carrying the EXIF accuracy across to a hand-pinned coordinate would
        # attach a precision claim to a position it does not describe.
        return lat, lon, "manual_pin", gps_accuracy_m
    if meta.has_gps:
        assert meta.lat is not None and meta.lon is not None  # narrowed by has_gps
        return meta.lat, meta.lon, "exif_gps", gps_accuracy_m or meta.gps_accuracy_m
    raise IngestRejected(
        "no coordinate could be resolved: the photograph carries no EXIF GPS "
        "and no latitude/longitude was supplied. A photograph without a "
        "position is not evidence about a place."
    )


_NEAR_DUPLICATES = text("""
SELECT i.unique_id, f.phash
FROM field_images f
JOIN interventions i ON i.id = f.intervention_id
WHERE f.district_lgd = :district_lgd AND f.phash IS NOT NULL
""")


def _find_duplicate(
    session: Session, *, district_lgd: str, fingerprint: int
) -> tuple[str, int] | None:
    """Nearest stored fingerprint within the duplicate threshold, if any.

    Scoped to the district, which is where a re-upload realistically happens and
    which keeps the scan bounded. Compared in Python rather than SQL because
    Postgres has no Hamming operator for `bigint` without an extension, and
    adding one for a district-sized scan would be a dependency bought for
    nothing.
    """
    for row in session.execute(_NEAR_DUPLICATES, {"district_lgd": district_lgd}).mappings():
        distance = hamming(fingerprint, to_unsigned(int(row["phash"])))
        if distance <= DUPLICATE_MAX_DISTANCE:
            return str(row["unique_id"]), distance
    return None


_PROJECT_FOR_DISTRICT = text("""
SELECT p.id AS project_id, p.mws_id
FROM projects p
WHERE p.district_lgd = :district_lgd
ORDER BY p.id
LIMIT 1
""")

_NEXT_SEQUENCE = text("""
SELECT count(*) + 1 AS n FROM interventions WHERE district_lgd = :district_lgd
""")

_INSERT_IMAGE = text("""
INSERT INTO field_images
    (id, intervention_id, district_lgd, object_key, derivative_key, phash,
     captured_at, captured_at_source, geom, gps_accuracy_m, orientation_deg,
     altitude_m, coord_provenance, device_make, device_model, width_px,
     height_px, blur_score, metadata_integrity, quality_flags, raw_exif,
     uploaded_by)
VALUES
    (CAST(:id AS uuid), :intervention_id, :district_lgd, :object_key,
     :derivative_key, :phash, :captured_at, :captured_at_source,
     ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :gps_accuracy_m,
     :orientation_deg, :altitude_m, CAST(:coord_provenance AS coord_provenance),
     :device_make, :device_model, :width_px, :height_px, :blur_score,
     :metadata_integrity, :quality_flags, CAST(:raw_exif AS jsonb),
     CAST(:uploaded_by AS uuid))
""")

_INSERT_INTERVENTION = text("""
INSERT INTO interventions
    (unique_id, project_id, mws_id, district_lgd, type, status, completed_date,
     geom, expected_footprint_m2)
VALUES
    (:unique_id, :project_id, :mws_id, :district_lgd,
     CAST(:type AS intervention_type), 'completed', :asserted_date,
     ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :expected_footprint_m2)
RETURNING id
""")

_INSERT_CLAIM = text("""
INSERT INTO claims
    (intervention_id, district_lgd, asserted_status, asserted_date, geom,
     uncertainty_m, detectability)
VALUES
    (:intervention_id, :district_lgd, 'completed', :asserted_date,
     ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), :uncertainty_m, :detectability)
RETURNING id
""")


def _metadata_integrity(meta: ImageMetadata, provenance: str) -> float:
    """A [0,1] trust multiplier for the metadata, not an evidence family.

    §14.4 and ADR-001 are explicit that metadata is a *quality* multiplier and
    never a sixth agreeing voice — counting it twice was defect D4. The factors
    are deliberately few and each one is a real, checkable absence rather than a
    fitted weight, because there is no data to fit against and pretending
    otherwise would be the dishonesty this product refuses.
    """
    score = 1.0
    if provenance != "exif_gps":
        # A hand-pinned coordinate is usable and is not camera-witnessed.
        score -= 0.30
    if meta.captured_at is None:
        score -= 0.20
    if meta.gps_accuracy_m is None:
        score -= 0.10
    if meta.device_make is None and meta.device_model is None:
        score -= 0.10
    return round(max(0.0, score), 3)


def ingest(
    session: Session,
    *,
    data: bytes,
    intervention_type: str,
    asserted_date: date,
    district_lgd: str,
    uploaded_by: str,
    lat: float | None = None,
    lon: float | None = None,
    gps_accuracy_m: float | None = None,
) -> IngestResult:
    """Create one claim from one photograph. Raises `IngestRejected` to refuse.

    `district_lgd` is decided by the caller against the principal's jurisdiction
    and is trusted here; this function does not re-authorise, it only refuses on
    evidence grounds.
    """
    if intervention_type not in SIGNATURES:
        raise IngestRejected(
            f"unknown intervention type {intervention_type!r}; "
            f"the expected-signature table defines {len(SIGNATURES)} types"
        )

    # 1. Sniff, then decode. `sniff` reads magic bytes and ignores the declared
    #    content type, which a client controls and which is therefore evidence
    #    of nothing.
    sniff(data)
    image = decode(data)

    # 2. Quality gate, before any storage or database work.
    quality = assess(image)
    if not quality.passed:
        raise IngestRejected(
            f"image quality gate failed ({quality.first_flag}); "
            f"blur score {quality.blur_score:.1f}. A photograph that cannot be "
            f"read cannot support a claim, and the record is append-only."
        )

    # 3. Position, with provenance.
    meta = read_metadata(image)
    resolved_lat, resolved_lon, provenance, accuracy = _resolve_position(
        meta, lat=lat, lon=lon, gps_accuracy_m=gps_accuracy_m
    )

    # 4. Duplicate check.
    fingerprint = phash(image)
    duplicate = _find_duplicate(session, district_lgd=district_lgd, fingerprint=fingerprint)
    if duplicate is not None:
        raise DuplicateUpload(*duplicate)

    parent = (
        session.execute(_PROJECT_FOR_DISTRICT, {"district_lgd": district_lgd}).mappings().first()
    )
    if parent is None:
        # Refused rather than invented. Fabricating a watershed hierarchy would
        # attach this structure to a micro-watershed that does not exist, and
        # every terrain and control computation downstream is keyed on that
        # polygon — a wrong parent is worse than a missing one because it
        # produces confident output.
        raise IngestRejected(
            f"district {district_lgd} has no onboarded project or micro-watershed. "
            f"Run district onboarding before filing claims against it."
        )

    signature = SIGNATURES[intervention_type]
    # Midpoint of the published range, matching how the detectability gate reads
    # the same table: a single number here would be a false precision, and the
    # gate already treats the range as a range.
    footprint = (signature.footprint_min_m2 + signature.footprint_max_m2) / 2.0
    uncertainty = accuracy if accuracy is not None else DEFAULT_UNCERTAINTY_M

    # 5. Store the bytes. Raises before any row is written.
    image_id = str(uuid.uuid4())
    stored = put_image(image, data, district_lgd=district_lgd, image_id=image_id)

    # 6. Three inserts, one transaction.
    sequence = int(session.execute(_NEXT_SEQUENCE, {"district_lgd": district_lgd}).scalar_one())
    unique_id = f"MH-{district_lgd}-{sequence:05d}"

    geom = {"lat": resolved_lat, "lon": resolved_lon}
    intervention_id = int(
        session.execute(
            _INSERT_INTERVENTION,
            {
                "unique_id": unique_id,
                "project_id": int(parent["project_id"]),
                "mws_id": int(parent["mws_id"]),
                "district_lgd": district_lgd,
                "type": intervention_type,
                "asserted_date": asserted_date,
                "expected_footprint_m2": footprint,
                **geom,
            },
        ).scalar_one()
    )

    session.execute(
        _INSERT_IMAGE,
        {
            "id": image_id,
            "intervention_id": intervention_id,
            "district_lgd": district_lgd,
            "object_key": stored.object_key,
            "derivative_key": stored.derivative_key,
            # Signed, because Postgres `bigint` is signed and a 64-bit
            # perceptual hash overflows it. `dedupe.to_unsigned` restores it.
            "phash": to_signed(fingerprint),
            "captured_at": meta.captured_at,
            "captured_at_source": "exif" if meta.captured_at is not None else None,
            "gps_accuracy_m": accuracy,
            "orientation_deg": meta.orientation_deg,
            "altitude_m": meta.altitude_m,
            "coord_provenance": provenance,
            "device_make": meta.device_make,
            "device_model": meta.device_model,
            "width_px": meta.width_px,
            "height_px": meta.height_px,
            "blur_score": quality.blur_score,
            "metadata_integrity": _metadata_integrity(meta, provenance),
            "quality_flags": list(quality.flags),
            "raw_exif": _raw_json(meta.raw),
            "uploaded_by": uploaded_by,
            **geom,
        },
    )

    # `detectability` is recorded as pending, not guessed: the gate compares the
    # expected footprint against the sensor's pixel area and is the satellite
    # worker's job. Writing 'passed' here would pre-empt a computation that has
    # not run.
    claim_id = int(
        session.execute(
            _INSERT_CLAIM,
            {
                "intervention_id": intervention_id,
                "district_lgd": district_lgd,
                "asserted_date": asserted_date,
                "uncertainty_m": uncertainty,
                "detectability": "pending",
                **geom,
            },
        ).scalar_one()
    )
    session.commit()

    return IngestResult(
        claim_id=claim_id,
        unique_id=unique_id,
        image_id=image_id,
        district_lgd=district_lgd,
        intervention_type=intervention_type,
        asserted_date=asserted_date,
        lat=resolved_lat,
        lon=resolved_lon,
        coord_provenance=provenance,
        gps_accuracy_m=accuracy,
        quality=quality,
        stored=stored,
        duplicate_of=None,
    )


def _raw_json(raw: dict[str, Any]) -> str:
    """`raw_exif` as a JSON string for the `jsonb` cast.

    `exif._jsonable` has already made the values JSON-safe; this is only the
    serialisation step, kept separate so a failure here is obviously a
    serialisation failure and not a parsing one.
    """
    import json

    return json.dumps(raw, ensure_ascii=False, default=str)
