/**
 * S7 — the temporal analysis chart (docs §24 S7, the hero screen).
 *
 * The bold line is the site. The shaded ribbon is the control band. The hatched
 * band is the construction period, which is excluded from both windows. When the
 * site line leaves the ribbon and stays out, the story is readable without any
 * training; when it stays inside, that is equally readable, and it is the case
 * that pays for the product.
 *
 * ## Why hand-rolled SVG rather than Observable Plot
 *
 * docs §20.1 chose Plot for this chart. Three requirements here are awkward in
 * it and trivial in SVG: a hatched exclusion band (needs a `<pattern>` def), a
 * per-season ribbon that is *not* a continuous area (the band is defined only
 * between paired seasons), and sufficiency ticks on a second baseline. Plot
 * would need custom marks and raw-SVG escapes for all three, which is more
 * code than the axes it saves. Recorded as a deviation from §20.1 rather than
 * left for a reader to wonder about.
 *
 * ## The honesty requirement
 *
 * `control_available` false means the ribbon was not built from
 * covariate-matched controls. The chart then draws it dashed and labels it on
 * the plot — not in a tooltip. A shaded band that looks like matched controls
 * but is not would claim more than the data supports.
 */

import { useMemo, useState } from "react";
import type { ControlBand, SeasonPoint, TemporalComparison } from "../../lib/api";

const WIDTH = 960;
const HEIGHT = 460;
const MARGIN = { top: 24, right: 28, bottom: 88, left: 60 } as const;
const PLOT_W = WIDTH - MARGIN.left - MARGIN.right;
const PLOT_H = HEIGHT - MARGIN.top - MARGIN.bottom;

/** Season tints. Deliberately desaturated: the site line must dominate. */
const SEASON_FILL: Record<string, string> = {
  kharif: "#eaeef2",
  rabi: "#e9f0ea",
  summer: "#f5f0e6",
};

/** Fractional position of each season within its year, for the x scale. */
const SEASON_OFFSET: Record<string, number> = { kharif: 0.6, rabi: 0.95, summer: 0.3 };

interface Props {
  data: TemporalComparison;
}

/** A season observation placed on a continuous time axis. */
interface Placed {
  point: SeasonPoint;
  /** Decimal year. Rabi straddles the calendar year, so it sits late. */
  t: number;
  site: number | null;
}

/**
 * Split into one run per season, breaking across unobserved points.
 *
 * **Per season, not one line through everything.** A single line joining rabi
 * (~0.58) to summer (~0.28) and back produces a sawtooth that hides the trend
 * completely — the first render of this chart did exactly that, and the numeric
 * checks all passed while the picture was unreadable. It is also wrong in
 * principle: docs §17.2 states a cross-season delta is a category error and the
 * engine physically cannot construct one, so a line segment implying rabi-to-
 * summer change asserts something the analysis refuses to.
 *
 * A null site value breaks the run. Interpolating would draw a confident
 * segment through a season nobody could see — the "absence of evidence read as
 * evidence" defect the engine refuses elsewhere.
 */
function seasonRuns(placed: Placed[]): { season: string; run: Placed[] }[] {
  const seasons = [...new Set(placed.map((p) => p.point.season))];
  const out: { season: string; run: Placed[] }[] = [];

  for (const season of seasons) {
    const items = placed
      .filter((p) => p.point.season === season)
      .sort((a, b) => a.t - b.t);
    let current: Placed[] = [];
    for (const item of items) {
      if (item.site === null) {
        if (current.length > 0) out.push({ season, run: current });
        current = [];
      } else {
        current.push(item);
      }
    }
    if (current.length > 0) out.push({ season, run: current });
  }
  return out;
}

