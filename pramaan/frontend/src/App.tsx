/**
 * Console shell and routing.
 *
 * Hash routing, no router dependency. Three routes is not a routing problem, and
 * `react-router` would be 20 kB to solve one it does not have.
 */

import { useCallback, useEffect, useState } from "react";
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
import { Detail } from "./screens/Detail";
import { PlanMap } from "./screens/PlanMap";
import { Register } from "./screens/Register";

type Route =
  | { name: "register" }
  | { name: "claim"; id: number }
  | { name: "temporal"; id: number }
  | { name: "map"; id: number };

function parseHash(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [screen, raw] = h.split("/");
  const id = Number(raw);
  if (screen === "claim" && Number.isFinite(id)) return { name: "claim", id };
  if (screen === "temporal" && Number.isFinite(id)) return { name: "temporal", id };
  if (screen === "map" && Number.isFinite(id)) return { name: "map", id };
  return { name: "register" };
}

export function App() {
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

  const id = route.name === "register" ? null : route.id;

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
      <Rail route={route} onMethod={() => setMethod(true)} />

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
          ) : (
            <Loading what="claim" />
          ))}

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

function Rail({ route, onMethod }: { route: Route; onMethod: () => void }) {
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

  return (
    <nav className="rail" aria-label="Main">
      {/* Institutional attribution above the wordmark. This product is an
          instrument of a programme, not a brand, and the header should say
          whose programme before it says whose software. The build state is on
          the same line on purpose: a prototype that presents itself as a
          deployed system is the dishonesty this whole console is against. */}
      <div className="rail-gov">
        <span className="gov-full">Government of India</span>
        <span className="gov-full">Ministry of Rural Development · DoLR</span>
        <span className="gov-full">WDC-PMKSY 2.0</span>
        {/* Two explicit spans rather than a CSS content swap: the caveat has to
            survive the icon-only rail, because its entire purpose is that a
            screenshot of any screen at any width carries it. Swapping text via
            `content` would hide it from assistive technology. */}
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
          <a
            className={on("claim")}
            href={route.name === "register" ? "#/claim/1" : `#/claim/${route.id}`}
          >
            Reconciliation
          </a>
        </li>
        <li>
          <a
            className={on("map")}
            href={route.name === "register" ? "#/map/1" : `#/map/${String(route.id)}`}
          >
            Plan view
          </a>
        </li>
        <li>
          <a
            className={on("temporal")}
            href={route.name === "register" ? "#/temporal/1" : `#/temporal/${route.id}`}
          >
            Temporal analysis
          </a>
        </li>
        <li>
          <button className="rail-btn" onClick={onMethod}>
            Method
          </button>
        </li>
      </ul>

      <footer className="rail-foot">
        <p className="label">engine</p>
        <p className="mono">{health?.engine_version ?? "…"}</p>
        <p className="label">offline mode</p>
        <p className="mono">{health?.offline_mode ?? "…"}</p>
        <p className="rail-note">
          Nothing here becomes government evidence until a named officer signs it.
        </p>
      </footer>
    </nav>
  );
}
