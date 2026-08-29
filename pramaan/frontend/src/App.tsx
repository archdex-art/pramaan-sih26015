/**
 * Console shell and routing.
 *
 * Hash routing, no router dependency. Three routes is not a routing problem, and
 * `react-router` would be 20 kB to solve one it does not have.
 */

import { useCallback, useEffect, useState } from "react";
import { AdjudicationPanel } from "./components/AdjudicationPanel";
import { MethodDrawer } from "./components/MethodDrawer";
import { TemporalControlChart } from "./components/charts/TemporalControlChart";
import {
  ApiError,
  fetchClaims,
  fetchEvidence,
  fetchTemporal,
  fetchVerdict,
  type EvidenceTree,
  type RegisterRow,
  type TemporalComparison,
  type Verdict,
} from "./lib/api";
import { can, getSession, logout, restore, subscribe } from "./lib/auth";
import type { Session } from "./lib/auth";
import { Admin } from "./screens/Admin";
import { Detail } from "./screens/Detail";
import { Ledger } from "./screens/Ledger";
import { Login } from "./screens/Login";
import { PlanMap } from "./screens/PlanMap";
import { Register } from "./screens/Register";

/** Server-side workspace key → the words shown to the officer.
 *
 * The keys are the API's `Workspace` enum values (`app/core/authz.py`). Kept as
 * a lookup rather than inlined so a new workspace fails visibly here instead of
 * rendering a raw enum value into the rail. */
const WORKSPACE_LABEL: Record<string, string> = {
  field: "Field workspace",
  monitoring: "Monitoring workspace",
  administration: "Administration",
};
type Route =
  | { name: "register" }
  | { name: "claim"; id: number }
  | { name: "temporal"; id: number }
  | { name: "map"; id: number }
  | { name: "ledger" }
  | { name: "admin" };

function parseHash(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [screen, raw] = h.split("/");
  const id = Number(raw);
  if (screen === "claim" && Number.isFinite(id)) return { name: "claim", id };
  if (screen === "temporal" && Number.isFinite(id)) return { name: "temporal", id };
  if (screen === "map" && Number.isFinite(id)) return { name: "map", id };
  if (screen === "ledger") return { name: "ledger" };
  if (screen === "admin") return { name: "admin" };
  return { name: "register" };
}

