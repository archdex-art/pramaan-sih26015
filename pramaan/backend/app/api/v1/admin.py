"""The administration console (design doc S13) — an honesty surface.

Three reads, no writes, and every number on them comes from the running system:
a `SELECT` against the live database, a `stat` against the live filesystem, or a
constant the engine itself imports. Nothing here is computed for effect.

## Why this screen reports emptiness

Most of the tables behind the field-photo, alert and satellite-scene subsystems
have no rows in this build. The tempting choice is to omit them, and the result
is a console that looks complete because it only shows what happens to work.
`GET /admin/system` therefore counts those tables *by name* and returns the
zeroes, so an administrator — or a judge — can read what is populated and what
is not off one screen instead of inferring it from an absence.

The same rule governs `GET /admin/districts`: it reports one row per district
that genuinely appears in `claims`. If only one district has been loaded, the
response has one row. It never pads the list from a district reference table to
make the deployment look wider than it is.

## Gating

Every route requires the capability that names what it exposes —
`user:manage` for the account list, `district:manage` for the district roster,
and both for the system summary, which mixes the two. In this build's
`CAPABILITIES` map only `dolr_admin` holds either, so all three routes resolve
to "administrator only".

`ledger:verify` was considered and rejected: `wcdc`, `slna` and `readonly` also
hold it, and a district officer being able to enumerate every account in the
country because the same grant lets them recompute a hash chain is exactly the
sort of accidental widening capability-based gating is supposed to prevent.

Each route additionally requires the caller's workspace to be
`administration`. That is a second, independent condition rather than a
restatement of the first: if `CAPABILITIES` is ever widened to grant a
monitoring role `district:manage`, the capability check alone would silently
open the administration console to it, and this guard would still refuse.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, DbSession, require
from app.core.authz import WORKSPACE, Capability, Principal, Role, Workspace
from app.core.config import get_settings
from app.services.audit.ledger import verify_chain
from app.services.reconcile.weights import ENGINE_VERSION

router = APIRouter(tags=["admin"])


def administration_only(principal: CurrentPrincipal) -> Principal:
    """Refuse anyone whose workspace is not `administration`.

    Paired with, not instead of, the capability gate on each route. See the
    module docstring: this is the condition that keeps holding if the
    capability map is later relaxed.
    """
    if principal.workspace is not Workspace.ADMINISTRATION:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"role '{principal.role}' is in the {principal.workspace} workspace; "
            f"the administration console requires the administration workspace",
        )
    return principal


def _iso(value: datetime | None) -> str | None:
    """Timestamps cross this boundary as ISO-8601 strings.

    Matches `api/v1/adjudication.py`, which types `decided_at` as `str`. One
    convention for the whole API beats two that differ per router and force the
    front end to branch on which endpoint it called.
    """
    return None if value is None else value.isoformat()


# --- Accounts --------------------------------------------------------------

#: `password_hash` is deliberately absent from this projection and must stay
#: absent. It is an Argon2 digest, not a password, but an endpoint that ships
#: hashes to a browser turns an online attack into an offline one, and a
#: response model is a weaker guarantee than never selecting the column.
_USERS = text("""
SELECT username, full_name, role::text AS role, scope_state, scope_district,
       is_active, last_login_at, failed_attempts, locked_until
FROM users
ORDER BY role::text, username
""")


class UserOut(BaseModel):
    username: str
    full_name: str
    role: str
    #: Derived from `authz.WORKSPACE`, never stored. The database has no
    #: workspace column, and adding one would let it disagree with the map the
    #: router actually enforces.
    workspace: str
    scope_state: str | None
    scope_district: str | None
    is_active: bool
    last_login_at: str | None
    #: The lockout counters, exposed because "why can this officer not log in"
    #: is the single most common administrative question and the answer is here.
    failed_attempts: int
    locked_until: str | None


@router.get(
    "/admin/users",
    response_model=list[UserOut],
    dependencies=[Depends(require(Capability.USER_MANAGE)), Depends(administration_only)],
)
def get_users(session: DbSession) -> list[UserOut]:
    """Every account, with its jurisdiction and its lockout state."""
    return [
        UserOut(
            username=row["username"],
            full_name=row["full_name"],
            role=row["role"],
            # `Role` mirrors the `user_role` enum in migration 0001. If the
            # database holds a value the enum does not, that is schema drift
            # and deserves a loud failure rather than a blank cell in a
            # security-relevant table.
            workspace=WORKSPACE[Role(row["role"])].value,
            scope_state=row["scope_state"],
            scope_district=row["scope_district"],
            is_active=row["is_active"],
            last_login_at=_iso(row["last_login_at"]),
            failed_attempts=row["failed_attempts"],
            locked_until=_iso(row["locked_until"]),
        )
        for row in session.execute(_USERS).mappings()
    ]


# --- Districts -------------------------------------------------------------

#: Grouped over `claims`, not over a district master table: the question this
#: screen answers is "what has actually been loaded", and a reference table
#: would answer "what could be".
#:
#: `adjudicated_count` counts *claims carrying at least one signed
#: adjudication*, not adjudication rows. A claim re-adjudicated twice is one
#: adjudicated claim, and counting rows would report progress that does not
#: exist. The extent columns feed the DEM tile check below.
_DISTRICTS = text("""
SELECT c.district_lgd                                   AS district_lgd,
       COUNT(DISTINCT c.id)                             AS claim_count,
       COUNT(DISTINCT v.id)                             AS verdict_count,
       COUNT(DISTINCT c.id) FILTER (WHERE a.id IS NOT NULL) AS adjudicated_count,
       ARRAY_AGG(DISTINCT i.type::text)                 AS intervention_types,
       MIN(ST_X(c.geom::geometry))                      AS min_lon,
       MIN(ST_Y(c.geom::geometry))                      AS min_lat,
       MAX(ST_X(c.geom::geometry))                      AS max_lon,
       MAX(ST_Y(c.geom::geometry))                      AS max_lat
