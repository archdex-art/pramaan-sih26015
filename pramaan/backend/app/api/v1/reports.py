"""Evidence Pack export — the most forwardable artefact in the system.

Two routes, one document. `GET /claims/{id}/report` returns the HTML,
`GET /claims/{id}/report.pdf` the same document rendered to PDF.

## Why jurisdiction scoping matters more here than anywhere else

Every other read surface returns JSON to a screen. This one returns a file, and a
file gets attached to an email. An unscoped report route is not a listing bug, it
is a cross-district data leak in a format the recipient can forward without ever
having authenticated — so `require_claim_visible` is the first statement in both
handlers, before the pack is loaded and before anything is rendered. It answers
404 for "not yours" and for "does not exist" with the same body, for the reason
given in its own module docstring: a 403/404 boundary maps the national register
by iteration.

## The three failure answers, and why they are different

* **404** — no such claim, for you. From `require_claim_visible`.
* **409** — the claim is yours and the stored record cannot produce a document:
  no verdict yet, or a verdict whose lineage is empty or truncated. The detail
  names which. Nothing is recomputed to fill the gap, because a document that
  recomputes can disagree with the record it claims to document.
* **501** — the PDF renderer's native libraries are absent on this host. The
  HTML route is unaffected and prints identically. Serving the HTML bytes under
  `application/pdf` was rejected: a file that is not what its type says it is
  ends up in an audit folder.

## Capability, not role

Both routes are gated on `verdict:read`, which `wcdc`, `slna`, `readonly` and
`dolr_admin` hold and no field role does. `evidence:read` was considered and
rejected: the pack's headline content is a verdict, and gating the exported
verdict less strictly than `GET /claims/{id}/verdict` would make the JSON route's
gate decorative.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse

from app.api.deps import CurrentPrincipal, CurrentScope, DbSession, require
from app.api.scope import require_claim_visible
from app.core.authz import Capability
from app.services.reports import (
    EvidencePack,
    PackUnavailable,
    PdfUnavailable,
    load_pack,
    render_html,
    render_pdf,
)

router = APIRouter(tags=["reports"])

#: Everything that is not safe in a `filename=` token. `interventions.unique_id`
#: is a TEXT column, and a quote, a semicolon or a CR in it would let a stored
#: value rewrite the Content-Disposition header — so the value is filtered to a
#: known-safe alphabet rather than quoted and hoped for. Substituting rather
#: than stripping keeps the filename the same length as the id, so two ids that
#: differ only in punctuation cannot collide into one filename.
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


def _filename(pack: EvidencePack, suffix: str) -> str:
    return f"PRAMAAN-EvidencePack-{_UNSAFE_FILENAME.sub('-', pack.claim.unique_id)}.{suffix}"


def _load(
    session: DbSession, scope: CurrentScope, principal: CurrentPrincipal, claim_id: int
) -> EvidencePack:
    """Scope check, then read. Shared so the two routes cannot drift apart.

    One timestamp is taken here and stamped into the pack, so the HTML and the
    PDF of the same export carry the same generation time rather than two clock
    reads a few milliseconds apart.
    """
    require_claim_visible(session, scope, claim_id)
    try:
        return load_pack(
            session,
            claim_id,
            generated_for=principal.username,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    except PackUnavailable as exc:
        # 409: the request is correct and the caller is entitled to the claim.
        # The stored record cannot answer it, and the message says which part is
        # missing — "cannot export" without a reason is indistinguishable from a
        # bug in the exporter.
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get(
    "/claims/{claim_id}/report",
    response_class=HTMLResponse,
    dependencies=[Depends(require(Capability.VERDICT_READ))],
    summary="Evidence Pack (HTML)",
)
def get_report_html(
    claim_id: int,
    session: DbSession,
    scope: CurrentScope,
    principal: CurrentPrincipal,
) -> HTMLResponse:
    """The Evidence Pack as one self-contained HTML document.

    `inline`, not `attachment`: this is the representation an officer reads on
    screen and a judge is shown during a demonstration, and it carries a filename
    so saving it still produces a named file. The PDF is the one you attach.
    """
    pack = _load(session, scope, principal, claim_id)
    return HTMLResponse(
        content=render_html(pack),
        headers={
            "Content-Disposition": f'inline; filename="{_filename(pack, "html")}"',
            # The pack reflects the ledger and the verdict status, both of which
            # change under the reader. A cached export shown after an
            # adjudication would read PROVISIONAL over a signed record.
            "Cache-Control": "no-store",
        },
    )


@router.get(
    "/claims/{claim_id}/report.pdf",
    dependencies=[Depends(require(Capability.VERDICT_READ))],
    summary="Evidence Pack (PDF)",
    response_class=Response,
)
def get_report_pdf(
    claim_id: int,
    session: DbSession,
    scope: CurrentScope,
    principal: CurrentPrincipal,
) -> Response:
    """The same document as PDF, or 501 naming the missing renderer library.

    `attachment`: a PDF export exists to be filed against a monthly report, so
    the useful default is a download with the structure's unique id in the
    filename.
    """
    pack = _load(session, scope, principal, claim_id)
    try:
        pdf = render_pdf(render_html(pack))
    except PdfUnavailable as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(pack, "pdf")}"',
            "Cache-Control": "no-store",
        },
    )
