/**
 * One submission, tracked — the field workspace's per-record screen.
 *
 * ## Why this is not the reconciliation detail
 *
 * `Detail.tsx` answers a monitoring officer's question: is this verdict sound,
 * and should I sign it. It is three columns of evidence with the dissent panel
 * pinned open. A WDT or PIA member who opens the work they recorded has a
 * different question — *where has it got to, and what is waiting on whom* — and
 * the evidence tree does not answer it. So this screen is a lifecycle tracker
 * with a link to the full reconciliation view, not a second copy of it.
 *
 * ## The stages are read, not drawn
 *
 * Every stage state below comes from a stored column: whether a verdict row
 * exists, its `status` (`pending` | `adjudicated` | `superseded`), and whether
 * a chain row can be found for it. A claim with no verdict shows *awaiting
 * reconciliation* — not a completed step with a spinner, and not a percentage.
 * The one thing a progress display must never do is imply that work has
 * happened because time has passed.
 *
 * ## Why the whole register is fetched to render one row
 *
 * There is no `GET /api/v1/claims/{id}`. `GET /api/v1/claims` returns the rows
 * the server has already narrowed to the caller's jurisdiction, and the row is
 * selected from that list. This is deliberate rather than pending: a per-claim
 * endpoint would need its own jurisdiction check, and a second copy of that
 * check is a second place for it to be wrong. A claim absent from the list is
 * absent from this account's view, which is the answer the screen needs.
 */

import { useEffect, useMemo, useState } from "react";
import type { EvidenceTree, LedgerEntry, RegisterRow, Verdict } from "../lib/api";
import {
  ApiError,
  fetchClaims,
  fetchEvidence,
  fetchLedger,
  fetchVerdict,
} from "../lib/api";
import { can, getSession } from "../lib/auth";

const shortLevel = (level: string) => level.split("_")[0] ?? level;

const reasonOf = (err: unknown) => (err instanceof ApiError ? err.detail : String(err));

/**
 * A stage's state, and nothing between the four.
 *
 * `unreadable` is the honest fourth: the adjudication chain is gated on
 * `ledger:verify`, which no field role holds, so a field account genuinely
 * cannot see whether a signature was chained. That is different from the stage
 * not having happened, and collapsing the two would either invent a completion
 * or deny one.
 */
type StageState = "complete" | "current" | "waiting" | "unreadable";

const STAGE_WORD: Record<StageState, string> = {
  complete: "done",
  current: "in progress",
  waiting: "not reached",
  unreadable: "not visible here",
};

interface Stage {
  key: string;
  name: string;
  state: StageState;
  /** What the stored row says at this stage. Never a placeholder. */
  detail: string;
}

