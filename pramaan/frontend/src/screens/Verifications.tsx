/**
 * The verification queue (FR-10).
 *
 * `/api/v1/alerts` and `/api/v1/alerts/summary` have been live, tested and
 * jurisdiction-scoped since the alerts service landed, and until now had no
 * screen at all. This is that screen: the monitoring officer's inbox, ordered by
 * the engine's own priority rule.
 *
 * ## Why this is not the claims register with a filter
 *
 * The register asks "how strongly is each claim known" and is browsed. This asks
 * "which site do I send someone to first" and is worked top to bottom. The
 * ordering is therefore not the ladder: within a severity band the tiebreak
 * flips direction — confidence descending for adversarial findings, data
 * sufficiency ascending for the data-limited ones — and re-deriving that in the
 * browser would put a second ranking rule in the product, waiting to disagree
 * with the engine's. Every row here is ranked server-side and rendered in the
 * order received.
 *
 * ## What is deliberately absent
 *
 * No team assignment, no "deploy", no task lifecycle. There is no table and no
 * endpoint behind any of those, so a control for them would be a drawing of a
 * feature rather than a feature. The queue's output is a ranked list and a
 * sentence per row; the officer acts outside the system and signs inside it.
 */

import { useEffect, useState } from "react";
import type { Alert, AlertSummary, Level } from "../lib/api";
import { ApiError, fetchAlertSummary, fetchAlerts } from "../lib/api";

interface Props {
  onOpen: (claimId: number) => void;
}

/** Severity order, matching `SEVERITY` in `services/alerts/priority.py` exactly.
 *
 *  L2, L3 and L4 are absent because a corroborated verdict is not a
 *  low-priority alert — it is not an alert, and giving it priority 47 would
 *  invite an officer to work down to it. L0 and L1 *are* here: a claim that was
 *  merely recorded or merely observed has not been corroborated, and the queue
 *  ranks it below the adversarial findings rather than dropping it.
 *
 *  Written out rather than derived from the response keys so a band with zero
 *  alerts still gets a cell. An omitted band reads as a band that does not
 *  exist, which is the one thing the server is careful to avoid by sending its
 *  zeroes. */
const BANDS: Level[] = [
  "N3_contradicted",
  "N2_unsupported",
  "N1_inconclusive",
  "L0_recorded",
  "L1_observed",
];

const shortLevel = (level: string) => level.split("_")[0] ?? level;

/** The engine's rejection reason for a panel that could not load. Both call
 *  sites below need the identical unwrap, so the shape stays in one place. */
const reasonOf = (err: unknown) => (err instanceof ApiError ? err.detail : String(err));

