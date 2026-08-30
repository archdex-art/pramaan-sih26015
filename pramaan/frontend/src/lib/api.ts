/**
 * API types and fetchers.
 *
 * These types mirror the FastAPI response models. They are hand-written for now
 * and will be replaced by `openapi-typescript` output at T18 (docs §30) — a
 * hand-written client drifts, and the generator exists precisely so it cannot.
 * Until the API surface stops moving, generating on every change costs more
 * than it saves.
 */

import { authFetch } from "./auth";

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

/** Exported so a screen can own its own response types without every new
 *  endpoint having to widen this module. The auth-retry and error-shaping
 *  behaviour must stay in exactly one place, which is why the helper is
 *  shared rather than the pattern being copied. */
export async function get<T>(path: string): Promise<T> {
  // Auth-aware fetch that injects the access token and retries once on 401.
  const response = await authFetch(path);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
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

// --- adjudication (S9) ----------------------------------------------------

export type Decision = "accept" | "edit" | "reject";

export interface AdjudicationResult {
  id: number;
  verdict_id: number;
  decision: string;
  corrected_level: string | null;
  reason: string | null;
  decided_at: string;
  signed_by_username: string;
  signed_by_name: string;
  prev_hash: string;
  row_hash: string;
}

export interface ChainReport {
  valid: boolean;
  rows: number;
  broken_at: number | null;
  reason: string | null;
  statement: string;
}

export interface LedgerEntry {
  id: number;
  verdict_id: number;
  decision: string;
  corrected_level: string | null;
  reason: string | null;
  decided_at: string;
  signed_by_username: string;
  signed_by_name: string;
  row_hash: string;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await authFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const b: unknown = await response.json();
      if (b && typeof b === "object" && "detail" in b) detail = String(b.detail);
    } catch { /* non-JSON */ }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const adjudicate = (
  verdictId: number,
  body: { decision: Decision; corrected_level?: string; reason?: string },
): Promise<AdjudicationResult> =>
  post(`/api/v1/verdicts/${verdictId}/adjudicate`, body);

export const fetchChainReport = (): Promise<ChainReport> =>
  get("/api/v1/ledger/verify");

export const fetchLedger = (): Promise<LedgerEntry[]> =>
  get("/api/v1/ledger");

// --- verification queue (FR-10) -------------------------------------------

/** One entry of the ranked verification queue, mirroring `AlertOut` in
 *  `app/api/v1/alerts.py` field for field.
 *
 *  `level` and `label` arrive as plain strings on the wire but are produced by
 *  the engine's own `Level` enum and `label_for`, so they are typed to the same
 *  unions the register uses. Typing them as `string` would let a screen render
 *  a chip with no ladder colour and never be told. */
export interface Alert {
  claim_id: number;
  verdict_id: number;
  unique_id: string;
  intervention_type: string;
  district_lgd: string;
  level: Level;
  label: Label;
  confidence: number;
  data_sufficiency: number;
  /** 1 is the most urgent. Computed by `services/alerts/priority.rank`. */
  priority: number;
  reason: string;
  recommended_action: string;
  adjudicated: boolean;
}

export interface AlertSummary {
  /** Carries every alert level the engine knows, *including* the zeroes. A
   *  missing key would be indistinguishable from a band that does not exist. */
  by_level: Record<string, number>;
  total: number;
  unadjudicated: number;
  /** `null`, not an empty string and not a reassuring sentence, when the queue
   *  is empty. The screen decides how to say "nothing is queued". */
  highest_priority_reason: string | null;
}

export const fetchAlerts = (limit = 100): Promise<Alert[]> =>
  get(`/api/v1/alerts?limit=${String(limit)}`);

export const fetchAlertSummary = (): Promise<AlertSummary> =>
  get("/api/v1/alerts/summary");

// --- administration -------------------------------------------------------

/** Mirrors `UserOut`. The scope fields are mutually exclusive in practice —
 *  a district officer has `scope_district`, a state officer `scope_state`, and
 *  a national officer neither — but the API reports both rather than a single
 *  pre-formatted string, so the UI can say "national" in its own words. */
export interface AdminUser {
  username: string;
  full_name: string;
  role: string;
  workspace: string;
  scope_state: string | null;
  scope_district: string | null;
  is_active: boolean;
  last_login_at: string | null;
  failed_attempts: number;
  locked_until: string | null;
}

/** DEM readiness for one district. `missing_tiles` is the field that matters:
 *  a district whose DEM does not cover its claims cannot produce terrain
 *  evidence, and that must be visible rather than inferred from a low
 *  coverage number. */
export interface DemStatus {
  derivatives_present: boolean;
  derivatives: string[];
  tiles: string[];
  missing_tiles: string[];
  covers_claim_extent: boolean;
}

export interface AdminDistrict {
  district_lgd: string;
  claim_count: number;
  verdict_count: number;
  adjudicated_count: number;
  intervention_types: string[];
  dem: DemStatus;
}

/** One subsystem table and whether anything has been written to it. `populated`
 *  is the engine's own answer, not `row_count > 0` re-derived here. */
export interface TableCount {
  table: string;
  row_count: number;
  populated: boolean;
}

export interface AdminSystem {
  engine_version: string;
  offline_mode: boolean;
  claims: number;
  verdicts: number;
  adjudications: number;
  users: number;
  ledger_rows: number;
  /** Result of re-hashing the adjudication chain, computed on this request. */
  ledger_valid: boolean;
  subsystems: TableCount[];
}

/** A recorded external-source verification, not a live probe. `status` is the
 *  string the check recorded — `SKIPPED_NO_CREDENTIALS` is a real outcome and
 *  is displayed as such. */
export interface DataSource {
  key: string;
  name: string;
  purpose: string;
  url: string;
  licence: string;
  status: string;
  detail: string;
  elapsed_ms: number | null;
  checked_at: string | null;
}

export const fetchAdminUsers = (): Promise<AdminUser[]> =>
  get("/api/v1/admin/users");

export const fetchAdminDistricts = (): Promise<AdminDistrict[]> =>
  get("/api/v1/admin/districts");

export const fetchAdminSystem = (): Promise<AdminSystem> =>
  get("/api/v1/admin/system");

export const fetchDataSources = (): Promise<DataSource[]> =>
  get("/api/v1/admin/data-sources");
