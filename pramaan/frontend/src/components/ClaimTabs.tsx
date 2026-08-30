/**
 * Navigation *within* one claim.
 *
 * ## The bug this fixes
 *
 * Reconciliation, plan view and temporal analysis were entries in the global
 * rail, built as `#/claim/${currentId}` where
 * `currentId = "id" in route ? route.id : 1`. From any screen without a claim in
 * the route — the register, the queue, analytics — those links pointed at
 * **claim 1**. So the rail offered "Plan view" as though it were a global
 * screen, and clicking it showed one arbitrary claim's map. There is no such
 * thing as the plan view; there is only *this structure's* plan view, and a nav
 * entry that implies otherwise is asking "whose reconciliation is this?" and
 * answering it wrongly.
 *
 * These three screens are therefore not global navigation at all. They are
 * views of a single record, and they belong to that record's own header.
 *
 * ## Why a tab strip and not breadcrumbs
 *
 * All four views answer questions about the same structure and an officer moves
 * between them repeatedly while forming one judgement — evidence, then where it
 * sits on the ground, then whether the change outlasts a season. That is lateral
 * movement across peers, which is what tabs mean. Breadcrumbs would imply a
 * hierarchy that does not exist between them.
 *
 * The claim's own identity sits above the tabs, in mono, because the first thing
 * a reader must be able to establish is *which* structure they are looking at.
 * That was the missing information: the old rail never showed it.
 */

import { useState } from "react";
import { authFetch, can } from "../lib/auth";

/** The per-claim views. `href` is a hash route; `report` is fetched, not linked
 *  — see `openReport`. */
interface Tab {
  name: string;
  href: string;
  text: string;
}

interface Props {
  claimId: number;
  /** The structure's programme identifier, when the caller already has it.
   *  Falls back to the numeric claim id, which is honest but less useful: a
   *  fabricated placeholder would be worse than a plain number. */
  uniqueId?: string | null;
  interventionType?: string | null;
  /** Route name currently rendered, so the active tab is marked from the router
   *  rather than from a second source of truth. */
  active: string;
  workspace: string;
}

export function ClaimTabs({ claimId, uniqueId, interventionType, active, workspace }: Props) {
  const [reportError, setReportError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const tabs: Tab[] = [];

  // The field workspace enters a record through its lifecycle tracker, so that
  // is their first tab. A monitoring officer has no use for it: they are not
  // waiting on their own submission, they are the thing it waits on.
  if (workspace === "field") {
    tabs.push({ name: "submission", href: `#/submission/${claimId}`, text: "Tracking" });
  }

  tabs.push(
    { name: "claim", href: `#/claim/${claimId}`, text: "Reconciliation" },
    { name: "map", href: `#/map/${claimId}`, text: "Plan view" },
    { name: "temporal", href: `#/temporal/${claimId}`, text: "Temporal analysis" },
  );

  /** Open the Evidence Pack.
   *
   *  Fetched and opened as a blob rather than linked with an `<a href>`: the
   *  report endpoint is capability-gated and the access token travels in an
   *  Authorization header, not a cookie, so a plain link opens a new tab with no
   *  credentials and gets a 401. A link that reliably fails is worse than a
   *  button, because the reader blames the report rather than the link. */
  async function openReport(): Promise<void> {
    setReportError(null);
    setLoading(true);
    try {
      const response = await authFetch(`/api/v1/claims/${claimId}/report`);
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body: unknown = await response.json();
          if (body && typeof body === "object" && "detail" in body) {
            detail = String(body.detail);
          }
        } catch {
          // Non-JSON error body; statusText is the best available.
        }
        setReportError(detail);
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
      // Revoked on a timer, not immediately: the new tab has to finish reading
      // the blob first, and revoking synchronously produces a blank document.
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err: unknown) {
      setReportError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="claim-nav rise">
      <div className="claim-nav-id">
        <span className="label">structure</span>
        <strong className="mono">{uniqueId ?? `claim ${claimId}`}</strong>
        {interventionType !== null && interventionType !== undefined && (
          <span className="sub">{interventionType.replace(/_/g, " ")}</span>
        )}
      </div>

      <nav className="claim-tabs" aria-label="Views of this structure">
        <ul>
          {tabs.map((t) => (
            <li key={t.name}>
              <a className={active === t.name ? "on" : ""} href={t.href}>
                {t.text}
              </a>
            </li>
          ))}
          {/* Gated on the capability the endpoint enforces, so the control is
              absent rather than present-and-403. */}
          {can("verdict:read") && (
            <li>
              <button className="rail-btn" onClick={() => void openReport()} disabled={loading}>
                {loading ? "Building…" : "Evidence Pack"}
              </button>
            </li>
          )}
        </ul>
      </nav>

      {reportError !== null && (
        <p className="note">
          The Evidence Pack could not be built: {reportError}
        </p>
      )}
    </div>
  );
}