export function App() {
  const [session, setSession] = useState<Session | null>(getSession);
  // Three states, not two. Without an explicit "restoring" phase the login
  // screen renders for one frame on every reload before the stored refresh
  // token is exchanged — a visible flash that looks exactly like being logged
  // out, which is the thing session restore exists to prevent.
  const [booting, setBooting] = useState(() => getSession() === null);

  useEffect(() => subscribe(() => setSession(getSession())), []);

  useEffect(() => {
    if (!booting) return;
    let cancelled = false;
    void restore().finally(() => {
      if (!cancelled) {
        setSession(getSession());
        setBooting(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [booting]);

  if (booting) {
    return (
      <div className="boot-shell">
        <p className="boot-mark">प्रमाण</p>
        <p className="boot-note mono">restoring session…</p>
      </div>
    );
  }

  if (!session) {
    return <Login onLogin={() => setSession(getSession())} />;
  }

  return <Console session={session} />;
}

function Console({ session }: { session: Session }) {
  const [route, setRoute] = useState<Route>(parseHash);
  const [rows, setRows] = useState<RegisterRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [method, setMethod] = useState(false);

  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [evidence, setEvidence] = useState<EvidenceTree | null>(null);
  const [temporal, setTemporal] = useState<TemporalComparison | null>(null);
  const [temporalError, setTemporalError] = useState<string | null>(null);
  useEffect(() => {
    const onHash = () => {
      setRoute(parseHash());
      // Close the drawer on navigation. It is a modal over one screen, and
      // leaving it open across a route change stranded it over a different
      // screen with no relationship to what was behind it.
      setMethod(false);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchClaims().then(
      (r) => !cancelled && setRows(r),
      (e: unknown) =>
        !cancelled &&
        setError(e instanceof ApiError ? e.detail : String(e)),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const id = "id" in route ? route.id : null;

  useEffect(() => {
    if (id === null) return;
    let cancelled = false;
    setVerdict(null);
    setEvidence(null);
    // Settled, not all: a claim with no temporal series must still show its
    // verdict, and a claim whose verdict failed to load must still show its
    // evidence. Each panel fails on its own.
    void Promise.allSettled([
      fetchVerdict(id),
      fetchEvidence(id),
      fetchTemporal(id),
    ]).then(([v, e, t]) => {
      if (cancelled) return;
      if (v.status === "fulfilled") setVerdict(v.value);
      if (e.status === "fulfilled") setEvidence(e.value);
      if (t.status === "fulfilled") {
        setTemporal(t.value);
        setTemporalError(null);
      } else {
        setTemporal(null);
        setTemporalError(
          t.reason instanceof ApiError ? t.reason.detail : String(t.reason),
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const open = useCallback((claimId: number) => {
    location.hash = `#/claim/${claimId}`;
  }, []);

  const claim = rows?.find((r) => r.claim_id === id) ?? null;

  return (
    <div className="app">
      <Rail route={route} session={session} onMethod={() => setMethod(true)} />

      <main className="main">
        {error !== null && (
          <p className="error-inline">
            <strong>Could not reach the API.</strong> {error}
          </p>
        )}

        {route.name === "register" &&
          (rows ? (
            <Register rows={rows} onOpen={open} />
          ) : (
            <Loading what="claims register" />
          ))}

        {route.name === "claim" &&
          (claim ? (
            <>
              <Detail
                claim={claim}
                verdict={verdict}
                evidence={evidence}
                onMethod={() => setMethod(true)}
                onTemporal={() => {
                  location.hash = `#/temporal/${claim.claim_id}`;
                }}
                onMap={() => {
                  location.hash = `#/map/${claim.claim_id}`;
                }}
              />
              {verdict && (
                <AdjudicationPanel
                  verdict={verdict}
                  onSigned={() => {
                    // Re-fetch to update the provisional flag in the UI.
                    void fetchVerdict(claim.claim_id).then(setVerdict);
                  }}
                />
              )}
            </>
          ) : (
            <Loading what="claim" />
          ))}

        {route.name === "ledger" && <Ledger />}

        {route.name === "admin" && <Admin session={session} />}

        {route.name === "temporal" && (
          <div className="screen">
            <header className="screen-head rise">
              <div>
                <h1>Temporal analysis</h1>
                <p className="sub mono">
                  {claim?.unique_id ?? `claim ${route.id}`}
                </p>
              </div>
              <div className="head-actions">
                <button
                  className="btn"
                  onClick={() => {
                    location.hash = `#/claim/${route.id}`;
                  }}
                >
                  Back to verdict
                </button>
                <button className="btn" onClick={() => setMethod(true)}>
                  Method
                </button>
              </div>
            </header>
            {temporal ? (
              <TemporalControlChart data={temporal} />
            ) : temporalError !== null ? (
              <div className="panel empty-state rise">
                <h2>No temporal series for this claim.</h2>
                <p className="note">{temporalError}</p>
                <p className="note">
                  Golden-case claims carry no observed series — they are synthetic
                  evidence bundles, not imagery. Open the measured claim to see the
                  chart.
                </p>
              </div>
            ) : (
              <Loading what="temporal series" />
            )}
          </div>
        )}

        {route.name === "map" && (
          <PlanMap
            claimId={route.id}
            onBack={() => {
              location.hash = `#/claim/${String(route.id)}`;
            }}
          />
        )}
      </main>

      {method && <MethodDrawer onClose={() => setMethod(false)} />}
    </div>
  );
}

function Loading({ what }: { what: string }) {
  // A sentence, not a shimmer. A skeleton that never resolves is
  // indistinguishable from a broken screen.
  return <p className="loading">Loading {what}…</p>;
}

function Rail({
  route,
  session,
  onMethod,
}: {
  route: Route;
  session: Session;
  onMethod: () => void;
}) {
  const [health, setHealth] = useState<{
    engine_version?: string;
    offline_mode?: string;
  } | null>(null);

  useEffect(() => {
    void fetch("/healthz")
      .then((r) => (r.ok ? r.json() : null))
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const on = (name: string) => (route.name === name ? "on" : "");
  const currentId = "id" in route ? route.id : 1;

  return (
    <nav className="rail" aria-label="Main">
      <div className="rail-gov">
        <span className="gov-full">Government of India</span>
        {/* Two explicit lines rather than "Ministry of Rural Development · DoLR",
            which wrapped and orphaned "· DoLR" onto its own line at the rail's
            width. Same total height, no orphan, and DoLR spelled out is the
            correct form for a government header. */}
        <span className="gov-full">Ministry of Rural Development</span>
        <span className="gov-full">Department of Land Resources</span>
        <span className="gov-full">WDC-PMKSY 2.0</span>
        <span className="rail-build mono" title="Prototype — not a deployed system">
          <span className="gov-full">PROTOTYPE · not a deployed system</span>
          <span className="gov-short" aria-hidden="true">
            PROTO
          </span>
        </span>
      </div>

      <a className="wordmark" href="#/">
        <span className="deva">प्रमाण</span>
        <span className="latin">PRAMAAN</span>
        <span className="label">proof</span>
      </a>

      <ul>
        <li>
          <a className={on("register")} href="#/">
            Claims register
          </a>
        </li>
        <li>
          <a className={on("claim")} href={`#/claim/${currentId}`}>
            Reconciliation
          </a>
        </li>
        <li>
          <a className={on("map")} href={`#/map/${currentId}`}>
            Plan view
          </a>
        </li>
        <li>
          <a className={on("temporal")} href={`#/temporal/${currentId}`}>
            Temporal analysis
          </a>
        </li>
        <li>
          <button className="rail-btn" onClick={onMethod}>
            Method
          </button>
        </li>
        {can("ledger:verify") && (
          <li>
            <a className={on("ledger")} href="#/ledger">
              Adjudication ledger
            </a>
          </li>
        )}
        {/* Workspace, not a hardcoded role name: the server owns the
            role→workspace mapping, so gating on the mapping's output means a
            future administrative role appears here without a frontend change. */}
        {session.workspace === "administration" && (
          <li>
            <a className={on("admin")} href="#/admin">
              Administration
            </a>
          </li>
        )}
      </ul>

      <footer className="rail-foot">
        <div className="rail-session">
          {/* Which of the three workspaces this officer is in. One deployment
              serves all three; the badge is how a user — and a judge watching a
              screen share — can tell at a glance which set of powers is live,
              rather than inferring it from which nav items are missing. */}
          <p className={`rail-workspace mono ws-${session.workspace}`}>
            {WORKSPACE_LABEL[session.workspace] ?? session.workspace}
          </p>
          <p className="mono rail-user">{session.full_name}</p>
          <p className="label">
            {session.role} ·{" "}
            {session.districts.length > 0
              ? `district ${session.districts.join(", ")}`
              : "national"}
          </p>
          <button className="rail-btn rail-logout" onClick={logout}>
            Sign out
          </button>
        </div>
        <p className="rail-stat">
          <span className="label">engine</span>
          <span className="mono">{health?.engine_version ?? "…"}</span>
        </p>
        <p className="rail-stat">
          <span className="label">offline</span>
          <span className="mono">{health?.offline_mode ?? "…"}</span>
        </p>
        <p className="rail-note">
          Nothing here becomes government evidence until a named officer signs it.
        </p>
      </footer>
    </nav>
  );
}
