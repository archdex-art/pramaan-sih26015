/**
 * Authentication state: login, logout, token lifecycle.
 *
 * ## Where tokens live, and why
 *
 * The **refresh token** is kept in `sessionStorage`; the **access token** stays
 * in module memory only.
 *
 * This is a deliberate change from an earlier memory-only design. That version
 * was defensible in the abstract — nothing on disk, nothing for XSS to read —
 * but it made every page reload dump the officer back to the login screen,
 * including an accidental Cmd-R in the middle of adjudicating. For a console
 * whose whole job is careful review of one claim at a time, losing context on
 * reload is not a security win, it is a correctness problem that trains people
 * to keep a password manager open.
 *
 * The tradeoff is bounded on purpose:
 *
 * - `sessionStorage`, not `localStorage`: scoped to the tab. Closing the tab or
 *   the browser ends the session. A shared district-office machine does not
 *   hand the next person a live signing session, which was the original concern.
 * - Only the **refresh** token is stored. The access token — the credential that
 *   actually authorises requests — is never written to disk, so a token lifted
 *   from storage still has to be exchanged through `/auth/refresh`, which is
 *   rotating, replay-detecting, and revocable server-side.
 * - Server-side revocation still wins: `logout` revokes the family immediately,
 *   and the refresh token has its own 12-hour cap.
 *
 * Residual risk, stated plainly: an XSS payload could read the refresh token out
 * of `sessionStorage` and mint access tokens until the family is revoked or
 * expires. The stronger design is an `HttpOnly` cookie, which the browser will
 * not expose to script at all — that requires server-set cookies plus CSRF
 * protection and is recorded as a known gap in `docs/17-roles-and-ledger.md`.
 *
 * ## Why there is no proactive refresh timer
 *
 * A timer that renews the access token every ~15 minutes would eliminate the
 * one failed request per expiry — and would also keep an abandoned session
 * alive indefinitely. Renewal is therefore *reactive*: it happens only in
 * response to a real 401 caused by a real user action.
 *
 * ## Why refresh is single-flight
 *
 * The server implements refresh-token rotation with replay detection: using one
 * refresh token twice revokes the entire token family. Screens in this console
 * fetch concurrently (`App.tsx` issues three requests per claim; `Ledger.tsx`
 * and `MethodDrawer.tsx` two each). Without coordination, an expired access
 * token makes every one of those requests 401 at the same instant, every one
 * calls `/auth/refresh` with the *same* refresh token, the first succeeds and
 * the rest are correctly identified as replays — killing the family and logging
 * the user out mid-session, including the successor token that had just been
 * issued.
 *
 * Measured, before this was fixed: three concurrent refreshes returned
 * `200`, `401 reuse detected; 2 session token(s) revoked`, `401 invalid`, and
 * the surviving successor token was already dead.
 *
 * So all concurrent callers share exactly one in-flight refresh and then retry
 * with whatever token it produced. Rotation stays strict on the server; the
 * client simply stops presenting the same token twice.
 */

// --- types -------------------------------------------------------------------

/** The authenticated officer, as returned by the server.
 *
 *  Sourced from the login/refresh response body's `principal`, never from
 *  client-side JWT decoding: the server is the authority on role, workspace and
 *  capabilities, and a client that derives them itself will eventually disagree
 *  with the server that enforces them. */
export interface Session {
  user_id: string;
  username: string;
  full_name: string;
  role: string;
  /** `field` | `monitoring` | `administration` — selects the landing screen. */
  workspace: string;
  /** District LGD codes this principal is scoped to. Empty means national. */
  districts: number[];
  /** Capabilities, for gating UI affordances only. The API re-checks every one. */
  capabilities: string[];
}

/** A login failure that carries the server's reason and, for a lockout, how
 *  long the caller must wait. Thrown instead of a bare `Error` so the login
 *  screen can render a countdown rather than a sentence about seconds. */
export class AuthError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly retryAfterSeconds: number | null = null,
  ) {
    super(message);
    this.name = "AuthError";
  }

  get isLockout(): boolean {
    return this.status === 429;
  }
}

// --- state -------------------------------------------------------------------

let accessToken: string | null = null;
let refreshToken: string | null = null;
let session: Session | null = null;
let listeners: Array<() => void> = [];

/** The one in-flight refresh, shared by every concurrent 401. See the module
 *  docstring: this single variable is what stops replay-detection from
 *  revoking the family. */
let refreshInFlight: Promise<boolean> | null = null;

/** Incremented by every `login` and `logout`.
 *
 * A refresh that started before the identity changed must not install its
 * result afterwards. Without this, pressing "Sign out" while a refresh was in
 * flight resurrects the session the user just ended: the in-flight call holds
 * its own copy of the old refresh token, completes, and adopts a principal for
 * an identity that is no longer current. */