export function TemporalControlChart({ data }: Props) {
  const [hover, setHover] = useState<Placed | null>(null);

  const placed = useMemo<Placed[]>(
    () =>
      data.series.map((point) => ({
        point,
        // Rabi straddles the calendar year, so it sits late within its year.
        t: point.year + (SEASON_OFFSET[point.season] ?? 0.5),
        site: point.site,
      })),
    [data.series],
  );
  const observed = placed.filter((p) => p.site !== null);

  const scales = useMemo(() => {
    if (observed.length === 0) return null;
    const ts = observed.map((p) => p.t);
    const values = observed.map((p) => p.site as number);
    // Include each ribbon's extents so it is never clipped. The anchor must be
    // the band's *own* PRE observation, not the global minimum: anchoring every
    // band to the series minimum put the rabi ribbon at y = -1.39, above the
    // plot, because rabi sits ~0.22 above summer. Caught by reading the
    // rendered path coordinates, which is the only place it was visible.
    for (const band of data.bands) {
      const anchor = placed.find(
        (p) => p.point.year === band.pre_year && p.point.season === band.season,
      );
      if (anchor?.site == null) continue;
      values.push(anchor.site + band.control_p10, anchor.site + band.control_p90);
    }
    const t0 = Math.min(...ts) - 0.35;
    const t1 = Math.max(...ts) + 0.35;
    const v0 = Math.min(...values);
    const v1 = Math.max(...values);
    const pad = (v1 - v0) * 0.12 || 0.05;
    const x = (t: number) => ((t - t0) / (t1 - t0)) * PLOT_W;
    const y = (v: number) =>
      PLOT_H - ((v - (v0 - pad)) / (v1 + pad - (v0 - pad))) * PLOT_H;
    return { x, y, t0, t1, v0: v0 - pad, v1: v1 + pad };
  }, [observed, placed, data.bands]);

  if (scales === null) {
    return (
      <div className="chart-empty">
        <strong>No usable seasonal observations.</strong>
        <p>
          Nothing was measured for this claim, so there is no series to draw.
          This is not a value of zero.
        </p>
      </div>
    );
  }

  const { x, y } = scales;
  const claimT = decimalYear(data.claimed_date);
  const construction = constructionBand(data.windows);

  return (
    <figure className="chart">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`${data.index} at the site against its control band, ${data.series.length} seasons`}
      >
        <defs>
          <pattern
            id="hatch"
            width="7"
            height="7"
            patternTransform="rotate(45)"
            patternUnits="userSpaceOnUse"
          >
            <line x1="0" y1="0" x2="0" y2="7" stroke="#b08968" strokeWidth="1.6" />
          </pattern>
        </defs>

        <g transform={`translate(${MARGIN.left},${MARGIN.top})`}>
          {/* Season tints, one rect per observation, behind everything. */}
          {placed.map((p) => (
            <rect
              key={`${p.point.year}-${p.point.season}`}
              x={x(p.t - 0.22)}
              y={0}
              width={Math.max(x(p.t + 0.22) - x(p.t - 0.22), 2)}
              height={PLOT_H}
              fill={SEASON_FILL[p.point.season] ?? "#eee"}
              opacity={0.55}
            />
          ))}

          {/* Construction band: excluded from both windows, so it is hatched
              rather than tinted — it is not a season, it is a hole. */}
          {construction && (
            <>
              <rect
                x={x(construction.from)}
                y={0}
                width={Math.max(x(construction.to) - x(construction.from), 2)}
                height={PLOT_H}
                fill="url(#hatch)"
                opacity={0.5}
              />
              <text
                className="band-label"
                x={(x(construction.from) + x(construction.to)) / 2}
                y={14}
                textAnchor="middle"
              >
                construction — excluded
              </text>
            </>
          )}

          <YAxis y={y} v0={scales.v0} v1={scales.v1} label={data.index} />
          <XAxis x={x} placed={placed} />

          {/* Control ribbon, per paired season. Anchored on the site's PRE
              value so the band shows where the site *would* have gone had it
              behaved like its controls. */}
          {data.bands.map((band) => (
            <ControlRibbon
              key={band.season}
              band={band}
              placed={placed}
              x={x}
              y={y}
              dashed={!data.control_available}
            />
          ))}

          {/* One bold line per season, broken across unobserved points. Rabi
              and summer are separate series because only same-season change is
              constructable (docs §17.2). */}
          {seasonRuns(placed).map(({ season, run }, i) => (
            <g key={`${season}-${i}`} className={`site season-${season}`}>
              <path
                className="site-line"
                d={run
                  .map(
                    (p, j) =>
                      `${j === 0 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.site as number).toFixed(1)}`,
                  )
                  .join(" ")}
              />
              <text
                className="season-label"
                x={x(run[run.length - 1]!.t) + 8}
                y={y(run[run.length - 1]!.site as number) + 3}
              >
                {season}
              </text>
            </g>
          ))}

          {placed
            .filter((p) => p.site !== null)
            .map((p) => (
              <circle
                key={`${p.point.year}-${p.point.season}`}
                className="site-dot"
                cx={x(p.t)}
                cy={y(p.site as number)}
                r={hover === p ? 6 : 4}
                onMouseEnter={() => setHover(p)}
                onMouseLeave={() => setHover(null)}
              />
            ))}

          {/* Claim date. */}
          <line
            className="claim-line"
            x1={x(claimT)}
            y1={0}
            x2={x(claimT)}
            y2={PLOT_H}
          />
          <text className="claim-label" x={x(claimT) + 5} y={PLOT_H - 6}>
            claimed complete
          </text>

          <SufficiencyTicks placed={placed} x={x} />
        </g>
      </svg>

      <figcaption>
        {!data.control_available && (
          <p className="warn">
            <strong>Control band is provisional.</strong> {data.control_basis}. The
            ribbon is drawn dashed because these are not covariate-matched
            controls, so no control-differenced conclusion is drawn from it.
          </p>
        )}
        {Object.entries(data.excluded_seasons).map(([season, why]) => (
          <p key={season} className="note">
            <strong>{season} excluded.</strong> {why}
          </p>
        ))}
        {data.trend && (
          <p className="note">
            <strong>Trend: {data.trend.direction}</strong> over {data.trend.n}{" "}
            seasons
            {data.trend.p_value !== null && ` (p = ${data.trend.p_value.toFixed(3)})`}
            {data.trend.insufficient &&
              ` — fewer than ${data.trend.min_points_required ?? 5} points, so no trend is claimed`}
            .
          </p>
        )}
        <p className="note">
          {data.n_scenes_total} cloud-screened granules · {data.provenance}
        </p>
        <p className="note">
          This is not a causal claim. PRAMAAN's ceiling is L4
          (control-differenced).
        </p>
      </figcaption>

      {hover && <HoverCard placed={hover} index={data.index} />}
    </figure>
  );
}

