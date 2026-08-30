/**
 * Submissions — the field workspace's landing screen.
 *
 * ## Why this exists rather than the register
 *
 * The claims register is framed by the epistemic ladder, which answers a
 * monitoring officer's question: how strongly is each claim known. A WDT or PIA
 * member recorded these works and has one question about them — *what happened
 * to what I sent?* Landing them on a ladder-ranked table asks them to translate
 * "N1_inconclusive · 0.41" into "still waiting", every time, for every row.
 *
 * So the same rows are framed by lifecycle: recorded → awaiting a decision →
 * signed, with the detectability gate called out because it is the one reason a
 * field member's work is assessed differently from how they submitted it.
 *
 * Built entirely from `GET /api/v1/claims`, which the server already narrows to
 * the caller's district — there is no client-side scoping here and there must
 * not be, because a filter in a browser is not an access control.
 *
 * ## Navigation
 *
 * A row opens `#/submissions/{claim_id}` — the lifecycle tracker for that one
 * record — and not `#/claim/{claim_id}`. The reconciliation view is one click
 * further on from there. This is the same reasoning as the screen itself: a
 * field member arriving from this table wants to know what is waiting on whom,
 * and the evidence tree does not say.
 */

import { useMemo } from "react";
import type { RegisterRow } from "../lib/api";
import { can } from "../lib/auth";

interface Props {
  rows: RegisterRow[];
  /** Accepted but unused: navigation here is a hash assignment, like every
   *  other route change in this app, so the row target is stated in one place
   *  instead of being decided by whatever the parent happened to pass. Optional
   *  so the prop can be dropped from the call site without breaking this
   *  file. */
  onOpen?: (claimId: number) => void;
}

/** Where a row goes. Not a prop: the target is a property of this screen. */
const openSubmission = (claimId: number) => {
  location.hash = `#/submissions/${String(claimId)}`;
};

/** The sentence a field member actually needs, derived from stored fields only.
 *
 * Two clauses at most: what the record is waiting on, and — separately — whether
 * the detectability gate changed how it was assessed. They are separate because
 * they are independent facts: a sub-pixel structure can be signed, and a
 * perfectly detectable one can be stuck awaiting a decision. Collapsing them
 * into one status word would lose whichever of the two the reader needed.
 */
function nextStep(row: RegisterRow): string {
  const parts: string[] = [];

  if (row.verdict_id === null) {
    parts.push("Recorded. No verdict has been computed against it yet.");
  } else if (row.status === "adjudicated") {
    parts.push("Signed by a monitoring officer. Nothing further is required.");
  } else if (row.status === "superseded") {
    parts.push(
      "A newer verdict version has replaced this one; the newer version is the live record.",
    );
  } else {
    parts.push("Assessed. Awaiting the monitoring officer's decision.");
  }

  // `passed` is the only value that means the gate cleared. Anything else —
  // including no recorded result — is treated as not cleared, because an
  // unrecorded gate is not a passed gate.
  if (row.detectability !== "passed") {
    parts.push(
      "The expected structure is smaller than one 30 m pixel, so it is assessed as part of a cluster rather than on its own.",
    );
  }

  return parts.join(" ");
}