let generation = 0;

/** `sessionStorage` key holding the refresh token. Tab-scoped by definition. */
const STORE_KEY = "pramaan.rt";

/** Storage can throw: Safari private mode and some managed-device policies make
 *  `sessionStorage` a getter that raises rather than a null. Auth must not be
 *  the thing that breaks the app, so every access is guarded and a failure
 *  degrades to memory-only — the pre-existing behaviour. */
function storeRefresh(token: string | null): void {
  try {
    if (token === null) sessionStorage.removeItem(STORE_KEY);
    else sessionStorage.setItem(STORE_KEY, token);
  } catch {
    // Memory-only session; reload will require sign-in again.
  }
}

function readStoredRefresh(): string | null {
  try {
    return sessionStorage.getItem(STORE_KEY);
  } catch {
    return null;
  }
}

function notify(): void {
  // Copy before iterating: a listener that unsubscribes during notification
  // would otherwise mutate the array being walked and skip its neighbour.
  for (const fn of [...listeners]) fn();
}

/** Subscribe to auth state changes. Returns an unsubscribe function. */
export function subscribe(fn: () => void): () => void {
  listeners.push(fn);
  return () => {
    listeners = listeners.filter((l) => l !== fn);
  };
}

export function getSession(): Session | null {
  return session;
}

/** Does the current session hold a capability? UI gating only — never a
 *  substitute for the server-side check, which runs on every request. */
export function can(capability: string): boolean {
  return session?.capabilities.includes(capability) ?? false;
}

// --- wire shapes -------------------------------------------------------------

/** Mirrors the API's `TokenOut`. `login` and `refresh` return the same shape,
 *  which is why one adapter serves both. */
interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  principal: {
    user_id: string;
    username: string;
    full_name: string;
    role: string;
    workspace: string;
    scope_state: string | null;
    scope_district: string | null;
    capabilities: string[];
  };
}

function isTokenResponse(v: unknown): v is TokenResponse {
  if (typeof v !== "object" || v === null) return false;
  const o = v as Record<string, unknown>;
  if (typeof o.access_token !== "string" || typeof o.refresh_token !== "string") {
    return false;
  }
  const p = o.principal;
  if (typeof p !== "object" || p === null) return false;
  const po = p as Record<string, unknown>;
  return (
    typeof po.user_id === "string" &&
    typeof po.username === "string" &&
    typeof po.role === "string" &&
    typeof po.workspace === "string" &&
    Array.isArray(po.capabilities)
  );
}

/** Adopt a token pair and its principal as the live session. */
function adopt(data: TokenResponse): Session {
  accessToken = data.access_token;
  refreshToken = data.refresh_token;
  // Persist the rotated token, not the one we presented: rotation means the
  // old value is already dead server-side, and storing it would guarantee a
  // replay on the next reload — which revokes the whole family.
  storeRefresh(data.refresh_token);

  const p = data.principal;
  session = {
    user_id: p.user_id,
    username: p.username,
    full_name: p.full_name,
    role: p.role,
    workspace: p.workspace,
    // `scope_district` is a comma-separated LGD list, or null for national
    // roles. Filtering non-finite values keeps a malformed field from
    // producing a `[NaN]` scope that would render as "district NaN".
    districts: (p.scope_district ?? "")
      .split(",")
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isFinite(n) && n > 0),
    capabilities: p.capabilities,
  };
  return session;
}

/** Pull the server's own reason out of an error body. A generic "login failed"
 *  would hide "account locked", which has a different remedy. */
async function reasonFor(res: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await res.json();
    if (body !== null && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string" && detail.length > 0) return detail;
    }
  } catch {
    // Non-JSON body (proxy error page, empty 502). Fallback is the best we have.
  }
  return fallback;
}

function retryAfterOf(res: Response): number | null {
  const raw = res.headers.get("Retry-After");
  if (raw === null) return null;
  const secs = Number(raw);
  return Number.isFinite(secs) && secs >= 0 ? Math.ceil(secs) : null;
}

// --- login / logout ----------------------------------------------------------

export async function login(username: string, password: string): Promise<Session> {
  let res: Response;
  try {
    res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      cache: "no-store",
    });
  } catch {
    // Distinguish "cannot reach the API" from "the API said no". Operators in
    // district offices hit the first case often, and it needs a different
    // action than a password reset.
    throw new AuthError(0, "Cannot reach the server. Check the network and try again.");
  }

  if (!res.ok) {
    throw new AuthError(
      res.status,
      await reasonFor(res, "Sign-in failed."),
      retryAfterOf(res),
    );
  }

  const data: unknown = await res.json();
  if (!isTokenResponse(data)) {
    // A 200 whose body we cannot read is a contract break, not a credential
    // problem. Saying so prevents an operator retyping a correct password.
    throw new AuthError(res.status, "The server returned an unrecognised sign-in response.");
  }

  generation += 1;
  const adopted = adopt(data);
  notify();
  return adopted;
}

