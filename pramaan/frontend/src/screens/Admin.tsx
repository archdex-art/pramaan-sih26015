/**
 * Administration — the landing screen for the administration workspace.
 *
 * Four questions, four panels, each answered by a real endpoint over real stored
 * rows:
 *
 * - **System** — what engine is running, how much is in the database, and does
 *   the adjudication hash chain still verify.
 * - **User directory** — every account, its role, its workspace and its
 *   jurisdiction. This is the panel that shows the role model is live rather
 *   than asserted: the workspace column is the server's own
 *   `Role → Workspace` mapping, printed.
 * - **Districts** — per-district claim and verdict counts, and DEM readiness.
 * - **Data sources** — the recorded verification result for each external
 *   source, licence included.
 *
 * ## Why every panel fails on its own
 *
 * `Promise.allSettled`, not `all`. Four independent endpoints, four independent
 * capability checks on the server; a 403 on the district endpoint must not blank
 * the user directory. The same reason the reconciliation screen settles its three
 * fetches instead of awaiting them together.
 *
 * ## What is deliberately absent
 *
 * No create/edit/delete. Accounts are provisioned by `scripts/seed_users.py` and
 * districts by `make seed-district`; there is no write endpoint behind a form
 * here, so there is no form. The panel notes say which command does the job.
 */

import { useEffect, useState } from "react";
import type {
  AdminDistrict,
  AdminSystem,
  AdminUser,
  DataSource,
} from "../lib/api";
import {
  ApiError,
  fetchAdminDistricts,
  fetchAdminSystem,
  fetchAdminUsers,
  fetchDataSources,
} from "../lib/api";
import { authFetch } from "../lib/auth";
import type { Session } from "../lib/auth";

interface MeResponse {
  user_id: string;
  username: string;
  full_name: string;
  role: string;
  workspace: string;
  capabilities: string[];
}

/** Panel-scoped failure keys. Kept as a union rather than free-form strings so a
 *  typo cannot silently produce a panel that never reports its own error. */
type PanelKey = "system" | "users" | "districts" | "sources";