function ControlRibbon({
  band,
  placed,
  x,
  y,
  dashed,
}: {
  band: ControlBand;
  placed: Placed[];
  x: (t: number) => number;
  y: (v: number) => number;
  dashed: boolean;
}) {
  const pre = placed.find(
    (p) => p.point.year === band.pre_year && p.point.season === band.season,
  );
  const post = placed.find(
    (p) => p.point.year === band.post_year && p.point.season === band.season,
  );
  if (!pre || !post || pre.site === null) return null;

  const base = pre.site;
  const yLo = y(base + band.control_p10);
  const yHi = y(base + band.control_p90);
  const x0 = x(pre.t);
  const x1 = x(post.t);

  return (
    <g className={dashed ? "ribbon provisional" : "ribbon"}>
      <path
        d={`M${x0},${y(base)} L${x1},${yHi} L${x1},${yLo} L${x0},${y(base)} Z`}
        className="ribbon-area"
      />
      <line x1={x0} y1={y(base)} x2={x1} y2={y(base + band.control_median_delta)} className="ribbon-median" />
      <text className="ribbon-label" x={x1 - 6} y={yHi - 7} textAnchor="end">
        {band.season} controls n={band.n_controls} ·{" "}
        {band.site_inside_control_band ? "site INSIDE band" : "site OUTSIDE band"}
      </text>
    </g>
  );
}

