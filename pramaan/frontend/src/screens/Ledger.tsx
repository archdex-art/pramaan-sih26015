/**
 * Ledger screen — the chain integrity view.
 *
 * Shows every adjudication in chain order with the verification result.
 * Available to any role with `ledger:verify` capability.
 */

import { useEffect, useState } from "react";
import type { ChainReport, LedgerEntry } from "../lib/api";
import { ApiError, fetchChainReport, fetchLedger } from "../lib/api";

/** ISO timestamp to a short readable stamp.
 *
 *  The raw `decided_at` was printed verbatim, which is a 32-character
 *  `2026-08-28T23:52:23.184471+00:00` in a table cell. The microseconds are
 *  stored and hashed and belong in the Evidence Pack, not in a column an
 *  officer scans. Rendered in the browser's locale rather than forced to IST:
 *  the column is `timestamptz`, so the instant is unambiguous either way, and
 *  pinning a zone here would mislead a reader in another one. */
function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

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
    // `.screen` + `.screen-head`, the shape every other screen uses. This one
    // was a bare `<section className="ledger">` with its own header block, so it
    // sat at a different width with different heading spacing and read as a
    // different application. The badge stays in the head because chain validity
    // is the screen's headline, not a detail below it.
    <div className="screen">
      <header className="screen-head rise">
        <div>
          <h1>Adjudication ledger</h1>
          <p className="sub">
            Every signature in chain order. Each row is{" "}
            <span className="mono">sha256</span> over its own content plus the
            previous row&rsquo;s hash.
          </p>
        </div>
        <span
          className={`ledger-badge mono ${report.valid ? "ledger-valid" : "ledger-broken"}`}
        >
          {report.valid
            ? `✓ VALID — ${report.rows} row${report.rows === 1 ? "" : "s"}`
            : `✗ BROKEN at row ${report.broken_at}: ${report.reason}`}
        </span>
      </header>

      <p className="triage-note note">{report.statement}</p>

      {entries.length === 0 ? (
        <p className="empty">
          No adjudications recorded yet. Nothing here becomes government evidence
          until a named officer signs it.
        </p>
      ) : (
        // `.table-wrap` is the same scroll container every other table here
        // uses. Without it the table sat in a section with `overflow-x: visible`,
        // so the widest column pushed the whole document 145px past the viewport
        // and the page scrolled sideways. A table may scroll inside its own box;
        // the page may not.
        <div className="table-wrap panel">
          <table className="register ledger-table">
          <thead>
            <tr>
              {/* Chain position, not a database id: it is what "row 6" means in
                  the verifier's output, so an auditor comparing the two needs
                  it. */}
              <th>#</th>
              <th>Structure</th>
              <th>Decision</th>
              {/* The officer's stated basis. This column did not exist, which
                  meant the one irreplaceably human thing in the ledger — why a
                  person rejected a claim — was stored, hash-protected, and
                  never shown. It is the reason the table is worth reading. */}
              <th>Stated reason</th>
              <th>Signed by</th>
              <th>At</th>
              <th>Chain</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td className="mono">{e.id}</td>
                {/* The structure, not the verdict's serial number. `v108` is the
                    database's name for an assessment; the programme id is the
                    officer's name for the thing on the ground. */}
                <td className="mono">{e.structure ?? `verdict ${e.verdict_id}`}</td>
                <td className={`adj-decision-${e.decision}`}>
                  {e.decision}
                  {/* Folded in rather than given its own column: a corrected
                      level exists only for `edit`, so as a column it was empty
                      on five rows out of seven and read as missing data. */}
                  {e.corrected_level !== null && (
                    <>
                      {" → "}
                      <span className="mono">{e.corrected_level}</span>
                    </>
                  )}
                </td>
                <td className="sub">
                  {/* An accept requires no reason: it adds no assertion beyond
                      the engine's own. Drawing a dash says that, where blank
                      would read as an officer who declined to explain. */}
                  {e.reason ?? "—"}
                </td>
                <td>
                  {e.signed_by_name}
                  <br />
                  <span className="mono sub">{e.signed_by_username}</span>
                </td>
                <td className="mono">{when(e.decided_at)}</td>
                {/* Twelve characters, with the full digest in `title` and in the
                    Evidence Pack. The chain's value is that it verifies, and the
                    badge above reports that; 64 hex characters per row proves
                    nothing extra to a human eye and pushes the reason — the part
                    a person can actually judge — off the line. */}
                <td className="mono adj-hash" title={`row_hash ${e.row_hash}`}>
                  {e.row_hash.slice(0, 12)}…
                </td>
              </tr>
            ))}
          </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
