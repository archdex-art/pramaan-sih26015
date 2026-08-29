/**
 * Ledger screen — the chain integrity view.
 *
 * Shows every adjudication in chain order with the verification result.
 * Available to any role with `ledger:verify` capability.
 */

import { useEffect, useState } from "react";
import type { ChainReport, LedgerEntry } from "../lib/api";
import { ApiError, fetchChainReport, fetchLedger } from "../lib/api";

export function Ledger() {
  const [entries, setEntries] = useState<LedgerEntry[] | null>(null);
  const [report, setReport] = useState<ChainReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([fetchLedger(), fetchChainReport()])
      .then(([e, r]) => {
        if (cancelled) return;
        setEntries(e);
        setReport(r);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.detail : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="error-inline">Ledger: {error}</p>;
  if (!entries || !report) return <p className="loading">Loading ledger…</p>;

  return (
    <section className="ledger">
      <header className="ledger-header">
        <h2>Adjudication ledger</h2>
        <span
          className={`ledger-badge mono ${report.valid ? "ledger-valid" : "ledger-broken"}`}
        >
          {report.valid
            ? `✓ VALID — ${report.rows} row${report.rows === 1 ? "" : "s"}`
            : `✗ BROKEN at row ${report.broken_at}: ${report.reason}`}
        </span>
      </header>

      <p className="ledger-statement">{report.statement}</p>

      {entries.length === 0 ? (
        <p className="adj-note">No adjudications recorded yet.</p>
      ) : (
        <table className="register ledger-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Verdict</th>
              <th>Decision</th>
              <th>Corrected level</th>
              <th>Signed by</th>
              <th>At</th>
              <th>Row hash</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td className="mono">{e.id}</td>
                <td className="mono">{e.verdict_id}</td>
                <td className={`adj-decision-${e.decision}`}>{e.decision}</td>
                <td className="mono">{e.corrected_level ?? "—"}</td>
                <td>
                  {e.signed_by_name}{" "}
                  <span className="mono label">({e.signed_by_username})</span>
                </td>
                <td className="mono">{e.decided_at}</td>
                <td className="mono adj-hash">{e.row_hash.slice(0, 16)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
