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
  // `no-store`: every one of these reads mutable state. A register showing a
  // stale verdict after a reconciliation has run is worse than a slow one, and
  // the browser did exactly that during development — the DB held new rows and
  // the table kept rendering the previous response.
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
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

// --- claims register (S1) and evidence tree (S2) ------------------------

export type Provenance = "measured" | "golden";
export type Direction = "agrees" | "neutral" | "disagrees" | "unavailable";

export interface RegisterRow {
  claim_id: number;
  unique_id: string;
  intervention_type: string;
  asserted_date: string | null;
  district_lgd: string;
  lat: number;
  lon: number;
  uncertainty_m: number | null;
  detectability: string | null;
  expected_footprint_m2: number | null;
  verdict_id: number | null;
  version: number | null;
  level: Level | null;
  label: Label | null;
  score: number | null;
  confidence: number | null;
  coverage: number | null;
  data_sufficiency: number | null;
  status: string | null;
  rule_path: string[];
  dissent_count: number;
  families_available: number;
  families_total: number;
  /** Rendered as a badge at chip size, never as a footnote. */
  provenance: Provenance;
  provisional: boolean;
}

export interface EvidenceEntry {
  family: string;
  /** Null when unavailable. Never 0.0 — that would read as "measured, neutral". */
  agreement: number | null;
  available: boolean;
  reason: string;
  cluster_scale: boolean;
  lineage: Record<string, unknown>;
  direction: Direction;
}

export interface EvidenceTree {
  claim_id: number;
  entries: EvidenceEntry[];
  families_available: number;
  families_total: number;
}

/** Shapes mirror `/api/v1/method/*` exactly — verified against the live
 *  response rather than assumed, because the Method drawer's entire purpose is
 *  that the interface cannot disagree with the engine. */
export interface Ladder {
  levels: string[];
  ceiling: string;
  refused: Record<string, string>;
  n3_paths: Record<string, string>;
}

export interface Weights {
  engine_version: string;
  config_fingerprint: string;
  families: string[];
  independent_families: string[];
  weights: Record<string, number>;
  weight_sum: number;
  formula: Record<string, string>;
}

export const fetchClaims = (): Promise<RegisterRow[]> => get("/api/v1/claims");

export const fetchEvidence = (claimId: number): Promise<EvidenceTree> =>
  get(`/api/v1/claims/${claimId}/evidence`);

export const fetchLadder = (): Promise<Ladder> => get("/api/v1/method/ladder");

export const fetchWeights = (): Promise<Weights> => get("/api/v1/method/weights");

// --- plan-view map layers (S3) -----------------------------------------

/** One D8 step from a stream cell to its downstream neighbour. */
export interface DrainageSegment {
  from: [number, number];
  to: [number, number];
  /** Strahler order — the value the terrain rule tests. Drives line weight. */
  order: number;
}

export interface MapControlPoint {
  control_id: string;
  lonlat: [number, number];
  slope_deg: number;
  elevation_m: number;
  dist_to_stream_m: number;
  dist_from_site_m: number;
}

export interface PlanMap {
  claim_id: number;
  unique_id: string;
  intervention_type: string;
  level: string | null;
  confidence: number | null;
  uncertainty_m: number | null;
  expected_footprint_m2: number | null;
  aoi: [number, number, number, number];
  window: [number, number, number, number];
  site: {
    lonlat: [number, number];
    strahler_order: number;
    dist_to_stream_m: number;
    slope_deg: number;
  };
  controls: MapControlPoint[];
  drainage: DrainageSegment[];
  provenance: Record<string, string>;
}

export const fetchMap = (claimId: number): Promise<PlanMap> =>
  get<PlanMap>(`/api/v1/claims/${claimId}/map`);
