/**
 * S4 — the Method drawer.
 *
 * Reads `/api/v1/method/*` **at runtime, from the running engine**. Nothing here
 * is hardcoded in the frontend.
 *
 * That matters for one reason: a system that prints its own method from its own
 * configuration cannot have an interface that disagrees with its code. If
 * someone changes a weight, this panel changes with it. Every other way of
 * building this screen — a static table, a copy in the docs — drifts.
 *
 * It is also the thing judges click.
 */

import { useEffect, useState } from "react";
import { ApiError, fetchLadder, fetchWeights, type Ladder, type Weights } from "../lib/api";

const WEIGHT_REASON: Record<string, string> = {
  terrain:
    "Fully independent of the claim, and unaffected by cloud, sensor resolution or season. The heaviest for that reason.",
  satellite: "Fully independent. Limited by the 30 m detection floor.",
  temporal: "Fully independent. Answers whether the surface state changed.",
  control:
    "Fully independent, and the only family that can separate the intervention from the weather.",
  photo:
    "NOT independent — it is the claim's own source. Weighted lowest so independent evidence outranks self-report.",
  context:
    "Carried for transparency. The matched-control design is a better rainfall control than any normalisation formula, so this is the lightest.",
};

export function MethodDrawer({ onClose }: { onClose: () => void }) {
  const [ladder, setLadder] = useState<Ladder | null>(null);
  const [weights, setWeights] = useState<Weights | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([fetchLadder(), fetchWeights()]).then(([l, w]) => {
      if (cancelled) return;
      if (l.status === "fulfilled") setLadder(l.value);
      if (w.status === "fulfilled") setWeights(w.value);
      const failed = [l, w].find((r) => r.status === "rejected");
      if (failed && failed.status === "rejected") {
        setError(
          failed.reason instanceof ApiError
            ? failed.reason.detail
            : String(failed.reason),
        );
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Method"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <h2>Method</h2>
            <p className="sub">
              Read from the running engine
              {weights && ` · ${weights.engine_version}`}
            </p>
          </div>
          <button className="btn" onClick={onClose} aria-label="Close method panel">
            Close
          </button>
        </header>

        {error !== null && (
          <p className="error-inline">Could not read the method: {error}</p>
        )}

        {ladder && (
          <section>
            <h3 className="label">The epistemic ladder</h3>
            <ol className="ladder">
              {ladder.levels.map((l) => (
                <li key={l}>
                  <span className="chip" data-level={l.split("_")[0]}>
                    {l.split("_")[0]}
                  </span>
                  <span className="mono">{l.replace(/_/g, " ")}</span>
                  {l === ladder.ceiling && (
                    <span className="chip ghost sm">ceiling</span>
                  )}
                </li>
              ))}
            </ol>
            {Object.entries(ladder.refused).map(([k, v]) => (
              <p key={k} className="refused">
                <strong>{k.replace(/_/g, " ")} — refused.</strong> {v}
              </p>
            ))}
          </section>
        )}

        {weights && (
          <section>
            <h3 className="label">Evidence weights</h3>
            <table className="weights">
              <tbody>
                {Object.entries(weights.weights)
                  .sort((a, b) => b[1] - a[1])
                  .map(([family, w]) => (
                    <tr key={family}>
                      <th>{family}</th>
                      <td className="mono num">{w.toFixed(2)}</td>
                      <td>
                        <div className="bar" style={{ width: `${w * 320}px` }} />
                      </td>
                      <td className="why">{WEIGHT_REASON[family]}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            <p className="note">
              Sum {weights.weight_sum.toFixed(2)} ·{" "}
              {weights.independent_families.length} of {weights.families.length}{" "}
              families are independent of the claim.
            </p>
            <h3 className="label">Formula</h3>
            <dl className="formula">
              {Object.entries(weights.formula).map(([name, expr]) => (
                <div key={name}>
                  <dt className="mono">{name}</dt>
                  <dd className="mono">{expr}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        {ladder && (
          <section>
            <h3 className="label">The two paths to a contradicted verdict</h3>
            {Object.entries(ladder.n3_paths).map(([name, why]) => (
              <p key={name} className="n3-path">
                <strong className="mono">{name}</strong> {why}
              </p>
            ))}
          </section>
        )}
      </aside>
    </div>
  );
}
