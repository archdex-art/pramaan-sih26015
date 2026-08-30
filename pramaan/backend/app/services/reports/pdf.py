"""HTML to PDF, when the renderer's native libraries are actually present.

WeasyPrint is a declared dependency of this backend, so the Python package is
installed. It is a CFFI binding over GLib, Pango and Cairo, and those are
*system* libraries that pip cannot install: on a host without them the import
fails with `OSError: cannot load library 'libgobject-2.0-0'` rather than
`ImportError`, and it fails at module import time, not at first use.

## Why the import is inside the function

A module-level import would make the whole API fail to start on a host missing
the native libraries — one absent PDF renderer taking down the claims register,
the verification queue and the ledger with it. The import is therefore deferred
to the one request that needs it, and its failure is confined to that request.

`OSError` is caught alongside `ImportError` for the reason above; the catch is
`Exception` because a CFFI loader can also raise its own error types, and the
alternative is a 500 that says nothing useful about a purely environmental
problem.

## Why the failure is 501 and not a silently HTML-typed response

The tempting shortcut is to serve the HTML bytes with `application/pdf`. It
would make the route "work", and it would hand an officer a file their PDF
reader refuses to open, or worse, one that opens and is quietly not the
archivable artefact they believe they attached to a report. A 501 naming the
missing library is a smaller failure and an actionable one — the HTML route
remains fully functional and prints identically.
"""

from __future__ import annotations


class PdfUnavailable(RuntimeError):
    """The PDF renderer could not run, and `str` says exactly why.

    Environmental, not a request fault: the same request would succeed on a host
    with the native libraries installed. The API reports it as 501.
    """


#: Printed to whoever gets the 501. Names the packages by their system names so
#: the message is directly actionable rather than a restatement of the error.
_INSTALL_HINT = (
    "WeasyPrint is declared as a dependency but its native libraries are not "
    "loadable on this host. It binds GLib, Pango and Cairo through CFFI, which "
    "pip cannot install: they need the system package manager "
    "(macOS: `brew install pango`; Debian/Ubuntu: "
    "`apt-get install libpango-1.0-0 libpangoft2-1.0-0`). The HTML Evidence Pack "
    "at the same path without the `.pdf` suffix is complete and prints "
    "identically."
)


def render_pdf(document: str) -> bytes:
    """Render a self-contained HTML document to PDF bytes.

    No `base_url` is passed, deliberately. WeasyPrint resolves relative URLs
    against it, and the Evidence Pack has none — no external stylesheet, no
    webfont, no image. Withholding the base URL means a future edit that
    introduced a remote reference would fail to resolve rather than silently
    making the report fetch something over the network, which design doc §38
    forbids.
    """
    try:
        from weasyprint import HTML  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001 - environmental; see module docstring
        raise PdfUnavailable(f"{_INSTALL_HINT} Underlying error: {exc}") from exc

    rendered: object = HTML(string=document).write_pdf()
    if not isinstance(rendered, bytes):  # pragma: no cover - only when given a target
        raise PdfUnavailable(
            "the PDF renderer returned no bytes. Nothing is emitted rather than an "
            "empty file, which would be indistinguishable from a corrupt export."
        )
    return rendered