/** End the session, server-side first.
 *
 * The server's logout endpoint takes the **refresh token** in the body — an
 * `Authorization` header alone leaves the refresh family alive until its own
 * expiry, which is a session that outlives the user pressing "Sign out". The
 * local wipe happens regardless of whether the network call succeeds. */
export function logout(): void {
  const token = refreshToken;

  generation += 1;
  accessToken = null;
  refreshToken = null;
  session = null;
  refreshInFlight = null;
  storeRefresh(null);
  notify();

  if (token !== null) {
    void fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
      cache: "no-store",
      // The tab may be closing. `keepalive` lets the browser finish the
      // revocation after teardown instead of cancelling it in flight.
      keepalive: true,
    }).catch(() => {
      // Offline sign-out still clears local state; the token expires server-side.
    });
  }
}

// --- refresh -----------------------------------------------------------------

/** Perform one refresh. Never call directly — go through `refreshOnce`. */
async function doRefresh(token: string): Promise<boolean> {
  const startedAt = generation;
  let res: Response;
  try {
    res = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
      cache: "no-store",
    });
  } catch {
    // Network failure is not proof the session is dead. Keep the tokens so the
    // next user action can retry; dropping them here would sign people out
    // every time a district office's link flaps.
    return false;
  }

  if (!res.ok) return false;

  const data: unknown = await res.json();
  if (!isTokenResponse(data)) return false;

  // The identity changed while this was in flight (sign-out, or sign-in as
  // someone else). Installing this principal now would resurrect a session the
  // user already ended. Discard it; the server-side token is orphaned and
  // expires on its own.
  if (startedAt !== generation) return false;

  adopt(data);
  notify();
  return true;
}

/** Coalesce concurrent refreshes into one network call.
 *
 * Callers that arrive while a refresh is in flight await that same promise
 * instead of presenting the already-consumed refresh token a second time. */
function refreshOnce(): Promise<boolean> {
  if (refreshInFlight !== null) return refreshInFlight;

  const token = refreshToken;
  if (token === null) return Promise.resolve(false);

  refreshInFlight = doRefresh(token).finally(() => {
    // Cleared on settle so a *later* expiry starts a fresh attempt. Callers
    // already awaiting hold their own reference to the promise.
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** Rebuild a session from the tab's stored refresh token, once, at boot.
 *
 * Called by `App` before deciding whether to show the login screen. Resolves to
 * the restored `Session`, or `null` when there is nothing stored or the stored
 * token is dead (expired, revoked, or issued by a server that has since rotated
 * its signing key).
 *
 * A failed restore deliberately clears storage. Leaving a known-dead token
 * behind would make every subsequent reload perform a pointless round trip and,
 * worse, could present an already-consumed token and trip replay detection —
 * revoking a family the user had legitimately re-established in another tab.
 */
export async function restore(): Promise<Session | null> {
  if (session !== null) return session;

  const stored = readStoredRefresh();
  if (stored === null) return null;

  refreshToken = stored;
  const ok = await refreshOnce();
  if (!ok) {
    refreshToken = null;
    accessToken = null;
    storeRefresh(null);
    return null;
  }
  return session;
}

// --- auth-aware fetch --------------------------------------------------------

/** Auth endpoints manage their own tokens; a 401 from one of them must never
 *  trigger a refresh, or a failed login would try to renew a dead session. */
const AUTH_PATHS = ["/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout"];

function send(path: string, init: RequestInit | undefined, token: string | null): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", "application/json");
  // `no-store`: every read here returns mutable state. A register showing a
  // stale verdict after adjudication is worse than a slow one.
  return fetch(path, { ...init, headers, cache: "no-store" });
}

/**
 * `fetch` with the access token attached and exactly one transparent renewal.
 *
 * On 401: refresh once (shared across concurrent callers), then replay the
 * original request with the new token. If the refresh fails, the session is
 * genuinely over — wipe it, which flips the UI to the login screen — and return
 * the 401 so the caller still sees a definite outcome rather than hanging.
 */
export async function authFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await send(path, init, accessToken);

  if (res.status !== 401) return res;
  if (AUTH_PATHS.some((p) => path.startsWith(p))) return res;

  // Capture the identity this request belonged to, so a failure is attributed
  // to that session and not to whatever session exists when the await returns.
  const attemptedAt = generation;
  if (refreshToken === null) return res;

  const renewed = await refreshOnce();
  if (!renewed) {
    // Wipe only if this is still the same session. A sibling caller may have
    // already logged out, and a *different* user may have signed in during the
    // await — clearing unconditionally would sign that new session out too.
    if (attemptedAt === generation) logout();
    return res;
  }

  return send(path, init, accessToken);
}
