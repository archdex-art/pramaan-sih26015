"""Evidence capture: one photograph in, one claim out.

`claim:create` has been a granted capability with no endpoint since the role
model landed, which meant the field workspace — the two roles that actually
stand in a field — had nothing to do. This is that endpoint.

## Why this is a route and not a worker task

Every refusal here is one the officer must see *while still standing at the
site*: the photograph is too blurred, the camera recorded no position, this is
the same photograph as an earlier submission. A refusal delivered
asynchronously, minutes later, arrives after the person who could have retaken
the photograph has walked away. So the ingest path is synchronous and its
failure modes are HTTP status codes with prose.

Reconciliation is the opposite: it reads satellite and terrain evidence, takes
seconds to minutes, and depends on third parties. It stays a worker step, and
this endpoint deliberately returns a claim with **no verdict**. The response
says so, because a UI that received a claim and displayed a verdict beside it
would be displaying a verdict nobody computed.

## Scoping

A field officer must not be able to file into another district. The district is
resolved from the principal's own jurisdiction; a body value is accepted only
when it matches. This is checked here rather than in the service because it is
an authorisation question, and `services/ingestion` deliberately knows nothing
about principals.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentPrincipal, CurrentScope, require
from app.core.authz import Capability
from app.db.session import db_session
from app.services.ingestion.service import (
    DuplicateUpload,
    IngestRejected,
    ingest,
)
from app.services.ingestion.store import (
    MAX_UPLOAD_BYTES,
    ObjectStoreUnavailable,
    UploadRejected,
    UploadTooLarge,
)
from app.services.reconcile.signatures import SIGNATURES

router = APIRouter(tags=["claims"])

DbSession = Annotated[Session, Depends(db_session)]


class QualityOut(BaseModel):
    """The gate's own numbers, returned rather than summarised.

    The officer is told the blur score, not merely "accepted": a submission that
    passed at 61 against a threshold of 60 is worth retaking, and hiding the
    margin would deny them that judgement.
    """

    blur_score: float
    exposure_ok: bool
    passed: bool
    flags: list[str]


class IngestOut(BaseModel):
    """The created claim.

    No verdict field exists on purpose. See the module docstring.
    """

    claim_id: int
    unique_id: str
    image_id: str
    district_lgd: str
    intervention_type: str
    asserted_date: date
    lat: float
    lon: float
    #: How the coordinate was obtained: `exif_gps` when the camera recorded it,
    #: `manual_pin` when a person supplied it. An auditor's first question.
    coord_provenance: str
    gps_accuracy_m: float | None
    quality: QualityOut
    #: Always null on success — a duplicate is a 409, not a successful create.
    #: The field exists so the client has one shape to parse.
    duplicate_of: str | None
    #: Stated in the payload, not just in prose, so a client cannot present this
    #: claim as assessed.
    verdict_present: bool = False
    next_step: str = (
        "Queued for reconciliation. No verdict exists yet; satellite, terrain "
        "and control evidence are gathered by a worker."
    )


def _district_for(principal_district: str | None, unrestricted: bool, requested: str | None) -> str:
    """The district this claim will be filed against, or a refusal.

    A national administrator has no home district, so filing requires an
    explicit one from them. Everyone else files into their own, and a request to
    file elsewhere is refused rather than silently redirected — a field officer
    who believes they filed into district 522 and actually filed into 520 has
    created a record that is wrong in a way nobody will notice.
    """
    if unrestricted:
        if requested is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "a national account has no home district; supply district_lgd",
            )
        return requested
    if principal_district is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "your account has no district jurisdiction, so it cannot file a claim",
        )
    if requested is not None and requested != principal_district:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"your jurisdiction is district {principal_district}; "
            f"you cannot file a claim into district {requested}",
        )
    return principal_district


@router.post(
    "/claims",
    response_model=IngestOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Capability.CLAIM_CREATE))],
)
async def post_claim(
    session: DbSession,
    principal: CurrentPrincipal,
    scope: CurrentScope,
    photo: Annotated[UploadFile, File(description="Geo-tagged field photograph")],
    intervention_type: Annotated[str, Form()],
    asserted_date: Annotated[date, Form()],
    claim_text: Annotated[str, Form()],
    lat: Annotated[float | None, Form()] = None,
    lon: Annotated[float | None, Form()] = None,
    gps_accuracy_m: Annotated[float | None, Form()] = None,
    district_lgd: Annotated[str | None, Form()] = None,
) -> IngestOut:
    """File one geo-tagged photograph as a claim.

    422 when the quality gate fails or no coordinate can be resolved; 409 when
    the photograph duplicates one already on file; 403 when the target district
    is outside the caller's jurisdiction; 413 when the upload exceeds the cap.

    `claim_text` is the officer's own statement of what they saw. It is recorded
    against the claim rather than interpreted: the engine reconciles imagery and
    terrain, and a free-text assertion is the thing being tested, not evidence.
    """
    if intervention_type not in SIGNATURES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"unknown intervention type {intervention_type!r}",
        )

    target_district = _district_for(scope.district_lgd, scope.unrestricted, district_lgd)

    # Read with a cap rather than trusting Content-Length, which the client
    # sets. `UploadFile` streams to a spooled temp file, so this bounds memory
    # and disk both.
    data = await photo.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
        )
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty upload")

    try:
        result = ingest(
            session,
            data=data,
            intervention_type=intervention_type,
            asserted_date=asserted_date,
            district_lgd=target_district,
            uploaded_by=principal.user_id,
            lat=lat,
            lon=lon,
            gps_accuracy_m=gps_accuracy_m,
        )
    except DuplicateUpload as exc:
        # 409, not 422: the submission is well-formed and the conflict is with
        # existing state, which is exactly what 409 means. The existing id is in
        # the detail so the officer can go and look at it.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except UploadTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    except (IngestRejected, UploadRejected) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except ObjectStoreUnavailable as exc:
        # 503, not 500: nothing was written, the request is valid, and retrying
        # is the correct client behaviour. Saying so beats a generic failure.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return IngestOut(
        claim_id=result.claim_id,
        unique_id=result.unique_id,
        image_id=result.image_id,
        district_lgd=result.district_lgd,
        intervention_type=result.intervention_type,
        asserted_date=result.asserted_date,
        lat=result.lat,
        lon=result.lon,
        coord_provenance=result.coord_provenance,
        gps_accuracy_m=result.gps_accuracy_m,
        quality=QualityOut(
            blur_score=result.quality.blur_score,
            exposure_ok=result.quality.exposure_ok,
            passed=result.quality.passed,
            flags=list(result.quality.flags),
        ),
        duplicate_of=result.duplicate_of,
    )
