/**
 * Login screen.
 *
 * Visual direction is the same "Survey Record" language as the console: paper
 * ground, serif body, mono for anything machine-generated. It should read like
 * the cover sheet of a filed report, not a SaaS onboarding page.
 *
 * ## On the error message
 *
 * A wrong password and a username that does not exist produce the **same**
 * message, deliberately. Distinguishing them turns the login form into a
 * directory of valid government usernames, which is the first step of a
 * credential-stuffing campaign. The server also spends the same time on both
 * paths (it verifies against a throwaway hash when the user is absent) so the
 * response *timing* cannot be used to tell them apart either. This is not a
 * rough edge to be smoothed; smoothing it is the vulnerability.
 *
 * A lockout is different and is shown differently: the credentials may well be
 * correct, and the remedy is to wait, so the screen says how long.
 */

import { type FormEvent, useEffect, useRef, useState } from "react";
import { AuthError, login } from "../lib/auth";

/** The seeded accounts, one per workspace.
 *
 * `can` describes the capability that actually distinguishes the role, taken
 * from `CAPABILITIES` in `app/core/authz.py` — not marketing copy. `pia` holds
 * `claim:create` and cannot adjudicate; `wcdc` holds `adjudication:create` and
 * can sign; `dolr_admin` is national-scope and deliberately holds neither,
 * which is the separation-of-duties rule the ledger depends on. */
const DEMO_ROLES = [
  {
    label: "Field user",
    username: "pia.nanded",
    can: "Files claims · district 520 · cannot sign",
  },
  {
    label: "Monitoring officer",
    username: "wcdc.nanded",
    can: "Adjudicates · district 520 · signs the ledger",
  },
  {
    label: "Administrator",
    username: "admin.dolr",
    can: "National view · cannot sign or file",
  },
] as const;

/** Shared by every seeded demo account. Also printed in the README. */
const DEMO_PASSWORD = "pramaan-demo-2026";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lockedFor, setLockedFor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);

  // Tick the lockout countdown. Showing a live number rather than the server's
  // one-shot sentence means the operator can see that waiting is working.
  useEffect(() => {
    if (lockedFor === null) return;
    if (lockedFor <= 0) {
      setLockedFor(null);
      setError(null);
      return;
    }
    const t = setTimeout(() => setLockedFor((s) => (s === null ? null : s - 1)), 1000);
    return () => clearTimeout(t);
  }, [lockedFor]);

  const locked = lockedFor !== null && lockedFor > 0;
  // Trim only the username. Passwords may legitimately begin or end with a
  // space, and silently trimming one is an unfixable "correct password
  // rejected" report.
  const trimmed = username.trim();
  const submittable = trimmed.length > 0 && password.length > 0 && !loading && !locked;

  /** One sign-in attempt, shared by the form and the quick-role buttons so both
   *  get identical lockout, error and focus behaviour. */
  const attempt = (user: string, pass: string) => {
    setLoading(true);
    setError(null);

    void login(user, pass)
      .then(() => {
        // Clear the password from component state on success. It is about to
        // leave scope anyway, but not holding a credential in a mounted
        // component's state any longer than necessary is free.
        setPassword("");
        onLogin();
      })
      .catch((err: unknown) => {
        if (err instanceof AuthError) {
          setError(err.message);
          if (err.isLockout && err.retryAfterSeconds !== null) {
            setLockedFor(err.retryAfterSeconds);
          }
        } else {
          setError("Sign-in failed.");
        }
        // Clear and refocus the password field, not the username: the username
        // is usually right and retyping it on every attempt is friction that
        // pushes people towards writing passwords down.
        setPassword("");
        passwordRef.current?.focus();
      })
      .finally(() => setLoading(false));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!submittable) return;
    attempt(trimmed, password);
  };

  const signInAs = (user: string) => {
    if (loading || locked) return;
    // Fill the field too, so the judge can see which account was used rather
    // than being teleported in by an invisible mechanism.
    setUsername(user);
    attempt(user, DEMO_PASSWORD);
  };

  return (
    <div className="login-shell">
      <main className="login-card">
        <div className="login-gov">
          <span>Government of India</span>
          <span>Ministry of Rural Development · DoLR</span>
          <span>WDC-PMKSY 2.0</span>
        </div>

        <div className="login-wordmark">
          <span className="deva">प्रमाण</span>
          <span className="latin">PRAMAAN</span>
          <span className="label">
            Photo-Referenced Analytics for Monitoring of Assets And
            Natural-resources
          </span>
        </div>

        <form className="login-form" onSubmit={submit} noValidate>
          <label className="login-field">
            <span className="login-label">Username</span>
            <input
              name="username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              enterKeyHint="next"
              autoFocus
              disabled={loading}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>

          <label className="login-field">
            <span className="login-label">Password</span>
            <input
              ref={passwordRef}
              name="password"
              type="password"
              autoComplete="current-password"
              enterKeyHint="go"
              disabled={loading}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>

          {/* `role="alert"` so a screen reader announces the failure. Without
              it the only signal is a visual colour change, which is exactly the
              user who most needs telling. */}
          {error !== null && (
            <p className="login-error" role="alert">
              {error}
              {locked && (
                <>
                  {" "}
                  <span className="mono">
                    Retry in {lockedFor}s.
                  </span>
                </>
              )}
            </p>
          )}

          <button type="submit" className="login-submit" disabled={!submittable}>
            {loading ? "Signing in…" : locked ? `Locked (${lockedFor}s)` : "Sign in"}
          </button>
        </form>

        {/* Quick role selection for judges.

            These are not a bypass. Each button performs a real `POST
            /auth/login` with a real seeded account and gets a real RS256 token
            whose capabilities the API enforces on every subsequent request — the
            only thing being skipped is typing. That matters for a demo where the
            point is that the three roles genuinely differ: a judge can move
            between them in one click and see the enforcement change.

            Visible because this build is a prototype, and labelled as such. */}
        <div className="login-demo">
          <p className="login-demo-head mono">
            Quick demo role selection — for judges
          </p>
          <div className="login-demo-row">
            {DEMO_ROLES.map((r) => (
              <button
                key={r.username}
                type="button"
                className="login-demo-btn"
                disabled={loading || locked}
                onClick={() => signInAs(r.username)}
              >
                <span className="login-demo-role">{r.label}</span>
                <span className="login-demo-who mono">{r.username}</span>
                <span className="login-demo-can">{r.can}</span>
              </button>
            ))}
          </div>
        </div>

        <footer className="login-foot">
          <p className="rail-build mono">PROTOTYPE · not a deployed system</p>
          <p className="login-caveat">
            Nothing here becomes government evidence until a named officer signs
            it.
          </p>
        </footer>
      </main>
    </div>
  );
}
