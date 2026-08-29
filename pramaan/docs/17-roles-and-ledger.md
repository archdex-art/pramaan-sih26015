# Auth, Roles, and Ledger — What Was Built and What Wasn't

## What was built

### Authentication (complete)

| Component | Location | Notes |
|---|---|---|
| Argon2id password hashing | `app/core/security.py` | OWASP parameters: t=2, m=19 MiB, p=1 |
| RS256 JWT access tokens | `app/core/security.py` | 20-min TTL; keypair from `JWT_KEY_DIR` |
| Refresh token rotation | `app/core/security.py` + `refresh_tokens` table | Family-based; reuse detection revokes the whole family |
| Lockout | `app/core/security.py` | 5 failures → 30-min lockout; exponential backoff |
| Login / refresh / logout / me | `app/api/v1/auth.py` | Standard endpoints |
| Seed users | `scripts/seed_users.py` | 7 accounts across 6 roles, 2 districts |

### Enforcement (complete)

| Component | Location | Notes |
|---|---|---|
| Role → capability matrix | `app/core/authz.py` | 7 capabilities; role checked at token decode, capability at endpoint |
| Jurisdiction scope | `app/api/scope.py` | SQL predicate filters by district; SLNA has state scope |
| Endpoint gates | Every router in `app/api/v1/` | `Depends(require(Capability.X))` on every route |
| Cross-district isolation | `register_clause()` + scope filter | Returns 404 not 403 — no existence oracle |
| Privilege separation | `app/db/session.py` + migration 0004 | `SET ROLE pramaan_app` on connect; app role has INSERT+SELECT only on `adjudications` |

### Adjudication ledger (complete)

| Component | Location | Notes |
|---|---|---|
| Hash-chained append | `app/services/audit/ledger.py` | SHA-256; hash-then-insert (no UPDATE); advisory lock for serialisation |
| Chain verification | `app/services/audit/ledger.py` | `verify_chain()` recomputes every link |
| API endpoints | `app/api/v1/adjudication.py` | POST adjudicate, GET verify, GET ledger |
| Standalone verifier | `scripts/verify_ledger_chain.py` | Connects directly to DB; no API token needed |
| Append-only control | Migration 0004 + `SET ROLE` | UPDATE and DELETE denied to app role; proven via psql |

### Frontend auth (complete)

| Component | Location | Notes |
|---|---|---|
| Token-in-memory auth | `src/lib/auth.ts` | No localStorage; page refresh forces re-login |
| Auth-aware fetch | `src/lib/auth.ts` → `src/lib/api.ts` | All API calls go through `authFetch`; transparent 401 refresh |
| Login screen | `src/screens/Login.tsx` | Government-aesthetic, no decoration |
| Adjudication panel | `src/components/AdjudicationPanel.tsx` | Decision form; shows receipt with hash chain link |
| Ledger view | `src/screens/Ledger.tsx` | Chain entries + validity badge |
| Admin panel | `src/screens/Admin.tsx` | Identity, role, capabilities introspection |
| Role-scoped navigation | `src/App.tsx` Rail component | `can()` gates nav items; role gates Admin link |

## Roles matrix

| Role | Workspace | Adjudicate | Ledger | Claims scope | Admin |
|---|---|---|---|---|---|
| `wcdc` | monitoring | ✓ | ✓ | own district | — |
| `slna` | monitoring | ✓ | ✓ | own state | — |
| `pia` | field | — | — | own district | — |
| `wdt` | field | — | — | own district | — |
| `readonly` | monitoring | — | ✓ | own district | — |
| `dolr_admin` | administration | — | ✓ | national (all) | ✓ |

## How the three workspaces are differentiated

**There is one login form and one deployment.** Three separate portals would
triple the authentication surface and the drift between them while adding
nothing: the real isolation is in the data layer (jurisdiction predicate) and the
capability gates, both of which run server-side on every request regardless of
which screen the user came from.

Differentiation happens *after* authentication, in this order:

1. **The server decides.** `WORKSPACE` in `app/core/authz.py` maps role →
   workspace (`field` | `monitoring` | `administration`). The client never
   computes this.
2. **The login response carries it.** `POST /auth/login` returns a `principal`
   object containing `role`, `workspace`, `capabilities` and `scope_district`.
   The frontend adopts that object verbatim as its session — it does **not**
   decode the JWT to derive them, because a client that derives authorisation
   facts itself will eventually disagree with the server that enforces them.
3. **The console adapts.** `src/App.tsx` renders a workspace badge in the rail,
   shows "Adjudication ledger" only when `can("ledger:verify")`, and shows
   "Administration" only when `workspace === "administration"`. The
   adjudication panel renders a signing form when `can("adjudication:create")`
   and an explanatory read-only note otherwise.
4. **The API re-checks everything.** Every gated affordance corresponds to a
   `Depends(require(Capability.X))` on the route. Hiding a button is a courtesy;
   the 403 is the control. Verified: `pia` hitting `/ledger` directly gets 403,
   not a page.

So an operator can tell which workspace they are in from the rail badge, and a
reviewer can tell from `/auth/me`.

## Why "invalid username or password" is the same for a missing account

Deliberate, and it must stay that way. Separate messages ("no such user" vs
"wrong password") turn the login form into a directory of valid government
usernames — the reconnaissance step before credential stuffing.

The server closes the *timing* channel too: `login()` in
`app/services/auth/session.py` verifies the submitted password against a
throwaway hash when the username does not exist, so the absent-user path costs
the same Argon2id work as the wrong-password path. Skipping that would leak
account existence through response latency even with identical text.

A **lockout** is treated differently on purpose: the credentials may well be
correct and the remedy is to wait, so it returns `429` with `Retry-After` and the
login screen renders a live countdown.

