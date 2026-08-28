/**
 * API types and fetchers.
 *
 * These types mirror the FastAPI response models. They are hand-written for now
 * and will be replaced by `openapi-typescript` output at T18 (docs §30) — a
 * hand-written client drifts, and the generator exists precisely so it cannot.
 * Until the API surface stops moving, generating on every change costs more
 * than it saves.
 */

/** Epistemic level. Level says *how strongly known*; label says *what*. */
export type Level =
  | "L0_recorded"
  | "L1_observed"
  | "L2_corroborated"
  | "L3_multi_indicator"
  | "L4_control_differenced"
  | "N1_inconclusive"
  | "N2_unsupported"
  | "N3_contradicted";

export type Label =
  | "CORROBORATED"
  | "PARTIAL"
  | "INCONCLUSIVE"
  | "UNSUPPORTED"
  | "CONTRADICTED";

export interface Verdict {
  id: number;
  claim_id: number;
  version: number;
  level: Level;
  label: Label;
  rule_path: string[];
  score: number;
  confidence: number;
  coverage: number;
  quality: number | null;
  data_sufficiency: number;
  /** Never empty: a verdict without stated counter-evidence is not shippable. */
  dissent: string[];
  recommended_action: { action: string; priority: number | null };
  engine_version: string;
  weights: Record<string, number>;
  status: string;
  provisional: boolean;
  note: string;
}

export interface SeasonPoint {
  year: number;
  season: string;
  /** Null when no usable pixels — a gap, never a zero. */
  site: number | null;
  controls: (number | null)[];
  usable_fraction: number;
  n_scenes: number;
  scene_ids: string[];
}

export interface ControlBand {
  season: string;
  pre_year: number;
  post_year: number;
  site_delta: number;
  control_median_delta: number;
  control_p10: number;
  control_p90: number;
  differenced_estimate: number;
  site_inside_control_band: boolean;
  n_controls: number;
}

export interface Trend {
  direction: string;
  insufficient: boolean;
  n: number;
  slope_per_year: number | null;
  p_value: number | null;
  min_points_required: number | null;
}

export interface TemporalComparison {
  claim_id: number;
  intervention_unique_id: string;
  intervention_type: string;
  index: string;
  claimed_date: string;
  windows: Record<string, string | number>;
  series: SeasonPoint[];
  bands: ControlBand[];
  trend: Trend | null;
  excluded_seasons: Record<string, string>;
  /** False when controls were not covariate-matched. The chart must say so. */
  control_available: boolean;
  control_basis: string;
  temporal_agreement: number;
  n_scenes_total: number;
  provenance: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    // Surface the API's own reason. A generic "failed to load" would hide
    // "reconciliation has not run", which is a different problem with a
    // different fix and the API already says which one it is.
    let detail = response.statusText;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        // `in` narrows body.detail to unknown; no assertion needed.
        detail = String(body.detail);
      }
    } catch {
      // Non-JSON error body; statusText is the best available.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const fetchTemporal = (claimId: number): Promise<TemporalComparison> =>
  get(`/api/v1/claims/${claimId}/temporal`);

export const fetchVerdict = (claimId: number): Promise<Verdict> =>
  get(`/api/v1/claims/${claimId}/verdict`);
