/**
 * The system audit trail.
 *
 * `audit_log` has existed since the baseline migration, RANGE-partitioned by
 * month, and nothing wrote to it. This screen is the read side of that table
 * now that the login, logout, refresh and signature paths record to it.
 *
 * ## Why this is not the adjudication ledger
 *
 * The ledger is the authoritative record of *decisions*: append-only, hash
 * chained, with `UPDATE` and `DELETE` revoked from the application role, and
 * verifiable from `psql` without this application running. It is deliberately
 * narrow — only signatures go in it, because a chain that also carried page
 * views would make tampering with a signature cheaper to hide in the noise.
 *
 * This trail is the wider, weaker record: who logged in, who failed to, what
 * was recomputed. It is useful and it is **not** tamper-evident, and the
 * difference is stated on screen rather than left for a reader to assume. An
 * audit view that implies more integrity than it has is worse than none.
 *
 * ## Scoping
 *
 * Gated on `ledger:verify`, which auditors, monitoring officers and the
 * administrator hold and field roles do not. The server owns that check; this
 * screen only decides what to draw.
 */

import { useEffect, useState } from "react";
import { ApiError, get } from "../lib/api";

/** One row of `audit_log`, joined to `users` for attribution.
 *
 *  `username`, `full_name` and `role` are nullable because the join is a LEFT
 *  JOIN on purpose: a system-generated event has no user, and a deleted user
 *  must not erase the history of what happened. A null actor is a system
 *  actor, not an unknown one, and is rendered as such. */
interface AuditEvent {
  id: number;
  at: string;
  username: string | null;
  full_name: string | null;
  role: string | null;
  action: string;
  entity: string | null;
  entity_id: string | null;
  payload: Record<string, unknown>;
}

const LIMIT = 200;

const reasonOf = (err: unknown) => (err instanceof ApiError ? err.detail : String(err));

/** ISO timestamp to something an officer reads without decoding.
 *
 *  Rendered in the browser's locale rather than forced to IST: the server
 *  stores `timestamptz`, so the instant is unambiguous, and pinning a display
 *  zone here would silently mislead anyone reading from another one. */
function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** The payload column, flattened to one readable line.
 *
 *  Not `JSON.stringify` of the whole object: the point of the column is the
 *  handful of decision-bearing keys an event carries, and a wall of braces in a
 *  table cell is read as noise and then ignored. Keys are shown in the order
 *  the server sent them, which is the order the writer chose. */
function summarise(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload ?? {});
  if (entries.length === 0) return "—";
  return entries.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ");
}

export function AuditTrail() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    const query = action === "" ? "" : `&action=${encodeURIComponent(action)}`;
    setEvents(null);
    setError(null);
    void get<AuditEvent[]>(`/api/v1/audit?limit=${LIMIT}${query}`).then(
      (rows) => {
        if (!cancelled) setEvents(rows);
      },
      (err: unknown) => {
        if (!cancelled) setError(reasonOf(err));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [action]);

  // Built from what actually arrived rather than from a hardcoded vocabulary:
  // the server owns the action enum, and a filter listing an action the server
  // never emits would invite a reader to conclude nothing had happened.
  const actions = events === null ? [] : [...new Set(events.map((e) => e.action))].sort();

  return (
    <div className="screen">
      <header className="screen-head rise">
        <div>
          <h1>Audit trail</h1>
          <p className="sub">
            Authentication and decision events, newest first. The most recent{" "}
            <span className="mono">{LIMIT}</span> in your jurisdiction.
          </p>
        </div>
      </header>

      <p className="triage-note note">
        This is the <strong>system trail</strong>, not the adjudication ledger.
        The ledger is append-only and hash-chained, the database refuses{" "}
        <span className="mono">UPDATE</span> and{" "}
        <span className="mono">DELETE</span> on it, and it can be verified from{" "}
        <span className="mono">psql</span> without this application — see the
        Adjudication ledger screen. This table is a wider record and is{" "}
        <strong>not tamper-evident</strong>. Both exist because they answer
        different questions, and only one of them is evidence.
      </p>

      {events !== null && actions.length > 0 && (
        <div className="panel rise">
          <h2 className="label">Filter</h2>
          <label className="label" htmlFor="audit-action">
            action
          </label>{" "}
          <select
            id="audit-action"
            className="mono"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          >
            <option value="">all ({events.length})</option>
            {actions.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="table-wrap panel rise">
        {events === null ? (
          error !== null ? (
            <p className="empty">The audit trail could not be loaded: {error}</p>
          ) : (
            <p className="loading">Loading audit trail…</p>
          )
        ) : events.length === 0 ? (
          <p className="empty">
            No events recorded{action === "" ? "" : ` for action ${action}`}. The
            trail records authentication and decision events; an empty trail
            means none have happened on this database, not that recording is
            off.
          </p>
        ) : (
          <table className="register">
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Role</th>
                <th>Action</th>
                <th>Entity</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td className="mono">{when(e.at)}</td>
                  <td>
                    {e.full_name === null ? (
                      // A null actor is the pipeline, not a missing person.
                      <span className="label">system</span>
                    ) : (
                      <>
                        {e.full_name}{" "}
                        <span className="mono sub">{e.username}</span>
                      </>
                    )}
                  </td>
                  <td className="mono">{e.role ?? "—"}</td>
                  <td className="mono">{e.action}</td>
                  <td className="mono">
                    {e.entity === null
                      ? "—"
                      : e.entity_id === null
                        ? e.entity
                        : `${e.entity} ${e.entity_id}`}
                  </td>
                  <td className="mono sub">{summarise(e.payload)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
