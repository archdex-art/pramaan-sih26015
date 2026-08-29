/**
 * Adjudication panel — the signature box.
 *
 * This is the component where the word PROVISIONAL stops appearing. It is
 * deliberately heavy with confirmation and explanation because signing a
 * verdict is the one irreversible action in the system. The hash chain link
 * is displayed after signing so the officer knows exactly what was committed.
 */

import { type FormEvent, useState } from "react";
import type {
  AdjudicationResult,
  Decision,
  Level,
  Verdict,
} from "../lib/api";
import { adjudicate, ApiError } from "../lib/api";
import { can, getSession } from "../lib/auth";

const LEVELS: Level[] = [
  "L0_recorded",
  "L1_observed",
  "L2_corroborated",
  "L3_multi_indicator",
  "L4_control_differenced",
  "N1_inconclusive",
  "N2_unsupported",
  "N3_contradicted",
];

export function AdjudicationPanel({
  verdict,
  onSigned,
}: {
  verdict: Verdict;
  onSigned: () => void;
}) {
  const session = getSession();
  const maySign = can("adjudication:create");
  const [decision, setDecision] = useState<Decision>("accept");
  const [level, setLevel] = useState<Level>(verdict.level as Level);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AdjudicationResult | null>(null);
  const [loading, setLoading] = useState(false);

  if (!maySign) {
    return (
      <div className="adj-panel adj-readonly">
        <h3 className="adj-title">Adjudication</h3>
        <p className="adj-note">
          Your role ({session?.role ?? "unknown"}) does not hold the{" "}
          <code>adjudication:create</code> capability. Adjudication requires a
          WCDC or SLNA officer.
        </p>
      </div>
    );
  }

  if (!verdict.provisional) {
    return (
      <div className="adj-panel adj-signed">
        <h3 className="adj-title">Adjudicated</h3>
        <p className="adj-note">
          This verdict has been signed and is no longer provisional.
        </p>
      </div>
    );
  }

  if (result) {
    return (
      <div className="adj-panel adj-result">
        <h3 className="adj-title">Signed</h3>
        <dl className="adj-receipt">
          <dt>Decision</dt>
          <dd>{result.decision}</dd>
          <dt>Signed by</dt>
          <dd>
            {result.signed_by_name} ({result.signed_by_username})
          </dd>
          <dt>At</dt>
          <dd className="mono">{result.decided_at}</dd>
          {result.corrected_level && (
            <>
              <dt>Corrected level</dt>
              <dd className="mono">{result.corrected_level}</dd>
            </>
          )}
          {result.reason && (
            <>
              <dt>Reason</dt>
              <dd>{result.reason}</dd>
            </>
          )}
          <dt>Row hash</dt>
          <dd className="mono adj-hash">{result.row_hash}</dd>
          <dt>Previous hash</dt>
          <dd className="mono adj-hash">{result.prev_hash}</dd>
        </dl>
      </div>
    );
  }

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setLoading(true);
    setError(null);

    const body: { decision: Decision; corrected_level?: string; reason?: string } = {
      decision,
    };
    if (decision === "edit") body.corrected_level = level;
    if (decision !== "accept") body.reason = reason;

    void adjudicate(verdict.id, body)
      .then((r) => {
        setResult(r);
        onSigned();
      })
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.detail : String(err));
      })
      .finally(() => setLoading(false));
  };

  const needsReason = decision !== "accept";
  const needsLevel = decision === "edit";

  return (
    <div className="adj-panel">
      <h3 className="adj-title">Adjudicate this verdict</h3>
      <p className="adj-note">
        Signing is irreversible. The decision, your identity, and a timestamp
        are hashed into an append-only chain that the system cannot alter after
        the fact.
      </p>

      <form className="adj-form" onSubmit={submit}>
        <fieldset className="adj-decisions">
          <legend className="label">Decision</legend>
          {(["accept", "edit", "reject"] as const).map((d) => (
            <label key={d} className="adj-radio">
              <input
                type="radio"
                name="decision"
                value={d}
                checked={decision === d}
                onChange={() => setDecision(d)}
              />
              <span className="adj-radio-label">
                {d === "accept"
                  ? "Accept — level and evidence are correct"
                  : d === "edit"
                    ? "Edit — correct the epistemic level"
                    : "Reject — evidence does not support the claim"}
              </span>
            </label>
          ))}
        </fieldset>

        {needsLevel && (
          <label className="adj-field">
            <span className="login-label">Corrected level</span>
            <select
              value={level}
              onChange={(e) => setLevel(e.target.value as Level)}
            >
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        )}

        {needsReason && (
          <label className="adj-field">
            <span className="login-label">Reason (required)</span>
            <textarea
              rows={3}
              maxLength={4000}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={
                decision === "reject"
                  ? "Why does the evidence not support the claim?"
                  : "Why is the level being changed?"
              }
            />
          </label>
        )}

        {error && <p className="login-error">{error}</p>}

        <button
          type="submit"
          className="adj-submit"
          disabled={loading || (needsReason && !reason.trim())}
        >
          {loading ? "Signing…" : `Sign: ${decision}`}
        </button>
      </form>
    </div>
  );
}
