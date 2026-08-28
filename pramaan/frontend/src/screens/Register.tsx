/**
 * S1 — the claims register.
 *
 * A dense table, one row per claim, with the level chip as the loudest element.
 *
 * ## Why golden cases are in here
 *
 * A register with one row looks broken, and padding it with invented claims
 * would be the most dishonest thing this product could do. The third option is
 * to seed the golden-case suite — 23 synthetic bundles whose verdicts are
 * computed by the same frozen engine — so the table demonstrates all eight
 * epistemic levels.
 *
 * Every row therefore carries a provenance badge at chip size. A screenshot of
 * this table forwarded without context must still say which rows were measured.
 */

import { useMemo, useState } from "react";
import type { Level, RegisterRow } from "../lib/api";

interface Props {
  rows: RegisterRow[];
  onOpen: (claimId: number) => void;
}

const LADDER: Level[] = [
  "L4_control_differenced",
  "L3_multi_indicator",
  "L2_corroborated",
  "L1_observed",
  "L0_recorded",
  "N1_inconclusive",
  "N2_unsupported",
  "N3_contradicted",
];

/**
 * Action bands. The ladder answers "how strong is the evidence"; a band answers
 * "so what does an officer do about it", which is the question a register of a
 * thousand claims is actually for.
 *
 * The mapping is not cosmetic grouping. N2/N3 and N1 are deliberately separate
 * bands because they demand opposite responses: an absent expected signature
 * with alternatives excluded is a site to go and stand on, whereas an
 * inconclusive verdict is a data problem and sending a jeep at it wastes the
 * trip. Collapsing them into one "needs attention" pile — which is what a
 * conventional RAG dashboard does — destroys exactly that distinction.
 */
const BANDS = [
  {
    key: "supported",
    label: "Corroborated",
    levels: ["L2_corroborated", "L3_multi_indicator", "L4_control_differenced"],
    action: "No field visit. Include in outcome reporting.",
  },
  {
    key: "recorded",
    label: "Recorded, not corroborated",
    levels: ["L0_recorded", "L1_observed"],
    action: "Confirm in the next observation cycle.",
  },
  {
    key: "unresolved",
    label: "Unresolved",
    levels: ["N1_inconclusive"],
    action: "Data problem, not a site problem. Re-assess, do not dispatch.",
  },
  {
    key: "flagged",
    label: "Flagged",
    levels: ["N2_unsupported", "N3_contradicted"],
    action: "Requires physical verification, priority-ranked.",
  },
] as const;

type BandKey = (typeof BANDS)[number]["key"];

const shortLevel = (level: string) => level.split("_")[0] ?? level;

