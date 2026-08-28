/**
 * The location uncertainty disk, drawn to scale against the 30 m pixel grid.
 *
 * This tiny diagram is the most under-rated thing in the product. Two of the
 * system's central claims are invisible as text and obvious as a picture:
 *
 *   1. "We never sample a single pixel." The disk visibly straddles pixel
 *      boundaries, so reading one pixel is self-evidently a choice about which
 *      of several to believe.
 *   2. "A 625 m² farm pond is smaller than a 900 m² pixel." Drawing the
 *      expected footprint at the same scale makes the detectability gate a
 *      thing you can see rather than a rule you are asked to accept.
 *
 * Scale is real: `PX_M` metres per grid cell, and the disk radius is
 * `max(gps_accuracy, 15)` m per docs §16.2 STEP 2.
 */

const PX_M = 30;
const MIN_DISK_M = 15;

interface Props {
  /** Recorded GPS accuracy in metres. */
  accuracyM: number | null;
  /** Expected footprint in m², drawn as a square at the same scale. */
  footprintM2?: number | null;
  /** Half-width of the view in metres. */
  extentM?: number;
  size?: number;
}

export function UncertaintyDisk({
  accuracyM,
  footprintM2,
  extentM = 48,
  size = 168,
}: Props) {
  const radiusM = Math.max(accuracyM ?? MIN_DISK_M, MIN_DISK_M);
  const mToPx = size / (extentM * 2);
  const c = size / 2;
  const rPx = radiusM * mToPx;
  const cell = PX_M * mToPx;

  // Footprint as a square of equal area — the real shape is unknown, and a
  // square of the right area is the honest representation of "about this big".
  const footSide = footprintM2 && footprintM2 > 0 ? Math.sqrt(footprintM2) * mToPx : 0;

  const lines: number[] = [];
  for (let x = c % cell; x < size; x += cell) lines.push(x);

  return (
    <figure className="disk">
      <svg
        width={size}
        height={size}
        role="img"
        aria-label={`Location uncertainty disk of radius ${radiusM.toFixed(0)} metres against a 30 metre pixel grid`}
      >
        <rect width={size} height={size} fill="var(--paper-3)" />
        {lines.map((v) => (
          <g key={v} stroke="var(--rule-2)" strokeWidth="0.75">
            <line x1={v} y1={0} x2={v} y2={size} />
            <line x1={0} y1={v} x2={size} y2={v} />
          </g>
        ))}

        {footSide > 0 && (
          <rect
            x={c - footSide / 2}
            y={c - footSide / 2}
            width={footSide}
            height={footSide}
            fill="none"
            stroke="var(--accent)"
            strokeWidth="1.4"
            strokeDasharray="3 2"
          />
        )}

        <circle
          cx={c}
          cy={c}
          r={rPx}
          fill="var(--n1)"
          fillOpacity="0.16"
          stroke="var(--n1)"
          strokeWidth="1.4"
        />
        <circle cx={c} cy={c} r={2} fill="var(--ink)" />
      </svg>
      <figcaption className="label">
        disk r={radiusM.toFixed(0)} m · grid 30 m
        {footSide > 0 && ` · footprint ${Math.round(footprintM2 ?? 0)} m²`}
      </figcaption>
    </figure>
  );
}
