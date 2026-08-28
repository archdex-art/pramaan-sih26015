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

const shortLevel = (level: string) => level.split("_")[0] ?? level;

export function Register({ rows, onOpen }: Props) {
  const [level, setLevel] = useState<string>("all");
  const [prov, setProv] = useState<string>("all");

  const shown = useMemo(
    () =>
      rows
        .filter((r) => level === "all" || r.level === level)
        .filter((r) => prov === "all" || r.provenance === prov)
        // Down the ladder, strongest first, then by confidence. Ordering by
        // confidence alone would put a high-confidence N3 above an L4.
        .sort((a, b) => {
          const ai = LADDER.indexOf(a.level as Level);
          const bi = LADDER.indexOf(b.level as Level);
          if (ai !== bi) return ai - bi;
          return (b.confidence ?? 0) - (a.confidence ?? 0);
        }),
    [rows, level, prov],
  );

  const counts = useMemo(() => {
    const byLevel: Record<string, number> = {};
    for (const r of rows) if (r.level) byLevel[r.level] = (byLevel[r.level] ?? 0) + 1;
    return byLevel;
  }, [rows]);

  const measured = rows.filter((r) => r.provenance === "measured").length;

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
