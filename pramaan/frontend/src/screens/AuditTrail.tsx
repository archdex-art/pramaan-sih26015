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
 * This trail is the wider, weaker record. It is useful and it is **not**
 * tamper-evident, and the difference is stated on screen rather than left for a
 * reader to assume.
 *
 * ## What is shown, and what was removed
 *
 * The first version of this table printed every stored column: a truncated user
 * UUID in an "Entity" column, and the raw payload as `key=value` pairs. Against
 * the real data that produced rows reading
 * `faf-41f7-947e-e38d2694e251 | role=wcdc · username=wcdc.nanded`, which is
 * three restatements of one fact — the officer's own row already names them —
 * plus a partial identifier that identifies nobody. 45 of 47 rows carried
 * `entity=user` and the actor's UUID, which is tautological: the actor *is* the
 * subject of a sign-in.
 *
 * So the columns are now the five questions an auditor actually asks — when,
 * who, what, about which structure, and with what outcome — and the payload is
 * filtered to its informative residue. Two rules do that filtering:
 *
 * 1. **Drop anything already in a column.** `username` and `role` are columns.
 * 2. **Drop anything that is not a fact about this event.** Nulls, internal row
 *    ids, and the ledger's `row_hash` — the hash is the subject of the ledger
 *    screen, where the chain is what is being verified; here it is 64 characters
 *    that push the readable content off the line.
 *
 * Nothing is *hidden*: `Show raw payload` restores every stored key verbatim.
 * An audit view that quietly discards data would be worse than a noisy one, so
 * the noise is opt-in rather than the default.
 */

import { useEffect, useState } from "react";
import { ApiError, get } from "../lib/api";

/** One row of `audit_log`, joined to `users` for attribution and to the claim
 *  hierarchy for a readable subject.
 *
 *  The actor triple is nullable because the join is a LEFT JOIN on purpose: a
 *  system-generated event has no user, and a deleted user must not erase the
 *  history of what happened. A null actor is a *system* actor, not an unknown
 *  one, and is rendered as such. */
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
  /** The structure's programme identifier when the event concerns an
   *  assessment; null for account events. Resolved server-side — the browser
   *  should not be guessing what a verdict id refers to. */
  subject: string | null;
}

const LIMIT = 200;

/** Machine action → what a person calls it.
 *
 *  The dotted form is the stored vocabulary and stays in the filter, because an
 *  auditor filtering a trail wants the exact token. It is a poor table cell
 *  though: `auth.token.refreshed` is read as jargon where "Session renewed" is
 *  read as an event. Unknown actions fall through to the raw token rather than
 *  to a guess, so a newly recorded action appears immediately and legibly
 *  enough rather than silently becoming a blank. */
const ACTION_WORDS: Record<string, string> = {
  "auth.login.succeeded": "Signed in",
  "auth.login.failed": "Sign-in failed",
  "auth.login.locked": "Account locked",
  "auth.logout": "Signed out",
  "auth.token.refreshed": "Session renewed",
  "auth.token.rejected": "Session refresh refused",
  "claim.captured": "Evidence recorded",
  "claim.rejected": "Evidence refused",
  "adjudication.signed": "Adjudication signed",
  "verdict.recomputed": "Verdict recomputed",
};

/** Payload keys that must never reach the Detail column.
 *
 *  `username` and `role` are columns of this table. `ledger_row_id` is a
 *  primary key nobody outside the database can act on. `row_hash` belongs to
 *  the ledger screen, which exists to verify the chain; repeating 64 hex
 *  characters here buys no integrity and costs the whole line. */
const SUPPRESSED = new Set(["username", "role", "ledger_row_id", "row_hash"]);

/** Keys worth a word rather than a token. */
const KEY_WORDS: Record<string, string> = {
  tokens_revoked: "sessions ended",
  decision: "decision",
  corrected_level: "corrected to",
  reason: "reason",
};

const reasonOf = (err: unknown) => (err instanceof ApiError ? err.detail : String(err));

/** ISO timestamp to something an officer reads without decoding.
 *
 *  Rendered in the browser's locale rather than forced to IST: the server
 *  stores `timestamptz`, so the instant is unambiguous, and pinning a display
 *  zone here would mislead anyone reading from another one. */
function when(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The informative residue of a payload, as one short line.
 *
 *  Returns null when nothing survives filtering, which is the common case for a
 *  sign-in: everything a login event carries is already on the row. A dash is
 *  then drawn instead of an empty cell, so the reader can tell "nothing to add"
 *  from "failed to load". */
function detail(payload: Record<string, unknown>): string | null {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(payload ?? {})) {
    if (SUPPRESSED.has(key)) continue;
    if (value === null || value === undefined || value === "") continue;
    const word = KEY_WORDS[key] ?? key.replace(/_/g, " ");
    // Count-like keys read better as "4 sessions ended" than "sessions ended=4".
    parts.push(typeof value === "number" ? `${value} ${word}` : `${word}: ${String(value)}`);
  }
  return parts.length === 0 ? null : parts.join(" · ");
}

function raw(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload ?? {});
  if (entries.length === 0) return "{}";
  return entries.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ");
}

export function AuditTrail() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [action, setAction] = useState<string>("");
  const [showRaw, setShowRaw] = useState(false);

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

  // Built from what actually arrived rather than from ACTION_WORDS: the server
  // owns the vocabulary, and offering a filter for an action this database has
  // never recorded invites the reader to conclude nothing happened.
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
          </select>{" "}
          {/* Opt-in rather than default. Every stored key is one click away, so
              filtering the Detail column hides nothing from an auditor — it only
              stops the common case from being unreadable. */}
          <button className="rail-btn" onClick={() => setShowRaw(!showRaw)}>
            {showRaw ? "Hide raw payload" : "Show raw payload"}
          </button>
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
                <th>Officer</th>
                <th>Action</th>
                <th>Structure</th>
                <th>{showRaw ? "Stored payload" : "Outcome"}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => {
                const line = showRaw ? raw(e.payload) : detail(e.payload);
                return (
                  <tr key={e.id}>
                    <td className="mono">{when(e.at)}</td>
                    <td>
                      {e.full_name === null ? (
                        // A null actor is the pipeline, not a missing person.
                        <span className="label">system</span>
                      ) : (
                        <>
                          {e.full_name}
                          <br />
                          <span className="mono sub">
                            {e.username}
                            {e.role !== null && ` · ${e.role}`}
                          </span>
                        </>
                      )}
                    </td>
                    <td>{ACTION_WORDS[e.action] ?? <span className="mono">{e.action}</span>}</td>
                    <td className="mono">{e.subject ?? "—"}</td>
                    <td className={showRaw ? "mono sub" : "sub"}>{line ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