export function Submissions({ rows }: Props) {
  const mayCapture = can("claim:create");

  const counts = useMemo(() => {
    let awaiting = 0;
    let signed = 0;
    let subPixel = 0;
    for (const r of rows) {
      if (r.provisional) awaiting += 1;
      if (r.status === "adjudicated") signed += 1;
      if (r.detectability !== "passed") subPixel += 1;
    }
    return { total: rows.length, awaiting, signed, subPixel };
  }, [rows]);

  const undated = rows.filter((r) => r.asserted_date === null).length;

  // Newest claimed date first: a field member is looking for what they entered
  // most recently. Rows with no claimed date sort last rather than being hidden,
  // and the count above says how many there are.
  const shown = useMemo(
    () =>
      [...rows].sort((a, b) =>
        (b.asserted_date ?? "").localeCompare(a.asserted_date ?? ""),
      ),
    [rows],
  );

  return (
    <div className="screen">
      <header className="screen-head rise">
        <div>
          <h1>Submissions</h1>
          <p className="sub">
            Works recorded in your district, and where each one has reached. Scoped
            to your jurisdiction by the server.
          </p>
        </div>
        {mayCapture && (
          <div className="head-actions">
            <button
              className="btn"
              onClick={() => {
                location.hash = "#/capture";
              }}
            >
              Record a work
            </button>
          </div>
        )}
      </header>

      <div className="panel strip-panel rise">
        <h2 className="label">Where these stand</h2>
        <dl className="strip">
          <div>
            <dt className="label">recorded</dt>
            <dd className="figure mono">{counts.total}</dd>
          </div>
          <div>
            <dt className="label">awaiting a decision</dt>
            <dd className="figure mono">{counts.awaiting}</dd>
          </div>
          <div>
            <dt className="label">signed</dt>
            <dd className="figure mono">{counts.signed}</dd>
          </div>
          <div>
            <dt className="label">below the 30 m limit</dt>
            <dd className="figure mono">{counts.subPixel}</dd>
          </div>
        </dl>
        <p className="note">
          “Awaiting a decision” counts every record whose verdict has not been
          signed, including ones with no verdict computed yet.{" "}
          <strong>“Below the 30 m limit”</strong> is the detectability gate: the
          expected structure is smaller than one satellite pixel, so per-structure
          imagery would prove nothing about it and it is assessed at cluster scale
          instead. A record with no gate result recorded is counted there too,
          because an unrecorded gate is not a cleared one.
          {undated > 0 && ` ${String(undated)} record${undated === 1 ? " carries" : "s carry"} no claimed date.`}
        </p>
      </div>

      <div className="table-wrap panel rise">
        <table className="register">
          <thead>
            <tr>
              <th>Unique ID</th>
              <th>Type</th>
              <th>Claimed date</th>
              <th className="num">GPS</th>
              <th>Detectability</th>
              <th>State</th>
              <th>What happens next</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr
                key={r.claim_id}
                onClick={() => openSubmission(r.claim_id)}
                onKeyDown={(e) => e.key === "Enter" && openSubmission(r.claim_id)}
                tabIndex={0}
                role="button"
                aria-label={`Open ${r.unique_id}`}
              >
                <td className="mono id">{r.unique_id}</td>
                <td>{r.intervention_type.replace(/_/g, " ")}</td>
                <td className="mono">{r.asserted_date ?? "not recorded"}</td>
                {/* Never a bare number: an accuracy figure without its unit and
                    its sign is not evidence of anything. */}
                <td className="num mono">
                  {r.uncertainty_m === null ? "—" : `±${r.uncertainty_m.toFixed(0)} m`}
                </td>
                <td className="mono">
                  {r.detectability === null ? "not recorded" : r.detectability}
                </td>
                <td>
                  {r.status === "adjudicated" ? (
                    <span className="mono state-signed">signed</span>
                  ) : (
                    <span className="chip ghost sm">provisional</span>
                  )}
                </td>
                <td className="reason-cell">{nextStep(r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && (
          <p className="empty">
            No works are recorded against your district in this database
            {mayCapture ? " — “Record a work” above is where the first one starts." : "."}
          </p>
        )}
      </div>

      <div className="panel strip-panel rise">
        <h2 className="label">What this account can and cannot do</h2>
        <p className="note">
          {mayCapture ? (
            <>
              <strong>Recording a work is the one write this account has.</strong>{" "}
              “Record a work” above opens the capture form: one photograph, one
              structure, one record. The server reads the frame's EXIF
              coordinate, measures its sharpness and exposure, and checks it
              against the images already stored before anything is written.
            </>
          ) : (
            <>
              <strong>This account cannot record works.</strong> Capture requires{" "}
              <code>claim:create</code>, which only WDT and PIA accounts hold.
            </>
          )}
        </p>
        <p className="note">
          Everything else here is read-only tracking. A field account can open
          any record and read the full evidence, the dissent and the rule path,
          but it does not hold <code>adjudication:create</code> and{" "}
          <strong>only a monitoring officer can sign a verdict</strong>. Nothing
          above becomes government evidence until one does, and the account that
          records a work is deliberately not the account that certifies it.
        </p>
      </div>
    </div>
  );
}