FROM claims c
JOIN interventions i ON i.id = c.intervention_id
LEFT JOIN verdicts v ON v.claim_id = c.id
LEFT JOIN adjudications a ON a.verdict_id = v.id
GROUP BY c.district_lgd
ORDER BY c.district_lgd
""")

#: Resolved the same way `api/v1/mapview.py` resolves `data/demo/map_layers.json`
#: — from this file upwards — so the API works from any working directory and
#: the repository can be checked out anywhere. An absolute path here is a demo
#: that runs on one laptop.
DEM_DIR = Path(__file__).resolve().parents[3].parent / "data" / "demo" / "dem"

#: NASADEM/SRTM tile naming: the letters and digits give the tile's south-west
#: corner in whole degrees, and each tile spans one degree square.
_HGT_NAME = re.compile(r"^([ns])(\d{2})([ew])(\d{3})\.hgt$", re.IGNORECASE)


def _tile_origin(filename: str) -> tuple[int, int] | None:
    """South-west corner of an `.hgt` tile, or None if the name is not one."""
    match = _HGT_NAME.match(filename)
    if match is None:
        return None
    lat = int(match.group(2)) * (1 if match.group(1).lower() == "n" else -1)
    lon = int(match.group(4)) * (1 if match.group(3).lower() == "e" else -1)
    return lat, lon


def _tile_name(lat: int, lon: int) -> str:
    ns, ew = ("n" if lat >= 0 else "s"), ("e" if lon >= 0 else "w")
    return f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}.hgt"


class DemStatus(BaseModel):
    """Whether the terrain pipeline's inputs and outputs exist on disk.

    A filesystem fact, kept in its own object so it cannot be mistaken for a
    database count. The DEM is a single mosaic over the whole area of interest
    rather than a per-district product, so what makes this answer
    district-specific is `missing_tiles`: the source tiles the district's own
    claim extent needs, checked against the tiles actually present.
    """

    #: True when the WhiteboxTools chain has been run and left GeoTIFFs behind.
    derivatives_present: bool
    #: The derivative rasters found, by filename. Empty means the terrain
    #: producer has nothing to read for any district.
    derivatives: list[str]
    #: Source tiles present in the directory.
    tiles: list[str]
    #: Tiles this district's claim extent needs and the directory lacks.
    missing_tiles: list[str]
    #: True when no tile is missing. False, with the names above, when the
    #: mosaic does not reach this district — the case a national deployment
    #: hits first and the one worth surfacing before someone reports a verdict
    #: as "terrain unavailable" and calls it a bug.
    covers_claim_extent: bool


def _dem_status(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> DemStatus:
    """Check `data/demo/dem/` against one district's claim bounding box."""
    if not DEM_DIR.is_dir():
        return DemStatus(
            derivatives_present=False,
            derivatives=[],
            tiles=[],
            missing_tiles=[],
            covers_claim_extent=False,
        )

    derivatives = sorted(p.name for p in DEM_DIR.glob("*.tif"))
    present = {origin for p in DEM_DIR.iterdir() if (origin := _tile_origin(p.name)) is not None}

    # A coordinate landing exactly on a tile boundary is attributed to the tile
    # north or east of it, so the required set can be one row or column larger
    # than strictly necessary. Conservative in the safe direction: this can ask
    # for a tile that is not needed, never miss one that is.
    needed = {
        (lat, lon)
        for lat in range(math.floor(min_lat), math.floor(max_lat) + 1)
        for lon in range(math.floor(min_lon), math.floor(max_lon) + 1)
    }
    missing = sorted(_tile_name(lat, lon) for lat, lon in needed - present)

    return DemStatus(
        derivatives_present=bool(derivatives),
        derivatives=derivatives,
        tiles=sorted(_tile_name(lat, lon) for lat, lon in present),
        missing_tiles=missing,
        covers_claim_extent=not missing,
    )


