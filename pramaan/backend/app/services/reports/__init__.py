"""The Evidence Pack — design doc §21.3 / FR-9.5, as a document.

FR-9.5 requires a pack carrying the claim, all evidence, the verdict, the
dissent, the limitations and the full lineage, with a limitations section that is
not removable. Three modules, split by what could go wrong in each:

`pack`
    Reads the stored rows. Raises `PackUnavailable` when the record cannot
    produce a document, and never falls back to recomputation.

`limitations`
    Builds the mandatory block. Pure — it takes a pack and returns text — so the
    one section a reviewer will read hardest is testable without a database.

`html` / `pdf`
    Presentation. `html` emits one self-contained document with no network
    resource of any kind; `pdf` renders that document and raises
    `PdfUnavailable` when the renderer's native libraries are absent, rather
    than serving HTML under a PDF content type.

The governing rule for all four is stated in `pack`: a report reads, it never
re-derives. See that module's docstring for why.
"""

from __future__ import annotations

from app.services.reports.html import ABSENT, render_html
from app.services.reports.limitations import CEILING, Limitation, limitations_for
from app.services.reports.pack import (
    ClaimFacts,
    EvidencePack,
    FamilyRow,
    PackUnavailable,
    SceneRecord,
    Signature,
    load_pack,
)
from app.services.reports.pdf import PdfUnavailable, render_pdf

__all__ = [
    "ABSENT",
    "CEILING",
    "ClaimFacts",
    "EvidencePack",
    "FamilyRow",
    "Limitation",
    "PackUnavailable",
    "PdfUnavailable",
    "SceneRecord",
    "Signature",
    "limitations_for",
    "load_pack",
    "render_html",
    "render_pdf",
]