export function Admin({ session }: { session: Session }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [system, setSystem] = useState<AdminSystem | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [districts, setDistricts] = useState<AdminDistrict[] | null>(null);
  const [sources, setSources] = useState<DataSource[] | null>(null);
  const [failures, setFailures] = useState<Partial<Record<PanelKey, string>>>({});

  useEffect(() => {
    let cancelled = false;
    void authFetch("/api/v1/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: MeResponse | null) => !cancelled && setMe(d));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([
      fetchAdminSystem(),
      fetchAdminUsers(),
      fetchAdminDistricts(),
      fetchDataSources(),
    ]).then(([sys, usr, dst, src]) => {
      if (cancelled) return;
      const bad: Partial<Record<PanelKey, string>> = {};
      if (sys.status === "fulfilled") setSystem(sys.value);
      else bad.system = reasonOf(sys.reason);
      if (usr.status === "fulfilled") setUsers(usr.value);
      else bad.users = reasonOf(usr.reason);
      if (dst.status === "fulfilled") setDistricts(dst.value);
      else bad.districts = reasonOf(dst.reason);
      if (src.status === "fulfilled") setSources(src.value);
      else bad.sources = reasonOf(src.reason);
      setFailures(bad);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="screen admin-panel">
      <header className="screen-head rise">
        <div>
          <h1>Administration</h1>
          <p className="sub">
            Accounts, districts, engine state and external sources. Read-only:
            provisioning is a command-line operation and there is no write
            endpoint behind this screen.
          </p>
        </div>
      </header>

      <div className="panel strip-panel rise">
        <h2>Signed-in officer</h2>
        <dl className="adj-receipt">
          <dt>Name</dt>
          <dd>{session.full_name}</dd>
          <dt>Username</dt>
          <dd className="mono">{session.username}</dd>
          <dt>Role</dt>
          <dd className="mono">{session.role}</dd>
          <dt>Workspace</dt>
          <dd className="mono">{me?.workspace ?? "…"}</dd>
          <dt>Districts</dt>
          <dd className="mono">
            {session.districts.length > 0
              ? session.districts.join(", ")
              : "national (all districts)"}
          </dd>
        </dl>

        <h3>Capabilities</h3>
        {me ? (
          <ul className="admin-caps">
            {me.capabilities.map((c) => (
              <li key={c} className="mono">
                {c}
              </li>
            ))}
          </ul>
        ) : (
          <p className="loading">Loading…</p>
        )}
      </div>

      {/* ---- system ---------------------------------------------------- */}
      <div className="panel strip-panel rise">
        <h2>System</h2>
        {system ? (
          <>
            <dl className="strip">
              <div>
                <dt className="label">engine</dt>
                <dd className="figure mono">{system.engine_version}</dd>
              </div>
              <div>
                <dt className="label">offline mode</dt>
                <dd className="figure mono">
                  {system.offline_mode ? "on" : "off"}
                </dd>
              </div>
              <div>
                <dt className="label">claims</dt>
                <dd className="figure mono">{system.claims}</dd>
              </div>
              <div>
                <dt className="label">verdicts</dt>
                <dd className="figure mono">{system.verdicts}</dd>
              </div>
              <div>
                <dt className="label">adjudications</dt>
                <dd className="figure mono">{system.adjudications}</dd>
              </div>
              <div>
                <dt className="label">accounts</dt>
                <dd className="figure mono">{system.users}</dd>
              </div>
            </dl>

            {/* The chain check is recomputed on this request by re-hashing every
                row, so it is reported as a verification result rather than as a
                stored flag someone could have written by hand. */}
            <p className="admin-chain">
              <span className="label">adjudication chain</span>
              <span
                className={`ledger-badge mono ${
                  system.ledger_valid ? "ledger-valid" : "ledger-broken"
                }`}
              >
                {system.ledger_valid
                  ? `verified — ${String(system.ledger_rows)} row${system.ledger_rows === 1 ? "" : "s"} re-hashed`
                  : `chain does not verify — ${String(system.ledger_rows)} row${system.ledger_rows === 1 ? "" : "s"}`}
              </span>
            </p>

            <h3>Subsystem tables</h3>
            <div className="table-wrap panel">
              <table className="register">
                <thead>
                  <tr>
                    <th>Table</th>
                    <th className="num">Rows</th>
                    <th>Populated</th>
                  </tr>
                </thead>
                <tbody>
                  {system.subsystems.map((t) => (
                    <tr key={t.table}>
                      <td className="mono">{t.table}</td>
                      <td className="num mono">{t.row_count}</td>
                      <td className="mono">{t.populated ? "yes" : "empty"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="note">
              An empty subsystem table is stated as empty, not omitted. A table
              with no rows is a part of the system that has not been exercised in
              this deployment, and hiding it would read as a part that does not
              exist.
            </p>
          </>
        ) : (
          <Missing what="System state" reason={failures.system} />
        )}
      </div>

      {/* ---- user directory -------------------------------------------- */}
      <h2>User directory</h2>
      <p className="note">
        Every provisioned account. The <strong>workspace</strong> column is the
        server’s own role-to-workspace mapping printed back — the same mapping
        that decides which screens each of these accounts lands on. Accounts are
        created by <code>scripts/seed_users.py</code>; there is no account form
        here because there is no endpoint behind one.
      </p>
      <div className="table-wrap panel rise">
        {users ? (
          <table className="register">
            <thead>
              <tr>
                <th>Name</th>
                <th>Username</th>
                <th>Role</th>
                <th>Workspace</th>
                <th>Jurisdiction</th>
                <th>Active</th>
                <th>Last sign-in</th>
                <th className="num">Rejected attempts</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.username}>
                  <td>{u.full_name}</td>
                  <td className="mono id">{u.username}</td>
                  <td className="mono">{u.role}</td>
                  <td className="mono">{u.workspace}</td>
                  <td className="mono">
                    {u.scope_district !== null
                      ? `district ${u.scope_district}`
                      : u.scope_state !== null
                        ? `state ${u.scope_state}`
                        : "national"}
                  </td>
                  <td className="mono">{u.is_active ? "yes" : "disabled"}</td>
                  <td className="mono">{u.last_login_at ?? "never"}</td>
                  <td className="num mono">
                    {u.failed_attempts}
                    {u.locked_until !== null && ` · locked to ${u.locked_until}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">
            <Missing what="User directory" reason={failures.users} />
          </p>
        )}
      </div>

      {/* ---- districts -------------------------------------------------- */}
      <h2>Districts</h2>
      <p className="note">
        DEM readiness is shown per district because terrain is the one evidence
        family that cannot degrade quietly: without derivatives covering a
        district’s claim extent there is no slope, no drainage and no
        plausibility check for any claim in it.{" "}
        <strong>Missing tiles are listed, not summarised away.</strong>
      </p>
      <div className="table-wrap panel rise">
        {districts ? (
          <table className="register">
            <thead>
              <tr>
                <th>District (LGD)</th>
                <th className="num">Claims</th>
                <th className="num">Verdicts</th>
                <th className="num">Signed</th>
                <th>Intervention types</th>
                <th>DEM derivatives</th>
                <th>Covers claim extent</th>
                <th>Missing tiles</th>
              </tr>
            </thead>
            <tbody>
              {districts.map((d) => (
                <tr key={d.district_lgd}>
                  <td className="mono id">{d.district_lgd}</td>
                  <td className="num mono">{d.claim_count}</td>
                  <td className="num mono">{d.verdict_count}</td>
                  <td className="num mono">{d.adjudicated_count}</td>
                  <td>
                    {d.intervention_types.length > 0
                      ? d.intervention_types.map((t) => t.replace(/_/g, " ")).join(", ")
                      : "none recorded"}
                  </td>
                  <td className="mono">
                    {d.dem.derivatives_present
                      ? d.dem.derivatives.join(" · ")
                      : "none present"}
                  </td>
                  <td className="mono">
                    {d.dem.covers_claim_extent ? "yes" : "not fully"}
                  </td>
                  <td className="mono reason-cell">
                    {d.dem.missing_tiles.length === 0
                      ? `none · ${String(d.dem.tiles.length)} tile${d.dem.tiles.length === 1 ? "" : "s"} present`
                      : d.dem.missing_tiles.join(" ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">
            <Missing what="District readiness" reason={failures.districts} />
          </p>
        )}
      </div>

      {/* ---- data sources ----------------------------------------------- */}
      <h2>Data sources</h2>
      <p className="note">
        <strong>These are recorded verification results, not a live connection
        test.</strong>{" "}
        Each row is the outcome the last verification run wrote down, with the
        time it was taken. A status other than <code>OK</code> is a stated fact
        about this deployment: <code>SKIPPED_NO_CREDENTIALS</code> against NRSC
        Bhoonidhi means the account required to reach it was never held, and
        recording that is more useful than a green row that would be untrue.
      </p>
      <div className="table-wrap panel rise">
        {sources ? (
          <table className="register">
            <thead>
              <tr>
                <th>Source</th>
                <th>Status</th>
                <th>Licence</th>
                <th>Checked at</th>
                <th className="num">Elapsed</th>
                <th>Recorded detail</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.key}>
                  <td>
                    {s.name}
                    <span className="admin-source-purpose">{s.purpose}</span>
                  </td>
                  <td className="mono">{s.status}</td>
                  <td className="mono">{s.licence}</td>
                  <td className="mono">{s.checked_at ?? "not recorded"}</td>
                  <td className="num mono">
                    {s.elapsed_ms === null ? "—" : `${String(s.elapsed_ms)} ms`}
                  </td>
                  <td className="reason-cell">{s.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="empty">
            <Missing what="Data source records" reason={failures.sources} />
          </p>
        )}
      </div>

      <div className="panel strip-panel rise">
        <h2>Operator notes</h2>
        <ul className="admin-notes">
          <li>
            Accounts are provisioned by <code>scripts/seed_users.py</code>. There
            is no create or edit form here because there is no write endpoint
            behind one.
          </li>
          <li>
            District onboarding is idempotent:{" "}
            <code>make seed-district DISTRICT=...</code>
          </li>
          <li>
            Engine configuration — ladder, family weights, thresholds, expected
            signatures — is read from the running engine in the Method drawer,
            reachable from every screen.
          </li>
        </ul>
      </div>
    </div>
  );
}

/** Why a panel has no content: still loading, or the endpoint said no.
 *
 * Four call sites that must stay in lockstep. The distinction matters — "loading"
 * and "your account cannot read this" are different facts, and a single "no
 * data" line would merge them into something an operator cannot act on. */
function Missing({ what, reason }: { what: string; reason: string | undefined }) {
  return reason === undefined ? (
    <span className="loading">Loading {what.toLowerCase()}…</span>
  ) : (
    <span className="note">
      {what} could not be read: {reason}
    </span>
  );
}

const reasonOf = (err: unknown) => (err instanceof ApiError ? err.detail : String(err));