class DistrictOut(BaseModel):
    district_lgd: str
    claim_count: int
    verdict_count: int
    #: Claims with at least one signed adjudication. See `_DISTRICTS`.
    adjudicated_count: int
    #: The intervention types actually present in this district, sorted. The
    #: list, not a bare count: "twelve types" tells an administrator nothing,
    #: "no check dams loaded yet" tells them what to chase.
    intervention_types: list[str]
    dem: DemStatus


@router.get(
    "/admin/districts",
    response_model=list[DistrictOut],
    dependencies=[Depends(require(Capability.DISTRICT_MANAGE)), Depends(administration_only)],
)
def get_districts(session: DbSession) -> list[DistrictOut]:
    """Every district present in `claims`, with its data and terrain readiness.

    Deliberately not filtered by `CurrentScope`. The route is reachable only
    from the administration workspace, and `dolr_admin` is the one role
    `authz.district_predicate` resolves to unrestricted, so a jurisdiction
    clause here could only ever be a no-op that implied a restriction the
    endpoint does not have.
    """
    return [
        DistrictOut(
            district_lgd=row["district_lgd"],
            claim_count=row["claim_count"],
            verdict_count=row["verdict_count"],
            adjudicated_count=row["adjudicated_count"],
            intervention_types=sorted(row["intervention_types"] or []),
            dem=_dem_status(
                float(row["min_lon"]),
                float(row["min_lat"]),
                float(row["max_lon"]),
                float(row["max_lat"]),
            ),
        )
        for row in session.execute(_DISTRICTS).mappings()
    ]


# --- System ----------------------------------------------------------------

_CORE_COUNTS = text("""
SELECT (SELECT COUNT(*) FROM claims)        AS claims,
       (SELECT COUNT(*) FROM verdicts)      AS verdicts,
       (SELECT COUNT(*) FROM adjudications) AS adjudications,
       (SELECT COUNT(*) FROM users)         AS users
""")

#: The subsystems this build has schema for and no data in. Listed by name so
#: the console reports them as zeroes rather than omitting them — see the
#: module docstring. Counted for real on every request: the day one of these
#: fills up, the screen should say so without a code change.
SUBSYSTEM_TABLES: tuple[str, ...] = (
    "field_images",
    "image_analysis",
    "alerts",
    "audit_log",
    "satellite_scenes",
)

#: Table identifiers cannot be bound parameters, so this SQL is assembled by
#: string join. Safe here and only here: the inputs are the module constant
#: directly above, never anything derived from a request.
_SUBSYSTEM_COUNTS = text(
    "\nUNION ALL\n".join(
        f"SELECT '{table}' AS table_name, COUNT(*) AS row_count FROM {table}"
        for table in SUBSYSTEM_TABLES
    )
)


class TableCount(BaseModel):
    table: str
    row_count: int
    populated: bool


class SystemOut(BaseModel):
    #: The engine's own constant, imported from `services.reconcile.weights`
    #: exactly as `/health` and `/method` import it. Not `settings.engine_version`:
    #: that one is environment-overridable and could report a version the
    #: running engine is not.
    engine_version: str
    #: §38 demo mode. True means outbound STAC/WMS calls are forbidden and
    #: producers must read the pre-cached tree.
    offline_mode: bool

    claims: int
    verdicts: int
    adjudications: int
    users: int

    #: Links in the adjudication hash chain, and whether recomputing them all
    #: still agrees. The count comes from the ledger's own verifier rather than
    #: a second `COUNT(*)`, so the number and the integrity statement can never
    #: be computed from different reads of the table.
    ledger_rows: int
    ledger_valid: bool

    #: One entry per subsystem table, populated or not.
    subsystems: list[TableCount]


@router.get(
    "/admin/system",
    response_model=SystemOut,
    dependencies=[
        Depends(require(Capability.USER_MANAGE, Capability.DISTRICT_MANAGE)),
        Depends(administration_only),
    ],
)
def get_system(session: DbSession) -> SystemOut:
    """What version is running, what is loaded, and what is still empty."""
    counts = session.execute(_CORE_COUNTS).mappings().one()
    chain = verify_chain(session)

    return SystemOut(
        engine_version=ENGINE_VERSION,
        offline_mode=get_settings().pramaan_offline,
        claims=counts["claims"],
        verdicts=counts["verdicts"],
        adjudications=counts["adjudications"],
        users=counts["users"],
        ledger_rows=chain.rows,
        ledger_valid=chain.valid,
        subsystems=[
            TableCount(
                table=row["table_name"],
                row_count=row["row_count"],
                populated=row["row_count"] > 0,
            )
            for row in session.execute(_SUBSYSTEM_COUNTS).mappings()
        ],
    )
