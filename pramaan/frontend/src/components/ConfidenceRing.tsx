/**
 * Confidence, as a ring.
 *
 * The one piece of scripted motion in the product: the arc draws once on mount
 * over 520 ms. It is allowed because it encodes the value rather than
 * decorating it — a short arc *looks* like low confidence before the number is
 * read, which is the whole point of showing uncertainty visually (docs §24.3).
 *
 * The ring is coloured by **level**, not by confidence. Confidence 0.9 on an N3
 * verdict is high confidence in a negative finding; colouring the ring by
 * magnitude would paint that green and invert the meaning.
 */

interface Props {
  /** 0–1. */
  confidence: number;
  /** Epistemic level, used only for the hue. */
  level: string | null;
  size?: number;
}

export function ConfidenceRing({ confidence, level, size = 92 }: Props) {
  const stroke = 7;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(1, confidence));
  const shown = circumference * clamped;

  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} aria-hidden="true">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--rule)"
          strokeWidth={stroke}
        />
        <circle
          className="ring-arc"
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`var(--${(level ?? "l0").slice(0, 2).toLowerCase()})`}
          strokeWidth={stroke}
          strokeLinecap="butt"
          strokeDasharray={`${shown} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ ["--arc" as string]: `${shown}` }}
        />
      </svg>
      {/* The number is the accessible value; the ring is decoration on top of
          it, so the figure carries the aria label rather than the svg. */}
      <div className="ring-value mono" role="img" aria-label={`confidence ${clamped.toFixed(4)}`}>
        <strong>{clamped.toFixed(2)}</strong>
        <span className="label">conf</span>
      </div>
    </div>
  );
}