export function Verifications({ onOpen }: Props) {
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Settled, not all: the summary and the queue are the same query on the
    // server, but a failure of one must not blank the other — an officer with a
    // working list and a missing header can still work.
    void Promise.allSettled([fetchAlerts(200), fetchAlertSummary()]).then(([q, s]) => {
      if (cancelled) return;
      if (q.status === "fulfilled") setAlerts(q.value);
      else setQueueError(reasonOf(q.reason));
      if (s.status === "fulfilled") setSummary(s.value);
      else setSummaryError(reasonOf(s.reason));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any level the server counted that this file does not know about. Rendered
  // rather than dropped: if the engine gains a ninth level, the queue header
  // must show it before someone notices it is missing.
  const extraBands =
    summary === null
      ? []
      : Object.keys(summary.by_level).filter((k) => !BANDS.includes(k as Level));

  return (
    <div className="screen">
      <header className="screen-head rise">
        <div>
          <h1>Verifications</h1>
          <p className="sub">
            Claims the engine could not corroborate, ranked by the priority rule
            it computed them with. Your jurisdiction only.
          </p>
        </div>
      </header>

      <div className="panel strip-panel rise">
        <h2 className="label">Queue</h2>
        {summary ? (
          <>
            <dl className="strip">
              <div>
                <dt className="label">queued</dt>
                <dd className="figure mono">{summary.total}</dd>
              </div>
              <div>
                <dt className="label">not yet signed</dt>
                <dd className="figure mono">{summary.unadjudicated}</dd>
              </div>
              {[...BANDS, ...extraBands].map((level) => (
                <div key={level}>
                  {/* Code and word both: a reader who cannot separate the
                      ladder's hues still gets the band from the text. */}
                  <dt>
                    <span className="chip sm" data-level={shortLevel(level)}>
                      {shortLevel(level)} {level.split("_").slice(1).join(" ")}
                    </span>
                  </dt>
                  <dd className="figure mono">{summary.by_level[level] ?? 0}</dd>
                </div>
              ))}
            </dl>
            {summary.total === 0 && (
              <p className="note">
                Nothing is queued. Every claim in your jurisdiction reached
                L2_corroborated or better, so the engine produced no queue entry
                and recorded no highest-priority reason — there is no entry to
                take one from.
              </p>
            )}
          </>
        ) : summaryError !== null ? (
          <p className="note">Queue counts could not be loaded: {summaryError}</p>
        ) : (
          <p className="loading">Loading queue counts…</p>
        )}
      </div>

      <p className="triage-note note">
        Order is the engine's <strong>priority rule</strong>, not the epistemic
        ladder: severity band first, then the tiebreak that band deserves —
        confidence descending for a contradicted or unsupported finding, because
        the case the engine is surest about is the one that survives a challenge,
        and data sufficiency ascending for the rest, where the scarcest evidence
        is the most urgent. A corroborated verdict never appears here at all; it
        is not a low-priority alert, it is not an alert.{" "}
        <strong>
          The strongest recommendation this system can make is physical
          verification
        </strong>{" "}
        — it never concludes anything about a person. A signed entry stays in the
        list, because a decision is part of the record, not a way to clear it.
      </p>

      <div className="table-wrap panel rise">
        {alerts === null ? (
          queueError !== null ? (
            <p className="empty">The queue could not be loaded: {queueError}</p>
          ) : (
            <p className="loading">Loading verification queue…</p>
          )
        ) : (
          <table className="register">
            <thead>
              <tr>
                <th className="num">Pri.</th>
                <th>Unique ID</th>
                <th>Type</th>
                <th>Level</th>
                <th className="num">Conf.</th>
                <th className="num">Suff.</th>
                <th>Decision</th>
                <th>Recommended action</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr
                  key={a.verdict_id}
                  onClick={() => onOpen(a.claim_id)}
                  onKeyDown={(e) => e.key === "Enter" && onOpen(a.claim_id)}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open ${a.unique_id}`}
                >
                  <td className="num mono">{a.priority}</td>
                  <td className="mono id">{a.unique_id}</td>
                  <td>{a.intervention_type.replace(/_/g, " ")}</td>
                  <td>
                    <span className="chip" data-level={shortLevel(a.level)}>
                      {shortLevel(a.level)} {a.label}
                    </span>
                  </td>
                  <td className="num mono">{a.confidence.toFixed(3)}</td>
                  <td className="num mono">{a.data_sufficiency.toFixed(2)}</td>
                  <td>
                    {a.adjudicated ? (
                      <span className="mono state-signed">signed</span>
                    ) : (
                      <span className="chip ghost sm">provisional</span>
                    )}
                  </td>
                  <td className="mono">
                    {a.recommended_action.replace(/_/g, " ")}
                  </td>
                  {/* Never truncated. This is the sentence that justifies
                      sending a person to a site; an ellipsis here would hide
                      the only part of the row that is an argument. */}
                  <td className="reason-cell">{a.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {alerts !== null && alerts.length === 0 && (
          <p className="empty">No claims in your jurisdiction need verification.</p>
        )}
      </div>

      <p className="foot-note rise">
        Opening a row opens its reconciliation record, where the evidence tree,
        the dissent and the rule path are shown before any decision is taken.
      </p>
    </div>
  );
}