## Login defects found and fixed

Both were found by measurement against the running system, not by review.

### 1. Concurrent refresh revoked the user's own session

The server implements refresh rotation with replay detection: presenting one
refresh token twice revokes the whole family. The console fetches concurrently —
`App.tsx` issues three requests per claim, `Ledger.tsx` and `MethodDrawer.tsx`
two each. When the access token expired, every one of those 401'd at the same
instant and every one called `/auth/refresh` with the *same* token.

Measured before the fix, three concurrent refreshes returned:

```
refresh #0: 401 refresh token reuse detected; 2 session token(s) revoked
refresh #1: 200
refresh #2: 401 invalid refresh token
successor token still usable? NO — family revoked
```

The token that *succeeded* was already dead. Effect: the user was signed out on
the first claim navigation after each 20-minute expiry.

**Fix.** `refreshOnce()` in `src/lib/auth.ts` coalesces all concurrent refreshes
into one in-flight promise; callers await it and retry with whatever token it
produced. Server-side rotation stays strict — the client simply stops presenting
a consumed token. Measured after: three concurrent 401s → exactly **one**
refresh call, all three requests return 200, session survives.

### 2. Sign-out never revoked anything

`POST /auth/logout` takes the refresh token in the request **body**. The client
sent only an `Authorization` header, so every sign-out returned `422` and the
refresh family stayed valid server-side until its own 12-hour expiry — a session
that outlived the user ending it.

**Fix.** `logout()` sends `{ refresh_token }`, uses `keepalive: true` so the
revocation survives tab teardown, and clears local state first so the UI never
waits on the network. Measured after: live refresh rows for the user fall on
sign-out.

### 3. Sign-out during an in-flight refresh could resurrect the session

A refresh that started before sign-out would complete afterwards and install its
principal, restoring the session the user had just ended.

**Fix.** A `generation` counter is incremented by every `login` and `logout`; a
refresh captures it at start and discards its result if the identity changed
while it was in flight. The same counter stops a failed refresh from signing out
a *different* session that signed in during the await.

### 4. The anti-enumeration mitigation was itself a timing oracle

`login()` verified the submitted password against a throwaway hash when the
username did not exist, so both failures returned identical text. The intent was
right; the implementation leaked. It called `hash_password(...)` **inline on
every request**, so the absent-user path performed two Argon2id operations
(one hash, one verify) against the real path's one verify.

Measured over 12 samples each, before the fix:

```
absent user   median = 53.5 ms   min 52.0
wrong passwd  median = 29.8 ms   min 27.1
differential  = +23.7 ms, non-overlapping ranges
```

A +23.7 ms separation with non-overlapping ranges is a reliable enumeration
oracle — the exact vulnerability the code's own comment claimed to prevent, just
inverted: "user exists" was now the *fast* answer.

**Fix.** The dummy hash is computed once per process (`_absent_user_hash`,
`lru_cache(maxsize=1)`), so the absent path does one verify with the same cost
parameters as a real one. Produced by `hash_password` rather than hard-coded, so
it tracks any future parameter change instead of silently becoming cheaper.

Measured after, 15 samples each:

```
absent user   median = 28.8 ms   range 26.6 – 30.0
wrong passwd  median = 28.8 ms   range 27.4 – 36.9
differential  = +0.0 ms, ranges overlap
```

## Gaps not closed (honest)

1. **Per-officer signing keys** — The ledger uses application-level SHA-256 chains, not PKI signatures. The chain proves integrity (no row was altered after the fact) but not non-repudiation (it does not prove which physical person pressed the key). Implementing this requires a certificate authority and key management infrastructure that is outside a hackathon scope. Stated in the `/ledger/verify` response.

2. **`SET ROLE` is not the same as separate credentials** — A compromised process can `RESET ROLE` back to the table owner. The control defends against accidental application-level writes (the realistic threat), not against arbitrary SQL execution. Stated in `app/db/session.py` docstring.

3. **Refresh token stored in JS memory** — Survives only the tab lifetime. A more secure approach would be HttpOnly cookies, but that requires server-side CSRF handling and changes the API contract. The current approach prevents XSS-based token theft from persistent storage.

4. **No RBAC UI for user management** — Users are seeded via `scripts/seed_users.py`. A CRUD admin interface would need password reset flow, role change audit trail, and MFA consideration. The current model proves the role system works; it does not pretend to be a user management product.

5. **No MFA** — Multi-factor authentication is not implemented. For a government evidence system in production, TOTP or hardware key support would be required.

6. **`scope_district` is a comma-separated string** — In the token claim and in
   the login response. Fine for the 1–2 districts a real WCDC officer covers; an
   officer scoped to 50 districts would bloat every request header. Would need a
   server-side scope lookup at that size.

7. **Access-token revocation is not immediate** — Capabilities are derived from
   the role at decode time, so a role change takes effect on the next refresh
   (≤20 min), not instantly. Refresh tokens *are* revocable immediately, which is
   what makes sign-out and replay-detection work. A deactivated user keeps read
   access for at most one access-token TTL. Closing that gap means a
   per-request revocation check — a database round trip on every call — which is
   the tradeoff named in `issue_access_token`.

8. **No password reset or rotation flow** — Passwords are set by
   `scripts/seed_users.py`. `needs_rehash` upgrades the stored hash on login when
   Argon2id parameters change, but there is no user-facing "change my password"
   path, and no forced-rotation policy.

9. **No audit trail for authentication events** — Logins, lockouts and refresh
   replays are enforced but not recorded in `audit_log`. The *adjudication*
   ledger is complete; the *authentication* history is not, so "who tried to sign
   in and failed" cannot be answered after the fact.
