/**
 * Admin panel — session identity and system information.
 *
 * For the demo, this shows the signed-in officer's identity, role, capabilities,
 * and jurisdiction scope. Full user management (CRUD) and district onboarding
 * are backend-only operations (seed scripts + migration) — the UI surface here
 * is the introspection view that proves the role model works.
 */

import { useEffect, useState } from "react";
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

export function Admin({ session }: { session: Session }) {
  const [me, setMe] = useState<MeResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    void authFetch("/api/v1/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then((d: MeResponse | null) => !cancelled && setMe(d));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="admin-panel">
      <header className="screen-head rise">
        <h1>Administration</h1>
      </header>

      <div className="panel rise">
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

      <div className="panel rise">
        <h2>System notes</h2>
        <ul className="admin-notes">
          <li>
            User management is a backend operation (seed scripts). This panel
            shows the role model is live, not a mockup.
          </li>
          <li>
            Engine configuration (weights, ladder, thresholds) is visible in the
            Method drawer from any screen.
          </li>
          <li>
            District onboarding is idempotent:{" "}
            <code>make seed-district DISTRICT=...</code>
          </li>
        </ul>
      </div>
    </section>
  );
}
