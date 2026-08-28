/**
 * S3 - the plan view. The thematic map product PS 26015 asks for.
 *
 * ## Why it looks like a survey plan and not like Google Maps
 *
 * There is no basemap. Two reasons, and the first is binding: §38 requires the
 * console to work with the network interface physically disabled, and a tile
 * request is exactly the dependency that fails on a venue network. The second
 * is that a slippy basemap makes this read as a consumer map product. A survey
 * plan reads as a record, which is what it is.
 *
 * ## Every layer is measured
 *
 * The drainage network is the D8 network extracted from six mosaicked NASADEM
 * tiles by the same WhiteboxTools chain, at the same calibrated threshold, that
 * produced the terrain verdict. Line weight is Strahler order - the value the
 * terrain rule actually tests, so the map weights what the analysis reads. The
 * control pins are the twelve sites `select_controls` chose. The uncertainty
 * disk and footprint square are drawn to the same scale as the terrain.
 *
 * This screen exists now, and did not before, because the geometry became real.
 */

import { useEffect, useMemo, useState } from "react";

import { ApiError, fetchMap, type PlanMap as PlanMapData } from "../lib/api";

/**
 * Asymmetric margins. The left margin is wide because latitude labels are
 * right-anchored into it and a symmetric pad clipped the leading digit — 19.102
 * rendered as "9.102", which is not a cosmetic defect on a map that claims to
 * be a survey record. The bottom margin carries the tick labels and, below
 * them, the scale bar.
 */
const PAD = { l: 52, r: 22, t: 22, b: 58 } as const;
const W = 760;

/**
 * Equirectangular with a cos(lat) correction on longitude, anchored at the
 * window centre. Over a 10 km window at 19 degrees N this is accurate to well
 * under a pixel, and unlike Web Mercator it states its own assumption.
 */
function projector(win: [number, number, number, number]) {
  const [w, s, e, n] = win;
  const k = Math.cos((((s + n) / 2) * Math.PI) / 180);
  const spanX = (e - w) * k;
  const spanY = n - s;
  const innerW = W - PAD.l - PAD.r;
  const innerH = innerW * (spanY / spanX);
  const H = Math.round(innerH + PAD.t + PAD.b);
  const scale = innerW / spanX;
  return {
    H,
    innerW,
    innerH,
    /** Metres per SVG unit - the scale bar and the disks both need this. */
    mPerUnit: (spanY * 111_320) / innerH,
    x: (lon: number) => PAD.l + (lon - w) * k * scale,
    y: (lat: number) => PAD.t + (n - lat) * scale,
  };
}

/** Strahler order to stroke width. Order 6 is the trunk; order 1 is a hairline. */
const weight = (order: number) => 0.35 + Math.min(order, 6) * 0.36;

type LayerKey = "drainage" | "controls" | "site" | "aoi";