export function SubmissionDetail({ claimId }: { claimId: number }) {
  const session = getSession();
  const maySign = can("adjudication:create");
  // Read rather than assumed: `readonly` and `dolr_admin` hold `ledger:verify`
  // without holding `adjudication:create`, and a field role holds neither, so
  // the two questions are asked separately.
  const mayReadChain = can("ledger:verify");

  const [rows, setRows] = useState<RegisterRow[] | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [verdictError, setVerdictError] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceTree | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[] | null>(null);
  const [ledgerError, setLedgerError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void fetchClaims().then(
      (r) => {
        if (!cancelled) setRows(r);
      },
      (e: unknown) => {
        if (!cancelled) setRowsError(reasonOf(e));
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setVerdict(null);
    setVerdictError(null);
    setEvidence(null);
    // Settled, not all. A claim with no verdict is a normal, expected state on
    // this screen — it is the state the whole first half of the pipeline is
    // about — so a 404 from the verdict endpoint must not blank the evidence
    // counts or the metadata beside it.
    void Promise.allSettled([fetchVerdict(claimId), fetchEvidence(claimId)]).then(
      ([v, e]) => {
        if (cancelled) return;
        if (v.status === "fulfilled") setVerdict(v.value);
        else setVerdictError(reasonOf(v.reason));
        if (e.status === "fulfilled") setEvidence(e.value);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [claimId]);

  useEffect(() => {
    // Not attempted without the capability: firing a request that is certain to
    // be refused would put a 403 in the audit trail for a screen the officer
    // opened legitimately, and the answer would still be "you cannot see this".
    if (!mayReadChain) return;
    let cancelled = false;
    void fetchLedger().then(
      (l) => {
        if (!cancelled) setLedger(l);
      },
      (e: unknown) => {
        if (!cancelled) setLedgerError(reasonOf(e));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [mayReadChain]);

  const row = useMemo(
    () => rows?.find((r) => r.claim_id === claimId) ?? null,
    [rows, claimId],
  );

  const chainRow = useMemo(() => {
    const verdictId = verdict?.id ?? row?.verdict_id ?? null;
    if (ledger === null || verdictId === null) return null;
    return ledger.find((l) => l.verdict_id === verdictId) ?? null;
  }, [ledger, verdict, row]);

  if (rows === null) {
    return (
      <div className="screen">
        <p className="loading mono">
          {rowsError === null ? "loading this submission…" : ""}
        </p>
        {rowsError !== null && (
          <div className="error-inline">
            The register could not be read: {rowsError}. Nothing about this
            submission can be stated without it, so nothing is shown.
          </div>
        )}
      </div>
    );
  }

  if (row === null) {
    return (
      <div className="screen">
        <header className="screen-head rise">
          <div>
            <h1>Not in your view</h1>
            <p className="sub mono">claim {claimId}</p>
          </div>
        </header>
        <div className="panel strip-panel rise">
          <p className="note">
            No record with this claim id appears in the rows the server returned
            for your account. Two things produce that, and this interface cannot
            tell them apart: the record does not exist, or it belongs to a
            district outside your jurisdiction. The server does not disclose
            which, and guessing here would leak the existence of records you are
            not scoped to.
          </p>
        </div>
      </div>
    );
  }

  const hasVerdict = verdict !== null || row.verdict_id !== null;
  const status = verdict?.status ?? row.status;
  const level = verdict?.level ?? row.level;
  const label = verdict?.label ?? row.label;

  const stages: Stage[] = [
    {
      key: "recorded",
      name: "1 · recorded",
      state: "complete",
      detail: row.unique_id,
    },
    hasVerdict
      ? {
          key: "verdict",
          name: "2 · verdict computed",
          state: "complete",
          detail:
            level === null
              ? "computed, level not returned"
              : `${shortLevel(level)}${label === null ? "" : ` · ${label}`}`,
        }
      : {
          key: "verdict",
          name: "2 · verdict computed",
          state: "current",
          detail: "awaiting reconciliation",
        },
    officerStage(hasVerdict, status),
    chainStage(status, mayReadChain, chainRow, ledgerError),
  ];

  return (
    <div className="screen detail">
      <header className="screen-head rise">
        <div>
          <h1>{row.intervention_type.replace(/_/g, " ")}</h1>
          <p className="sub mono">
            {row.unique_id} · district {row.district_lgd} · claimed{" "}
            {row.asserted_date ?? "date not recorded"}
          </p>
        </div>
        <div className="head-actions">
          <span className="badge" data-prov={row.provenance}>
            {row.provenance}
          </span>
          {row.status === "adjudicated" ? (
            <span className="mono state-signed">signed</span>
          ) : (
            <span className="chip ghost">provisional</span>
          )}
          <button
            className="btn"
            onClick={() => {
              location.hash = `#/claim/${String(claimId)}`;
            }}
          >
            Full reconciliation view
          </button>
        </div>
      </header>

      <div className="panel strip-panel rise">
        <h2 className="label">Where this record has reached</h2>
        <dl className="strip">
          {stages.map((s) => (
            <div key={s.key}>
              <dt className="label">{s.name}</dt>
              <dd className="mono figure">{s.detail}</dd>
              <dd className="label">{STAGE_WORD[s.state]}</dd>
            </div>
          ))}
        </dl>
        <p className="note">
          Each stage is read from a stored column, not from elapsed time. The
          decision and the signature are one stage apart because they are two
          different records: the officer's decision is a row in the adjudication
          table, and the signature is that row's link in the hash chain, written
          in the same transaction. Nothing here advances on its own.
        </p>
        {verdictError !== null && !hasVerdict && (
          <p className="note">
            The verdict endpoint returned: <span className="mono">{verdictError}</span>
            . On this screen that is information rather than an error — a claim
            with no verdict is the normal state of a record that has been
            submitted and not yet reconciled.
          </p>
        )}
      </div>

      <div className="cols">
        <section className="panel col rise">
          <h2 className="label">What you submitted</h2>
          <dl className="kv">
            <dt>Type</dt>
            <dd className="mono">{row.intervention_type}</dd>
            <dt>Claimed date</dt>
            <dd className="mono">{row.asserted_date ?? "not recorded"}</dd>
            <dt>Coordinate</dt>
            <dd className="mono">
              {row.lat.toFixed(5)}°N {row.lon.toFixed(5)}°E
            </dd>
            <dt>GPS uncertainty</dt>
            {/* Never a bare number: an accuracy figure without its sign and its
                unit is not evidence of anything. */}
            <dd className="mono">
              {row.uncertainty_m === null
                ? "not recorded"
                : `±${row.uncertainty_m.toFixed(0)} m`}
            </dd>
            <dt>Detectability</dt>
            <dd className="mono">{row.detectability ?? "not recorded"}</dd>
            <dt>Expected footprint</dt>
            <dd className="mono">
              {row.expected_footprint_m2 === null
                ? "not recorded"
                : `${row.expected_footprint_m2.toFixed(0)} m²`}
            </dd>
          </dl>
          <p className="note">
            The uncertainty radius is not a footnote: every terrain variable is
            read as a distribution over that disk rather than from the single
            pixel at its centre, so a wide radius widens the range of terrain the
            verdict had to account for.
          </p>
        </section>

        <section className="panel col centre rise">
          <h2 className="label">What the engine said</h2>
          {level === null ? (
            <p className="note">
              No epistemic level exists for this record yet. Reconciliation
              reads terrain, imagery, temporal, control and context evidence for
              the coordinate above and issues a level with its dissent; it has
              not run for this claim. Until it does there is nothing here to
              report, and nothing is reported.
            </p>
          ) : (
            <>
              <div className="verdict-head">
                <div>
                  <span className="chip lg" data-level={shortLevel(level)}>
                    {shortLevel(level)}
                    {label === null ? "" : ` · ${label}`}
                  </span>
                  <p className="verdict-level mono">level {level}</p>
                </div>
              </div>
              <dl className="metrics">
                <div>
                  <dt className="label">status</dt>
                  <dd className="mono figure">{status ?? "not recorded"}</dd>
                </div>
                <div>
                  <dt className="label">version</dt>
                  <dd className="mono figure">
                    {verdict?.version ?? row.version ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt className="label">dissent</dt>
                  <dd className="mono figure">
                    {verdict?.dissent.length ?? row.dissent_count}
                  </dd>
                </div>
                <div>
                  <dt className="label">families</dt>
                  <dd className="mono figure">
                    {evidence === null
                      ? `${row.families_available}/${row.families_total}`
                      : `${evidence.families_available}/${evidence.families_total}`}
                  </dd>
                </div>
              </dl>
              <p className="note">
                The dissent count is the number of recorded statements against
                this verdict. It is never zero by design — a verdict with no
                stated counter-evidence is not shippable — and every entry is
                readable in the reconciliation view. An unavailable evidence
                family lowers coverage; it is not read as a family that agreed
                neutrally.
              </p>
              {status === "superseded" && (
                <p className="note">
                  <strong>This verdict version has been superseded.</strong> A
                  newer recomputation has replaced it and is the live record for
                  this claim. A decision taken on this version does not carry
                  across to the newer one.
                </p>
              )}
            </>
          )}
        </section>

        <section className="col rise">
          {row.detectability !== "passed" && (
            <div className="panel dissent">
              <h2 className="label">Assessed as part of a cluster</h2>
              <ul>
                <li>
                  {row.detectability === null
                    ? "No detectability result is recorded against this claim, and an unrecorded gate is not a passed gate."
                    : `The detectability gate recorded "${row.detectability}".`}
                  {row.expected_footprint_m2 === null
                    ? " The expected footprint for this intervention type is below the 30 m ground resolution of the satellite record."
                    : ` The expected footprint of ${row.expected_footprint_m2.toFixed(0)} m² is smaller than one 30 m pixel of the satellite record.`}
                </li>
                <li>
                  So a picture of this structure alone would show nothing either
                  way, and the absence of a signature in it is not evidence
                  against the work. The engine refuses to read it as such.
                </li>
                <li>
                  It is assessed at cluster scale instead: the treated area
                  around it is compared against comparable un-intervened land,
                  and the verdict speaks about that cluster's signature rather
                  than about this one structure. This is a limit of the sensor,
                  not a judgement about what you built.
                </li>
              </ul>
            </div>
          )}

          <div className="panel action">
            <h2 className="label">What this account can do</h2>
            {maySign ? (
              <p className="note">
                Your role (<code>{session?.role ?? "unknown"}</code>) holds{" "}
                <code>adjudication:create</code>, so you can sign this verdict.
                The signature box is on the reconciliation view rather than
                here, because signing requires reading the evidence and the
                dissent first and this screen deliberately shows neither in
                full.
              </p>
            ) : (
              <p className="note">
                Read-only tracking. Your role (
                <code>{session?.role ?? "unknown"}</code>) can open this record,
                the full evidence, the dissent and the rule path, but it does not
                hold <code>adjudication:create</code> and cannot sign.{" "}
                <strong>
                  Only a monitoring officer — WCDC or SLNA — can adjudicate
                </strong>
                , and nothing here becomes government evidence until one does.
                The separation is the point: the account that records a work is
                not the account that certifies it.
              </p>
            )}
          </div>

          <div className="panel action">
            <h2 className="label">The signed record</h2>
            {!mayReadChain ? (
              <p className="note">
                The adjudication ledger is gated on{" "}
                <code>ledger:verify</code>, which this account does not hold, so
                the chain link for this claim is not shown — not because it is
                missing, but because this account cannot read it. The stage
                strip above says the same thing in one word.
              </p>
            ) : ledgerError !== null ? (
              <p className="note">
                The ledger could not be read: <span className="mono">{ledgerError}</span>
                . No chain state is inferred from that failure.
              </p>
            ) : chainRow === null ? (
              <p className="note">
                {status === "adjudicated"
                  ? "This verdict is marked adjudicated but no chain row was found for it in the ledger. That is stated rather than smoothed over: the two records should not be able to disagree."
                  : "No chain row exists, because no decision has been signed for this verdict yet."}
              </p>
            ) : (
              <dl className="adj-receipt">
                <dt>Decision</dt>
                <dd>{chainRow.decision}</dd>
                <dt>Signed by</dt>
                <dd>
                  {chainRow.signed_by_name} ({chainRow.signed_by_username})
                </dd>
                <dt>At</dt>
                <dd className="mono">{chainRow.decided_at}</dd>
                <dt>Row hash</dt>
                <dd className="mono adj-hash">{chainRow.row_hash}</dd>
              </dl>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/** Stage 3 — whether an officer has decided.
 *
 *  `provisional` is not consulted: the register derives it as
 *  `status != 'adjudicated'`, so reading both would be reading one column
 *  twice and inviting the two to disagree on screen. */
function officerStage(hasVerdict: boolean, status: string | null): Stage {
  const name = "3 · officer decision";
  if (!hasVerdict) {
    return { key: "decision", name, state: "waiting", detail: "no verdict to decide" };
  }
  if (status === "adjudicated") {
    return { key: "decision", name, state: "complete", detail: "adjudicated" };
  }
  if (status === "superseded") {
    // Not "done" and not "waiting": the version this row describes was replaced
    // before or after any decision on it, and `status` alone cannot say which.
    return { key: "decision", name, state: "waiting", detail: "superseded" };
  }
  return { key: "decision", name, state: "current", detail: status ?? "pending" };
}

/** Stage 4 — whether the decision is chained.
 *
 *  Four outcomes, all of them read: chained (hash shown), nothing to chain,
 *  chained but not readable by this account, and the anomaly where the verdict
 *  says adjudicated and no chain row answers to it. */
function chainStage(
  status: string | null,
  mayReadChain: boolean,
  chainRow: LedgerEntry | null,
  ledgerError: string | null,
): Stage {
  const name = "4 · signed into the chain";
  if (status !== "adjudicated") {
    return { key: "chain", name, state: "waiting", detail: "nothing signed yet" };
  }
  if (!mayReadChain) {
    return { key: "chain", name, state: "unreadable", detail: "needs ledger:verify" };
  }
  if (ledgerError !== null) {
    return { key: "chain", name, state: "unreadable", detail: "ledger unreadable" };
  }
  if (chainRow === null) {
    return { key: "chain", name, state: "unreadable", detail: "no chain row found" };
  }
  return {
    key: "chain",
    name,
    state: "complete",
    detail: chainRow.row_hash.slice(0, 12),
  };
}