export function Register({ rows, onOpen }: Props) {
  const [level, setLevel] = useState<string>("all");
  const [prov, setProv] = useState<string>("all");
  const [band, setBand] = useState<BandKey | "all">("all");

  const bandLevels = useMemo(
    () => BANDS.find((b) => b.key === band)?.levels as readonly string[] | undefined,
    [band],
  );

  const shown = useMemo(
    () =>
      rows
        .filter((r) => level === "all" || r.level === level)
        .filter((r) => prov === "all" || r.provenance === prov)
        .filter((r) => !bandLevels || (r.level !== null && bandLevels.includes(r.level)))
        // Down the ladder, strongest first, then by confidence. Ordering by
        // confidence alone would put a high-confidence N3 above an L4.
        .sort((a, b) => {
          const ai = LADDER.indexOf(a.level as Level);
          const bi = LADDER.indexOf(b.level as Level);
          if (ai !== bi) return ai - bi;
          return (b.confidence ?? 0) - (a.confidence ?? 0);
        }),
    [rows, level, prov, bandLevels],
  );

  const counts = useMemo(() => {
    const byLevel: Record<string, number> = {};
    for (const r of rows) if (r.level) byLevel[r.level] = (byLevel[r.level] ?? 0) + 1;
    return byLevel;
  }, [rows]);

  const measured = rows.filter((r) => r.provenance === "measured").length;

  /** Per-band totals, and how many of each came from real imagery. The second
   *  number is the one that stops the strip reading as a portfolio it is not. */
  const bandCounts = useMemo(
    () =>
      BANDS.map((b) => {
        const inBand = rows.filter(
          (r) => r.level !== null && (b.levels as readonly string[]).includes(r.level),
        );
        return {
          ...b,
          n: inBand.length,
          measured: inBand.filter((r) => r.provenance === "measured").length,
        };
      }),
    [rows],
  );

  return (
    <div className="screen">
      <header className="screen-head rise">
        <div>
          <h1>Claims register</h1>
          <p className="sub">
            {rows.length} claims · <strong>{measured} measured</strong> ·{" "}
            {rows.length - measured} golden-case · {Object.keys(counts).length} of 8
            epistemic levels present
          </p>
        </div>
      </header>

      {/* Triage strip. Bands are filters, not decoration — a summary number an
          officer cannot click is a number they have to re-derive by hand. */}
      <div className="triage rise">
        {bandCounts.map((b) => (
          <button
            key={b.key}
            type="button"
            className="triage-band"
            data-band={b.key}
            aria-pressed={band === b.key}
            onClick={() => setBand((cur) => (cur === b.key ? "all" : b.key))}
          >
            <span className="triage-n mono">{b.n}</span>
            <span className="triage-label">{b.label}</span>
            <span className="triage-levels mono">
              {b.levels.map(shortLevel).join(" · ")}
            </span>
            <span className="triage-action">{b.action}</span>
            <span className="triage-prov">
              {b.measured} measured · {b.n - b.measured} golden-case
            </span>
          </button>
        ))}
      </div>
      <p className="triage-note note">
        Bands group the ladder by the action they demand, not by severity.{" "}
        <strong>Unresolved is not a weak Flagged</strong> — an inconclusive
        verdict is a data gap and dispatching a field team at it wastes the trip,
        whereas a flagged claim is a site to go and stand on. Counts are over the
        {" "}
        {rows.length} claims in this register, of which {measured} came from real
        imagery.
      </p>

      <div className="filters rise">
        <Filter
          name="Level"
          value={level}
          onChange={setLevel}
          options={[
            ["all", `All (${rows.length})`],
            ...LADDER.filter((l) => counts[l]).map(
              (l) => [l, `${shortLevel(l)} · ${counts[l]}`] as [string, string],
            ),
          ]}
        />
        <Filter
          name="Provenance"
          value={prov}
          onChange={setProv}
          options={[
            ["all", "All"],
            ["measured", `Measured (${measured})`],
            ["golden", `Golden (${rows.length - measured})`],
          ]}
        />
      </div>

      <div className="table-wrap panel rise">
        <table className="register">
          <thead>
            <tr>
              <th>Unique ID</th>
              <th>Type</th>
              <th>Level</th>
              <th className="num">Score</th>
              <th className="num">Conf.</th>
              <th className="num">Cov.</th>
              <th className="num">Families</th>
              <th className="num">Dissent</th>
              <th>Provenance</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.claim_id} onClick={() => onOpen(r.claim_id)} tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && onOpen(r.claim_id)}
                  role="button"
                  aria-label={`Open ${r.unique_id}`}>
                <td className="mono id">{r.unique_id}</td>
                <td>{r.intervention_type.replace(/_/g, " ")}</td>
                <td>
                  {r.level ? (
                    <span className="chip" data-level={shortLevel(r.level)}>
                      {shortLevel(r.level)} {r.label}
                    </span>
                  ) : (
                    <span className="chip ghost">no verdict</span>
                  )}
                </td>
                <td className="num mono">{r.score?.toFixed(3) ?? "—"}</td>
                <td className="num mono">{r.confidence?.toFixed(3) ?? "—"}</td>
                <td className="num mono">{r.coverage?.toFixed(2) ?? "—"}</td>
                <td className="num mono">
                  {r.families_available}/{r.families_total}
                </td>
                <td className="num mono">{r.dissent_count}</td>
                <td>
                  <span className="badge" data-prov={r.provenance}>
                    {r.provenance}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {shown.length === 0 && (
          <p className="empty">No claims match this filter.</p>
        )}
      </div>

      <p className="foot-note rise">
        <strong>Golden-case rows are synthetic inputs with engine-computed
        verdicts</strong> — the 23 bundles that gate every commit. They are here
        because they exercise all eight levels; they are badged because a
        synthetic row must never be mistaken for a measurement.
      </p>
    </div>
  );
}

function Filter({
  name,
  value,
  onChange,
  options,
}: {
  name: string;
  value: string;
  onChange: (v: string) => void;
  options: [string, string][];
}) {
  return (
    <label className="filter">
      {/* A visible label, not a placeholder — the forms checklist is explicit. */}
      <span className="label">{name}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}