export function PlanMap({
  claimId,
  onBack,
}: {
  claimId: number;
  onBack: () => void;
}) {
  const [data, setData] = useState<PlanMapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [on, setOn] = useState<Record<LayerKey, boolean>>({
    drainage: true,
    controls: true,
    site: true,
    aoi: true,
  });
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    void fetchMap(claimId).then(
      (d) => !cancelled && setData(d),
      (e: unknown) =>
        !cancelled && setError(e instanceof ApiError ? e.detail : String(e)),
    );
    return () => {
      cancelled = true;
    };
  }, [claimId]);

  const geom = useMemo(() => (data ? projector(data.window) : null), [data]);

  if (error !== null) {
    return (
      <section className="screen">
        <ScreenHead onBack={onBack} />
        <p className="empty">
          <strong>No plan view.</strong> {error}
        </p>
      </section>
    );
  }
  if (data === null || geom === null) {
    return (
      <section className="screen">
        <ScreenHead onBack={onBack} />
        <p className="empty">Loading measured geometry...</p>
      </section>
    );
  }

  const { x, y, H, innerW, innerH, mPerUnit } = geom;
  const [sLon, sLat] = data.site.lonlat;
  const [aw, as, ae, an] = data.aoi;

  // Drawn to scale against the drainage, not as a fixed-pixel marker. A pin
  // whose size means nothing is how a map lies about precision.
  const diskR = (data.uncertainty_m ?? 15) / mPerUnit;
  const footSide = Math.sqrt(data.expected_footprint_m2 ?? 0) / mPerUnit;

  // At watershed scale a 3,200 m² structure is about three SVG units across —
  // physically correct and visually absent. So the site gets a locator ring,
  // which is explicitly *not* to scale, and the legend says so and names the
  // screen that does draw it to scale. Silently inflating the disk instead
  // would be the map lying about precision.
  const toScaleVisible = footSide >= 6;

  // A round-metres scale bar sized to about a fifth of the frame.
  const targetM = innerW * mPerUnit * 0.2;
  const barM = [500, 1000, 2000, 5000].reduce((best, c) =>
    Math.abs(c - targetM) < Math.abs(best - targetM) ? c : best,
  );

  const orders = [...new Set(data.drainage.map((d) => d.order))].sort(
    (a, b) => a - b,
  );

  return (
    <section className="screen">
      <ScreenHead onBack={onBack} />

      <div className="plan-head">
        <h2>
          Plan view <span className="mono">{data.unique_id}</span>
        </h2>
        <div className="plan-toggles">
          {(
            [
              ["drainage", `Drainage (${String(data.drainage.length)})`],
              ["controls", `Matched controls (${String(data.controls.length)})`],
              ["site", "Intervention + uncertainty"],
              ["aoi", "Analysis AOI"],
            ] as [LayerKey, string][]
          ).map(([k, label]) => (
            <label key={k} className="toggle">
              <input
                type="checkbox"
                checked={on[k]}
                onChange={(ev) =>
                  setOn((prev) => ({ ...prev, [k]: ev.target.checked }))
                }
              />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div className="plan-wrap">
        <svg
          className="plan"
          viewBox={`0 0 ${String(W)} ${String(H)}`}
          role="img"
          aria-label={`Plan view of ${data.unique_id}: extracted drainage network, ${String(data.controls.length)} matched control sites, and the intervention with its location uncertainty disk drawn to scale.`}
        >
          {/* Graticule. Ticks, not a grid - a full grid competes with the
              drainage, which is the layer that carries the argument. */}
          <g className="plan-frame">
            <rect
              x={PAD.l}
              y={PAD.t}
              width={innerW}
              height={innerH}
              fill="none"
            />
            {[0, 0.25, 0.5, 0.75, 1].map((f) => {
              const lon = data.window[0] + (data.window[2] - data.window[0]) * f;
              const lat = data.window[1] + (data.window[3] - data.window[1]) * f;
              const base = PAD.t + innerH;
              return (
                <g key={f}>
                  <line x1={x(lon)} y1={base} x2={x(lon)} y2={base + 5} />
                  <text className="tick" x={x(lon)} y={base + 15}>
                    {lon.toFixed(3)}
                  </text>
                  <line x1={PAD.l - 5} y1={y(lat)} x2={PAD.l} y2={y(lat)} />
                  <text className="tick tick-y" x={PAD.l - 8} y={y(lat) + 3}>
                    {lat.toFixed(3)}
                  </text>
                </g>
              );
            })}
          </g>

          <clipPath id="plan-clip">
            <rect x={PAD.l} y={PAD.t} width={innerW} height={innerH} />
          </clipPath>

          <g clipPath="url(#plan-clip)">
            {on.drainage && (
              <g className="plan-drainage">
                {data.drainage.map((seg, i) => (
                  <line
                    key={i}
                    x1={x(seg.from[0])}
                    y1={y(seg.from[1])}
                    x2={x(seg.to[0])}
                    y2={y(seg.to[1])}
                    strokeWidth={weight(seg.order)}
                  />
                ))}
              </g>
            )}

            {on.aoi && (
              <rect
                className="plan-aoi"
                x={x(aw)}
                y={y(an)}
                width={x(ae) - x(aw)}
                height={y(as) - y(an)}
              />
            )}

            {on.controls &&
              data.controls.map((c) => (
                <g
                  key={c.control_id}
                  className="plan-control"
                  onMouseEnter={() =>
                    setHover(
                      `${c.control_id} · slope ${c.slope_deg.toFixed(2)}° · ${c.elevation_m.toFixed(0)} m · ${c.dist_to_stream_m.toFixed(0)} m to stream · ${(c.dist_from_site_m / 1000).toFixed(2)} km from site`,
                    )
                  }
                  onMouseLeave={() => setHover(null)}
                >
                  <circle cx={x(c.lonlat[0])} cy={y(c.lonlat[1])} r={4} />
                </g>
              ))}

            {on.site && (
              <g className="plan-site">
                {/* Locator ring: fixed pixel size, explicitly not to scale.
                    Without it the intervention is three units wide on a
                    700-unit plan and the subject of the map is invisible. */}
                <circle
                  className="plan-locator"
                  cx={x(sLon)}
                  cy={y(sLat)}
                  r={11}
                />
                <circle
                  className="plan-disk"
                  cx={x(sLon)}
                  cy={y(sLat)}
                  r={Math.max(diskR, 0.8)}
                />
                {footSide > 0 && (
                  <rect
                    className="plan-foot"
                    x={x(sLon) - footSide / 2}
                    y={y(sLat) - footSide / 2}
                    width={footSide}
                    height={footSide}
                  />
                )}
                <circle className="plan-pin-dot" cx={x(sLon)} cy={y(sLat)} r={1.6} />
                <text className="plan-site-label" x={x(sLon) + 16} y={y(sLat) + 3}>
                  {data.intervention_type.replace(/_/g, " ")}
                </text>
              </g>
            )}
          </g>

          {/* Survey plan conventions. The scale bar sits in the bottom margin
              below the tick labels: inside the frame it collided with the
              order-1 drainage mesh and read as a stray channel. */}
          <g className="plan-north">
            {(() => {
              const nx = PAD.l + innerW - 9;
              const ny = PAD.t + 8;
              return (
                <>
                  <path d={`M ${String(nx)} ${String(ny + 15)} L ${String(nx)} ${String(ny)}`} />
                  <path
                    d={`M ${String(nx - 3.5)} ${String(ny + 5)} L ${String(nx)} ${String(ny)} L ${String(nx + 3.5)} ${String(ny + 5)}`}
                  />
                  <text className="tick" x={nx} y={ny + 25}>
                    N
                  </text>
                </>
              );
            })()}
          </g>
          <g className="plan-scale">
            {(() => {
              const bx = PAD.l;
              const by = PAD.t + innerH + 34;
              const bw = barM / mPerUnit;
              return (
                <>
                  <line x1={bx} y1={by} x2={bx + bw} y2={by} />
                  <line x1={bx} y1={by - 3.5} x2={bx} y2={by + 3.5} />
                  <line x1={bx + bw} y1={by - 3.5} x2={bx + bw} y2={by + 3.5} />
                  <text className="tick tick-scale" x={bx + bw + 7} y={by + 3}>
                    {barM >= 1000
                      ? `${String(barM / 1000)} km`
                      : `${String(barM)} m`}
                  </text>
                </>
              );
            })()}
          </g>
        </svg>

        <p className="plan-hover mono">{hover ?? "\u00a0"}</p>
      </div>

      <div className="plan-legend">
        <div>
          <h3>Drainage — Strahler order</h3>
          <ul className="legend-orders">
            {orders.map((o) => (
              <li key={o}>
                <svg width="34" height="10" aria-hidden="true">
                  <line
                    x1="1"
                    y1="5"
                    x2="33"
                    y2="5"
                    stroke="var(--ink-2)"
                    strokeWidth={weight(o)}
                  />
                </svg>
                <span className="mono">{o}</span>
              </li>
            ))}
          </ul>
          <p className="note">{data.provenance.drainage}</p>
        </div>
        <div>
          <h3>Site</h3>
          <dl className="metrics">
            <dt>Strahler order at site</dt>
            <dd className="mono">{data.site.strahler_order.toFixed(0)}</dd>
            <dt>Distance to stream</dt>
            <dd className="mono">
              {data.site.dist_to_stream_m.toFixed(0)} m
            </dd>
            <dt>Slope</dt>
            <dd className="mono">{data.site.slope_deg.toFixed(2)}°</dd>
            <dt>Location uncertainty</dt>
            <dd className="mono">{(data.uncertainty_m ?? 15).toFixed(0)} m</dd>
            <dt>Expected footprint</dt>
            <dd className="mono">
              {(data.expected_footprint_m2 ?? 0).toFixed(0)} m²
            </dd>
          </dl>
          <p className="note">
            <strong>Scale of the site marker.</strong>{" "}
            {toScaleVisible ? (
              <>
                The uncertainty disk and footprint square are drawn to the plan
                scale of {mPerUnit.toFixed(1)} m per unit. The ring around them
                is a fixed-size locator and is not to scale.
              </>
            ) : (
              <>
                At {mPerUnit.toFixed(1)} m per unit this structure is under three
                units across — physically correct and visually absent, so the
                ring is a fixed-size locator and is <em>not</em> to scale. The
                disk is drawn to scale against the 30 m pixel grid on the
                reconciliation screen, which is where the detectability gate is
                argued.
              </>
            )}
          </p>
          <p className="note">{data.provenance.controls}</p>
          <p className="note">
            <strong>Basemap:</strong> {data.provenance.basemap}
          </p>
        </div>
      </div>
    </section>
  );
}

function ScreenHead({ onBack }: { onBack: () => void }) {
  return (
    <div className="screen-head">
      <h1>Plan view</h1>
      <button className="btn" onClick={onBack}>
        Back to claim
      </button>
    </div>
  );
}
