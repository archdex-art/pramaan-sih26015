/**
 * S2 — reconciliation detail. The daily-use screen (docs §24 S9).
 *
 * Three columns: the claim, the verdict, and the dissent. The ordering is the
 * argument — an officer should meet the evidence before the conclusion, and the
 * bad news must never be below the fold.
 *
 * Two rules this screen exists to enforce:
 *
 *   **Level before confidence** (§24.4). Level says how strongly a thing is
 *   known; confidence says how much of that level's evidence agreed. Rendering
 *   confidence first invites reading 0.06 as "6 % likely true".
 *
 *   **The dissent panel is never collapsible.** A verdict without visible
 *   counter-evidence is not shippable (§16.2 STEP 11), and a collapsed panel is
 *   an invisible one.
 */

import { useState } from "react";
import { ConfidenceRing } from "../components/ConfidenceRing";
import { UncertaintyDisk } from "../components/UncertaintyDisk";
import type { EvidenceEntry, EvidenceTree, RegisterRow, Verdict } from "../lib/api";

interface Props {
  claim: RegisterRow;
  verdict: Verdict | null;
  evidence: EvidenceTree | null;
  onMethod: () => void;
  onTemporal: () => void;
}

const GLYPH: Record<string, string> = {
  agrees: "▲",
  neutral: "▬",
  disagrees: "▼",
  unavailable: "○",
};

const FAMILY_NOTE: Record<string, string> = {
  terrain: "Is this site hydrologically capable of hosting this structure?",
  satellite: "Is the observed surface state consistent with the expectation?",
  temporal: "Did the surface change, same season, year on year?",
  control: "Did it change more than comparable un-intervened land?",
  context: "Can rainfall account for the change?",
  photo: "What the field sent. Weighted lowest — it is the claim's own source.",
};