function YAxis({
  y,
  v0,
  v1,
  label,
}: {
  y: (v: number) => number;
  v0: number;
  v1: number;
  label: string;
}) {
  const ticks = niceTicks(v0, v1, 5);
  return (
    <g className="axis y-axis">
      {ticks.map((t) => (
        <g key={t} transform={`translate(0,${y(t)})`}>
          <line x1={0} x2={PLOT_W} className="gridline" />
          <text x={-10} dy="0.32em" textAnchor="end">
            {t.toFixed(2)}
          </text>
        </g>
      ))}
      <text className="axis-title" transform={`translate(-44,${PLOT_H / 2}) rotate(-90)`} textAnchor="middle">
        {label}
      </text>
    </g>
  );
}

function XAxis({ x, placed }: { x: (t: number) => number; placed: Placed[] }) {
  return (
    <g className="axis x-axis" transform={`translate(0,${PLOT_H})`}>
      <line x1={0} x2={PLOT_W} className="axis-line" />
      {placed.map((p) => (
        <text
          key={`${p.point.year}-${p.point.season}`}
          x={x(p.t)}
          y={16}
          textAnchor="middle"
        >
          {p.point.season.slice(0, 1).toUpperCase()}
          {String(p.point.year).slice(2)}
        </text>
      ))}
    </g>
  );
}

/**
 * Usable-scene fraction per season, on its own baseline.
 *
 * Present because uncertainty must be visible before it is read (docs §24.3):
 * a thin bar under a point tells the eye not to trust it without anyone having
 * to open a tooltip.
 */
function SufficiencyTicks({
  placed,
  x,
}: {
  placed: Placed[];
  x: (t: number) => number;
}) {
  const base = PLOT_H + 34;
  const maxH = 16;
  return (
    <g className="sufficiency">
      <text x={-10} y={base + maxH} textAnchor="end" className="suff-label">
        usable
      </text>
      {placed.map((p) => (
        <rect
          key={`${p.point.year}-${p.point.season}`}
          x={x(p.t) - 6}
          y={base + maxH - maxH * p.point.usable_fraction}
          width={12}
          height={Math.max(maxH * p.point.usable_fraction, 1)}
          className={p.point.usable_fraction < 0.35 ? "suff-bar low" : "suff-bar"}
        />
      ))}
    </g>
  );
}

function HoverCard({ placed, index }: { placed: Placed; index: string }) {
  const { point } = placed;
  return (
    <div className="hover-card">
      <div className="hover-head">
        {point.season} {point.year}
      </div>
      <dl>
        <dt>{index}</dt>
        <dd>{point.site === null ? "no usable pixels" : point.site.toFixed(4)}</dd>
        <dt>usable</dt>
        <dd>{(point.usable_fraction * 100).toFixed(0)}%</dd>
        <dt>scenes</dt>
        <dd>{point.n_scenes}</dd>
      </dl>
      <ul className="scene-ids">
        {point.scene_ids.map((id) => (
          <li key={id}>{id}</li>
        ))}
      </ul>
    </div>
  );
}

// --- scale helpers -------------------------------------------------------

function niceTicks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo;
  if (span <= 0) return [lo];
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  const start = Math.ceil(lo / step) * step;
  const out: number[] = [];
  for (let v = start; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** ISO date to decimal year, for placement on the same axis as the seasons. */
function decimalYear(iso: string): number {
  const d = new Date(`${iso}T00:00:00Z`);
  const year = d.getUTCFullYear();
  const start = Date.UTC(year, 0, 1);
  const end = Date.UTC(year + 1, 0, 1);
  return year + (d.getTime() - start) / (end - start);
}

function constructionBand(
  windows: Record<string, string | number>,
): { from: number; to: number } | null {
  const preEnd = windows["pre_end"];
  const postStart = windows["post_start"];
  if (typeof preEnd !== "string" || typeof postStart !== "string") return null;
  return { from: decimalYear(preEnd), to: decimalYear(postStart) };
}
