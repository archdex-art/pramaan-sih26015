/**
 * S7 shell: the verdict header and the temporal chart.
 *
 * The level chip is rendered *before* confidence, per docs §24.4: level says how
 * strongly a thing is known, confidence says how much of that level's evidence
 * agreed. Showing confidence first invites reading 0.16 as "16% likely true",
 * which is not what it means.
 */

import { useEffect, useState } from "react";
import { TemporalControlChart } from "./components/charts/TemporalControlChart";
import {
  ApiError,
  fetchTemporal,
  fetchVerdict,
  type TemporalComparison,
  type Verdict,
} from "./lib/api";

const CLAIM_ID = Number(new URLSearchParams(location.search).get("claim") ?? 1);

export function App() {
  const [temporal, setTemporal] = useState<TemporalComparison | null>(null);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Settled, not all: a missing verdict must not blank the chart, and a
    // missing chart must not hide the verdict. Each half fails on its own.
    void Promise.allSettled([fetchTemporal(CLAIM_ID), fetchVerdict(CLAIM_ID)]).then(
      ([t, v]) => {
        if (cancelled) return;
        if (t.status === "fulfilled") setTemporal(t.value);
        else
          setError(
            t.reason instanceof ApiError ? t.reason.detail : String(t.reason),
          );
        if (v.status === "fulfilled") setVerdict(v.value);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <header className="masthead">
        <h1>
          PRAMAAN <span className="sub">Temporal Analysis</span>
        </h1>
        {temporal && (
          <p className="claim-id">
            {temporal.intervention_unique_id} · {temporal.intervention_type} ·
            claimed complete {temporal.claimed_date}
          </p>
        )}
      </header>

      {verdict && <VerdictHeader verdict={verdict} />}

      {error !== null && (
        <section className="error">
          <strong>The temporal chart could not be drawn.</strong>
          <p>{error}</p>
        </section>
      )}

      {temporal && <TemporalControlChart data={temporal} />}

      {!temporal && error === null && <p className="loading">Loading…</p>}
    </main>
  );
}

function VerdictHeader({ verdict }: { verdict: Verdict }) {
  return (
    <section className="verdict">
      <div className="verdict-row">
        {/* Level first, per docs §24.4. */}
        <span className={`chip level-${verdict.level.slice(0, 2).toLowerCase()}`}>
          {verdict.level.replace(/_/g, " ")}
        </span>
        <span className="chip label">{verdict.label}</span>
        {verdict.provisional && <span className="chip provisional">PROVISIONAL</span>}
      </div>
      <dl className="metrics">
        <div>
          <dt>confidence</dt>
          <dd>{verdict.confidence.toFixed(4)}</dd>
        </div>
        <div>
          <dt>coverage</dt>
          <dd>{verdict.coverage.toFixed(2)}</dd>
        </div>
        <div>
          <dt>data sufficiency</dt>
          <dd>{verdict.data_sufficiency.toFixed(2)}</dd>
        </div>
        <div>
          <dt>engine</dt>
          <dd>{verdict.engine_version}</dd>
        </div>
      </dl>
      {/* Always expanded. A verdict without visible counter-evidence is not
          shippable (docs §16.2 STEP 11), so this is not collapsible. */}
      <div className="dissent">
        <h2>Dissent</h2>
        <ul>
          {verdict.dissent.map((d) => (
            <li key={d}>{d}</li>
          ))}
        </ul>
      </div>
      <p className="rule-path">
        rule path: <code>{verdict.rule_path.join(" → ")}</code>
      </p>
    </section>
  );
}