export function Detail({ claim, verdict, evidence, onMethod, onTemporal }: Props) {
  const level = verdict?.level ?? claim.level ?? null;
  const short = level ? (level.split("_")[0] ?? level) : null;

  return (
    <div className="screen detail">
      <header className="screen-head rise">
        <div>
          <h1>{claim.intervention_type.replace(/_/g, " ")}</h1>
          <p className="sub mono">
            {claim.unique_id} · district {claim.district_lgd} · claimed{" "}
            {claim.asserted_date ?? "—"}
          </p>
        </div>
        <div className="head-actions">
          <span className="badge" data-prov={claim.provenance}>
            {claim.provenance}
          </span>
          {claim.provisional && <span className="chip ghost">provisional</span>}
          <button className="btn" onClick={onTemporal}>
            Temporal analysis
          </button>
          <button className="btn" onClick={onMethod}>
            Method
          </button>
        </div>
      </header>

      <div className="cols">
        {/* ---- left: the claim ---------------------------------------- */}
        <section className="panel col rise">
          <h2 className="label">The claim</h2>
          <dl className="kv">
            <dt>Coordinate</dt>
            <dd className="mono">
              {claim.lat.toFixed(5)}°N {claim.lon.toFixed(5)}°E
            </dd>
            <dt>GPS accuracy</dt>
            <dd className="mono">
              {claim.uncertainty_m === null ? "—" : `${claim.uncertainty_m} m`}
            </dd>
            <dt>Detectability</dt>
            <dd className="mono">{claim.detectability ?? "—"}</dd>
          </dl>

          <UncertaintyDisk
            accuracyM={claim.uncertainty_m}
            footprintM2={claim.expected_footprint_m2}
          />

          <p className="note">
            Every terrain variable is read as a distribution over this disk, never
            from the single pixel at its centre. On the measured claim, flow
            accumulation varied <strong>1 to 216 pixels</strong> across 15 m.
          </p>
        </section>

        {/* ---- centre: the verdict ------------------------------------ */}
        <section className="panel col centre rise">
          <h2 className="label">Verdict</h2>

          {/* Level first, deliberately. */}
          <div className="verdict-head">
            <div>
              {short && (
                <span className="chip lg" data-level={short}>
                  {short} · {verdict?.label ?? claim.label}
                </span>
              )}
              {/* The chip already says "N1 · INCONCLUSIVE"; repeating it as
                  "n1 inconclusive" was noise. Show the machine-readable level
                  instead, which is what appears in the stored row. */}
              <p className="verdict-level mono">level {level}</p>
            </div>
            <ConfidenceRing
              confidence={verdict?.confidence ?? claim.confidence ?? 0}
              level={short}
            />
          </div>

          <dl className="metrics">
            <Metric label="score" value={verdict?.score ?? claim.score} digits={4} />
            <Metric label="coverage" value={verdict?.coverage ?? claim.coverage} />
            <Metric label="quality" value={verdict?.quality ?? null} />
            <Metric
              label="sufficiency"
              value={verdict?.data_sufficiency ?? claim.data_sufficiency}
            />
          </dl>

          <h3 className="label">Evidence</h3>
          <ul className="tree">
            {evidence?.entries.map((e) => (
              <FamilyRow key={e.family} entry={e} />
            ))}
          </ul>
          {evidence && (
            <p className="note">
              {evidence.families_available} of {evidence.families_total} families
              available. An unavailable family lowers coverage — it is not read as
              a neutral reading.
            </p>
          )}

          <h3 className="label">Rule path</h3>
          <p className="rule-path mono">
            {(verdict?.rule_path ?? claim.rule_path).join("  →  ") || "—"}
          </p>
        </section>

        {/* ---- right: dissent and action ------------------------------ */}
        <section className="col rise">
          {/* Always expanded. Not a <details>. */}
          <div className="panel dissent">
            <h2 className="label">Dissent — always shown</h2>
            {verdict && verdict.dissent.length > 0 ? (
              <ul>
                {verdict.dissent.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            ) : (
              <p className="note">
                {verdict
                  ? "No dissent recorded — which would itself be a defect."
                  : `${claim.dissent_count} entries recorded.`}
              </p>
            )}
          </div>

          <div className="panel action">
            <h2 className="label">Recommended action</h2>
            <p className="action-name mono">
              {(verdict?.recommended_action.action ?? "—").replace(/_/g, " ")}
              {verdict?.recommended_action.priority != null &&
                ` · priority ${verdict.recommended_action.priority}`}
            </p>
            <p className="note">
              The strongest phrase this system can emit is “requires physical
              verification”. It is enforced by a linter, not a style guide.
            </p>
          </div>

          <div className="panel adjudication">
            <h2 className="label">Adjudication</h2>
            <div className="btn-row">
              <button className="btn primary" disabled>
                Accept
              </button>
              <button className="btn" disabled>
                Edit
              </button>
              <button className="btn danger" disabled>
                Reject
              </button>
            </div>
            {/* A disabled control says why. No dead buttons. */}
            <p className="note">
              <strong>Not yet wired.</strong> The append-only, hash-chained
              <code> adjudications</code> table exists and the database already
              refuses UPDATE and DELETE to the application role — but the signing
              endpoint is Stage 5. Until then every verdict stays{" "}
              <strong>PROVISIONAL</strong>, which is the correct state.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  digits = 2,
}: {
  label: string;
  value: number | null | undefined;
  digits?: number;
}) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd className="mono figure">
        {value === null || value === undefined ? "—" : value.toFixed(digits)}
      </dd>
    </div>
  );
}

function FamilyRow({ entry }: { entry: EvidenceEntry }) {
  const [open, setOpen] = useState(false);
  const lineageKeys = Object.keys(entry.lineage);

  return (
    <li className="fam" data-dir={entry.direction}>
      <button
        className="fam-head"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="fam-glyph" aria-hidden="true">
          {GLYPH[entry.direction]}
        </span>
        <span className="fam-name">{entry.family}</span>
        <span className="fam-value mono">
          {entry.agreement === null ? "unavailable" : entry.agreement.toFixed(3)}
        </span>
        {entry.cluster_scale && <span className="chip ghost sm">cluster</span>}
        <span className="fam-caret" aria-hidden="true">
          {open ? "–" : "+"}
        </span>
      </button>
      {open && (
        <div className="fam-body">
          <p className="fam-question">{FAMILY_NOTE[entry.family]}</p>
          <p className="fam-reason">{entry.reason || "no reason recorded"}</p>
          {lineageKeys.length > 0 && (
            <p className="label">
              lineage: {lineageKeys.slice(0, 8).join(" · ")}
              {lineageKeys.length > 8 && ` · +${lineageKeys.length - 8} more`}
            </p>
          )}
        </div>
      )}
    </li>
  );
}
