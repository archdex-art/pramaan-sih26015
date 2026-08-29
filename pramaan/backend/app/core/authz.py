"""The authorisation contract. Roles, capabilities and jurisdiction, in one file.

## Why this is one frozen module and not scattered decorators

docs §25.1 requires jurisdiction scoping *"enforced in the data layer, not in
the UI"*, and warns that a scoping bug is a data leak rather than a rendering
defect. Both properties only hold if there is exactly one place that answers
"what may this principal see and do". Two places drift, and the drift is silent.

Nothing here does IO, reads the environment, or touches a request. It is a pure
mapping from a principal to permissions, which is what makes it testable by
enumeration — see `tests/unit/test_authz.py`, which asserts the matrix rather
than trusting it.

## The roles are the government's own

`dolr_admin`, `slna`, `wcdc`, `pia`, `wdt`, `readonly` are the roles SRISHTI
itself defines (docs §25.1 **[VERIFIED]**), and the `user_role` enum in
migration 0001 is already this list. Inventing a friendlier set of three would
have meant translating at every boundary and explaining the translation to the
department that owns the vocabulary.

## Separation of duties is the load-bearing rule

The console's central promise — *"nothing here becomes government evidence until
a named officer signs it"* — is worth nothing if the officer who signs is also
the person who submitted the claim. So:

- Field roles (`wdt`, `pia`) may **create** claims and may never adjudicate.
- Monitoring roles (`wcdc`, `slna`) may **adjudicate** and may never create
  claims.
- `dolr_admin` administers the system and may do **neither**. An administrator
  who can both create a user and sign that user's evidence is a single point of
  total compromise of the ledger.

That last line is the one a reviewer should test, and `CAPABILITIES` below is
written so they can read it off the table in ten seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """Mirrors the `user_role` enum in migration 0001. Do not reorder casually."""

    DOLR_ADMIN = "dolr_admin"
    SLNA = "slna"
    WCDC = "wcdc"
    PIA = "pia"
    WDT = "wdt"
    READONLY = "readonly"


class Capability(StrEnum):
    """Verbs, not screens.

    Gating on capabilities rather than on roles is what stops the check
    `if role == "wcdc"` appearing in six routers and then disagreeing with
    itself when a seventh role is added.
    """

    CLAIM_READ = "claim:read"
    CLAIM_CREATE = "claim:create"
    EVIDENCE_READ = "evidence:read"
    VERDICT_READ = "verdict:read"
    VERDICT_RECOMPUTE = "verdict:recompute"
    ADJUDICATION_CREATE = "adjudication:create"
    LEDGER_VERIFY = "ledger:verify"
    USER_MANAGE = "user:manage"
    DISTRICT_MANAGE = "district:manage"


#: The whole authorisation policy. Every capability a role does not hold is a
#: 403, and the omissions are deliberate:
#:
#: - No field role holds ADJUDICATION_CREATE.
#: - No monitoring role holds CLAIM_CREATE.
#: - DOLR_ADMIN holds neither, and holds no VERDICT_RECOMPUTE either: recompute
#:   re-runs the engine over stored evidence, and while it cannot change the
#:   evidence, letting the administrator drive it blurs a line worth keeping
#:   sharp.
#: - READONLY holds only reads. It exists for auditors and for the observer
#:   accounts a demo needs, so that "look but do not touch" is a real grant
#:   rather than a disabled button.
CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.WDT: frozenset(
        {
            Capability.CLAIM_READ,
            Capability.CLAIM_CREATE,
            Capability.EVIDENCE_READ,
            Capability.VERDICT_READ,
        }
    ),
    Role.PIA: frozenset(
        {
            Capability.CLAIM_READ,
            Capability.CLAIM_CREATE,
            Capability.EVIDENCE_READ,
            Capability.VERDICT_READ,
        }
    ),
    Role.WCDC: frozenset(
        {
            Capability.CLAIM_READ,
            Capability.EVIDENCE_READ,
            Capability.VERDICT_READ,
            Capability.VERDICT_RECOMPUTE,
            Capability.ADJUDICATION_CREATE,
            Capability.LEDGER_VERIFY,
        }
    ),
    Role.SLNA: frozenset(
        {
            Capability.CLAIM_READ,
            Capability.EVIDENCE_READ,
            Capability.VERDICT_READ,
            Capability.VERDICT_RECOMPUTE,
            Capability.ADJUDICATION_CREATE,
            Capability.LEDGER_VERIFY,
        }
    ),
    Role.DOLR_ADMIN: frozenset(
        {
            Capability.CLAIM_READ,
            Capability.EVIDENCE_READ,
            Capability.VERDICT_READ,
            Capability.LEDGER_VERIFY,
            Capability.USER_MANAGE,
            Capability.DISTRICT_MANAGE,
        }
    ),
    Role.READONLY: frozenset(
        {
            Capability.CLAIM_READ,
            Capability.EVIDENCE_READ,
            Capability.VERDICT_READ,
            Capability.LEDGER_VERIFY,
        }
    ),
}


class Workspace(StrEnum):
    """Which of the three workspaces a role lands in.

    Three workspaces, one deployment. Three separate portals would triple the
    authentication surface and the drift while adding nothing: §25.1 puts the
    real control in the data layer, so the number of front ends is a question
    about task focus, not about security.
    """

    FIELD = "field"
    MONITORING = "monitoring"
    ADMINISTRATION = "administration"


WORKSPACE: dict[Role, Workspace] = {
    Role.WDT: Workspace.FIELD,
    Role.PIA: Workspace.FIELD,
    Role.WCDC: Workspace.MONITORING,
    Role.SLNA: Workspace.MONITORING,
    Role.READONLY: Workspace.MONITORING,
    Role.DOLR_ADMIN: Workspace.ADMINISTRATION,
}


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, as decoded from an access token.

    Frozen because a request handler that can mutate its own principal can
    escalate its own privileges, and that is a class of bug worth making
    impossible rather than reviewing for.
    """

    user_id: str
    username: str
    full_name: str
    role: Role
    scope_state: str | None
    scope_district: str | None

    @property
    def capabilities(self) -> frozenset[Capability]:
        return CAPABILITIES[self.role]

    def can(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @property
    def workspace(self) -> Workspace:
        return WORKSPACE[self.role]

    @property
    def is_national(self) -> bool:
        """Only `dolr_admin` sees every district.

        Checked against the role, never against "scope is empty". A user whose
        scope failed to load must collapse to seeing nothing, not to seeing
        everything — see `district_predicate`.
        """
        return self.role is Role.DOLR_ADMIN


@dataclass(frozen=True, slots=True)
class ScopeFilter:
    """A jurisdiction restriction, expressed so SQL cannot forget to apply it.

    `unrestricted` is a separate flag rather than "district is None", because
    those two conditions must produce opposite results and conflating them is
    exactly how a scoping bug becomes a national data leak.
    """

    unrestricted: bool
    district_lgd: str | None
    #: True when the principal has no usable jurisdiction. Fails closed: the
    #: query must return nothing rather than everything.
    denies_everything: bool


def district_predicate(principal: Principal) -> ScopeFilter:
    """Resolve a principal to a jurisdiction filter. Fail closed.

    Three outcomes and no fourth:

    1. `dolr_admin` -> unrestricted.
    2. A role with a district -> restricted to that district.
    3. Anything else, including a state-scoped `slna` whose district mapping is
       absent -> denies everything.

    Outcome 3 is the important one. `slna` is scoped to a state, and this build
    has no state-to-district table, so an `slna` principal genuinely cannot be
    resolved to a district list. Returning "unrestricted" there would be the
    convenient choice and would hand one state's officer every state's data.
    """
    if principal.is_national:
        return ScopeFilter(unrestricted=True, district_lgd=None, denies_everything=False)
    if principal.scope_district:
        return ScopeFilter(
            unrestricted=False,
            district_lgd=principal.scope_district,
            denies_everything=False,
        )
    return ScopeFilter(unrestricted=False, district_lgd=None, denies_everything=True)
