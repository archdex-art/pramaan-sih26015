"""Baseline schema: hierarchy, programme, images, evidence, verdicts, ledger.

Revision ID: 0001_baseline
Revises:
Create Date: (Stage 0)

Transcribes the DDL in docs §22.2, with two corrections applied on top of the
master design doc:

1. `evidence_family` is the frozen six-family enum from ADR-001 (§14.4/§16.1)
   — `metadata` is not a member; it lives in `metadata_integrity` columns.
2. Partitioning (the W7 fix) is applied only to `evidence` (LIST by
   district_lgd) and `audit_log` (RANGE by month) — the two high-volume
   tables with **no incoming foreign key** on their `id` column. `verdicts`
   and `field_images` are FK targets (`adjudications`, `alerts`,
   `image_analysis`); partitioning them would force every referencing table
   onto a composite foreign key across the partition key. That cost is not
   justified at hackathon/pilot row counts. Documented, not silently dropped
   — see docs §22.3.
"""

from __future__ import annotations

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

DDL_UP = """
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
  CREATE ROLE pramaan_app NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============ ORGANISATION & ACCESS ============
CREATE TYPE user_role AS ENUM ('dolr_admin','slna','wcdc','pia','wdt','readonly');

CREATE TABLE users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username       TEXT UNIQUE NOT NULL,
  full_name      TEXT NOT NULL,
  email          TEXT,
  password_hash  TEXT NOT NULL,
  role           user_role NOT NULL,
  scope_state    TEXT,
  scope_district TEXT,
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT scope_consistency CHECK (
    (role='dolr_admin' AND scope_state IS NULL) OR
    (role IN ('slna') AND scope_state IS NOT NULL) OR
    (role IN ('wcdc','pia','wdt','readonly'))
  )
);

-- ============ WATERSHED HIERARCHY (SLUSI codes) ============
CREATE TABLE watersheds (
  id        BIGSERIAL PRIMARY KEY,
  ws_code   TEXT UNIQUE NOT NULL,
  name      TEXT,
  geom      GEOMETRY(MultiPolygon, 4326) NOT NULL,
  area_ha   NUMERIC(12,2) GENERATED ALWAYS AS
              (ST_Area(ST_Transform(geom, 7755))/10000.0) STORED
);
CREATE INDEX idx_ws_geom ON watersheds USING GIST (geom);

CREATE TABLE sub_watersheds (
  id           BIGSERIAL PRIMARY KEY,
  sws_code     TEXT UNIQUE NOT NULL,
  watershed_id BIGINT NOT NULL REFERENCES watersheds(id),
  geom         GEOMETRY(MultiPolygon, 4326) NOT NULL,
  mean_rain_mm NUMERIC(8,2),
  agro_zone    TEXT
);
CREATE INDEX idx_sws_geom ON sub_watersheds USING GIST (geom);
CREATE INDEX idx_sws_ws   ON sub_watersheds (watershed_id);

CREATE TABLE micro_watersheds (
  id             BIGSERIAL PRIMARY KEY,
  mws_code       TEXT UNIQUE NOT NULL,
  sub_ws_id      BIGINT NOT NULL REFERENCES sub_watersheds(id),
  state_lgd      TEXT, district_lgd TEXT, block_lgd TEXT,
  geom           GEOMETRY(MultiPolygon, 4326) NOT NULL,
  analysis_srid  INTEGER NOT NULL,
  CONSTRAINT mws_valid CHECK (ST_IsValid(geom))
);
CREATE INDEX idx_mws_geom ON micro_watersheds USING GIST (geom);
CREATE INDEX idx_mws_dist ON micro_watersheds (district_lgd);

-- ============ PROGRAMME ============
CREATE TYPE project_phase AS ENUM ('preparatory','works','consolidation','closed');

CREATE TABLE projects (
  id                 BIGSERIAL PRIMARY KEY,
  project_code       TEXT UNIQUE NOT NULL,
  name               TEXT NOT NULL,
  mws_id             BIGINT REFERENCES micro_watersheds(id),
  state_lgd          TEXT NOT NULL, district_lgd TEXT NOT NULL,
  pia_name           TEXT,
  phase              project_phase NOT NULL DEFAULT 'preparatory',
  start_date         DATE, end_date DATE,
  sanctioned_area_ha NUMERIC(10,2),
  outlay_inr         NUMERIC(14,2),
  geom               GEOMETRY(MultiPolygon, 4326)
);
CREATE INDEX idx_proj_geom ON projects USING GIST (geom);

CREATE TYPE intervention_type AS ENUM (
  'check_dam','percolation_tank','farm_pond','nala_bund','earthen_bund',
  'contour_bund','contour_trench','staggered_trench','gully_plug',
  'plantation','horticulture','waterbody_renovation','dug_well','borewell',
  'recharge_shaft','livestock','livelihood','other');

CREATE TYPE work_status AS ENUM ('not_initiated','initiated','in_progress','completed');

CREATE TABLE interventions (
  id                     BIGSERIAL PRIMARY KEY,
  unique_id              TEXT UNIQUE NOT NULL,
  project_id             BIGINT NOT NULL REFERENCES projects(id),
  mws_id                 BIGINT REFERENCES micro_watersheds(id),
  district_lgd           TEXT NOT NULL,
  type                   intervention_type NOT NULL,
  status                 work_status NOT NULL DEFAULT 'not_initiated',
  planned_date           DATE, completed_date DATE,
  cost_inr               NUMERIC(12,2),
  village_lgd            TEXT, survey_no TEXT, beneficiary TEXT,
  geom                   GEOMETRY(Point, 4326) NOT NULL,
  command_geom           GEOMETRY(Polygon, 4326),
  expected_footprint_m2  NUMERIC(10,1),
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_int_geom   ON interventions USING GIST (geom);
CREATE INDEX idx_int_cmd    ON interventions USING GIST (command_geom);
CREATE INDEX idx_int_type   ON interventions (type);
CREATE INDEX idx_int_proj   ON interventions (project_id);
CREATE INDEX idx_int_status ON interventions (status, completed_date);
CREATE INDEX idx_int_dist   ON interventions (district_lgd);

-- ============ FIELD IMAGES (FK target — not partitioned; see module docstring) ============
CREATE TYPE coord_provenance AS ENUM ('exif_gps','sidecar_json','csv_row','manual_pin','unknown');

CREATE TABLE field_images (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  intervention_id    BIGINT REFERENCES interventions(id),
  district_lgd       TEXT,
  object_key         TEXT NOT NULL,
  derivative_key     TEXT,
  phash              BIGINT,
  captured_at        TIMESTAMPTZ,
  captured_at_source TEXT,
  geom               GEOMETRY(Point, 4326),
  gps_accuracy_m     NUMERIC(6,2),
  orientation_deg    NUMERIC(5,2),
  altitude_m         NUMERIC(7,2),
  coord_provenance   coord_provenance NOT NULL DEFAULT 'unknown',
  device_make        TEXT, device_model TEXT,
  width_px           INTEGER, height_px INTEGER,
  blur_score         NUMERIC(8,3),
  metadata_integrity NUMERIC(4,3),
  quality_flags      TEXT[],
  raw_exif           JSONB,
  uploaded_by        UUID REFERENCES users(id),
  uploaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT acc_sane CHECK (gps_accuracy_m IS NULL OR gps_accuracy_m BETWEEN 0 AND 10000)
);
CREATE INDEX idx_img_geom  ON field_images USING GIST (geom);
CREATE INDEX idx_img_int   ON field_images (intervention_id);
CREATE INDEX idx_img_phash ON field_images (phash);
CREATE INDEX idx_img_time  ON field_images (captured_at);
CREATE INDEX idx_img_dist  ON field_images (district_lgd);

CREATE TABLE image_analysis (
  id              BIGSERIAL PRIMARY KEY,
  image_id        UUID NOT NULL REFERENCES field_images(id) ON DELETE CASCADE,
  model_name      TEXT NOT NULL, model_version TEXT NOT NULL,
  labels          JSONB NOT NULL,
  scene_scale     TEXT,
  abstained       TEXT[],
  explanation_key TEXT,
  inferred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (image_id, model_name, model_version)
);
CREATE INDEX idx_ia_labels ON image_analysis USING GIN (labels);

-- ============ SATELLITE & RASTER ============
CREATE TABLE satellite_scenes (
  id            BIGSERIAL PRIMARY KEY,
  source        TEXT NOT NULL,
  scene_id      TEXT NOT NULL,
  sensed_at     TIMESTAMPTZ NOT NULL,
  cloud_pct     NUMERIC(5,2),
  gsd_m         NUMERIC(6,2) NOT NULL,
  footprint     GEOMETRY(MultiPolygon, 4326) NOT NULL,
  stac_href     TEXT,
  UNIQUE (source, scene_id)
);
CREATE INDEX idx_scene_fp   ON satellite_scenes USING GIST (footprint);
CREATE INDEX idx_scene_time ON satellite_scenes (sensed_at);

CREATE TABLE raster_layers (
  id           BIGSERIAL PRIMARY KEY,
  mws_id       BIGINT REFERENCES micro_watersheds(id),
  kind         TEXT NOT NULL,
  season       TEXT,
  year         SMALLINT,
  object_key   TEXT NOT NULL,
  srid         INTEGER NOT NULL,
  pixel_m      NUMERIC(6,2) NOT NULL,
  usable_frac  NUMERIC(4,3),
  scene_ids    BIGINT[],
  built_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_rl_lookup ON raster_layers (mws_id, kind, season, year);

-- ============ CLAIMS ============
CREATE TABLE claims (
  id                BIGSERIAL PRIMARY KEY,
  intervention_id   BIGINT NOT NULL REFERENCES interventions(id),
  district_lgd      TEXT NOT NULL,
  primary_image_id  UUID REFERENCES field_images(id),
  asserted_status   work_status NOT NULL,
  asserted_date     DATE NOT NULL,
  geom              GEOMETRY(Point, 4326) NOT NULL,
  uncertainty_m     NUMERIC(6,2) NOT NULL DEFAULT 15,
  detectability     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_claim_geom ON claims USING GIST (geom);
CREATE INDEX idx_claim_dist ON claims (district_lgd);

-- ============ EVIDENCE (partitioned by district — no incoming FK) ============
CREATE TYPE evidence_family AS ENUM ('terrain','satellite','temporal','photo','control','context');
-- Frozen by ADR-001 (§14.4/§16.1): exactly six families, `metadata` excluded.

CREATE TABLE evidence (
  id            BIGINT GENERATED ALWAYS AS IDENTITY,
  claim_id      BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  district_lgd  TEXT NOT NULL,
  family        evidence_family NOT NULL,
  agreement     NUMERIC(4,3) NOT NULL,
  available     BOOLEAN NOT NULL DEFAULT TRUE,
  payload       JSONB NOT NULL,
  lineage       JSONB NOT NULL,
  computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT agreement_range CHECK (agreement BETWEEN -1 AND 1),
  PRIMARY KEY (id, district_lgd),
  UNIQUE (claim_id, family, district_lgd)
) PARTITION BY LIST (district_lgd);

-- Demo partitions; district onboarding (§Stage 2 / T04) creates the rest via
-- `ATTACH PARTITION` in scripts/onboard_district.py — never manually.
CREATE TABLE evidence_default PARTITION OF evidence DEFAULT;

CREATE INDEX idx_ev_claim ON evidence (claim_id);
CREATE INDEX idx_ev_pay   ON evidence USING GIN (payload);

CREATE TABLE control_sites (
  id           BIGSERIAL PRIMARY KEY,
  claim_id     BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  geom         GEOMETRY(Point, 4326) NOT NULL,
  covariates   JSONB NOT NULL,
  delta        NUMERIC(10,5),
  CONSTRAINT ctrl_valid CHECK (ST_IsValid(geom))
);
CREATE INDEX idx_ctrl_claim ON control_sites (claim_id);
CREATE INDEX idx_ctrl_geom  ON control_sites USING GIST (geom);

-- ============ VERDICTS (FK target — not partitioned; see module docstring) ============
CREATE TYPE epistemic_level AS ENUM
  ('L0_recorded','L1_observed','L2_corroborated','L3_multi_indicator',
   'L4_control_differenced','N1_inconclusive','N2_unsupported','N3_contradicted');

CREATE TABLE verdicts (
  id                 BIGSERIAL PRIMARY KEY,
  claim_id           BIGINT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
  version            INTEGER NOT NULL DEFAULT 1,
  level              epistemic_level NOT NULL,
  rule_path          TEXT[] NOT NULL DEFAULT '{}',
  score              NUMERIC(5,4) NOT NULL,
  confidence         NUMERIC(5,4) NOT NULL,
  coverage           NUMERIC(5,4) NOT NULL,
  data_sufficiency   NUMERIC(5,4) NOT NULL,
  dissent            JSONB NOT NULL DEFAULT '[]',
  recommended_action JSONB NOT NULL,
  engine_version     TEXT NOT NULL,
  weights            JSONB NOT NULL,
  status             TEXT NOT NULL DEFAULT 'pending',
  computed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (claim_id, version),
  CONSTRAINT confidence_le_score CHECK (confidence <= ABS(score) + 0.0005)
  -- Structural invariant I1 (§14.4): confidence = |score| * coverage * quality,
  -- and coverage, quality ∈ [0,1], so confidence can never exceed |score|.
  -- Enforced here as well as in the engine's property tests (belt and braces —
  -- this is the exact defect that produced the unreproducible 0.71 in an
  -- earlier draft of Worked Example B).
);
CREATE INDEX idx_v_claim  ON verdicts (claim_id, version DESC);
CREATE INDEX idx_v_status ON verdicts (status, level);

-- ============ HUMAN ADJUDICATION (append-only, hash-chained) ============
CREATE TABLE adjudications (
  id              BIGSERIAL PRIMARY KEY,
  verdict_id      BIGINT NOT NULL REFERENCES verdicts(id),
  user_id         UUID NOT NULL REFERENCES users(id),
  decision        TEXT NOT NULL CHECK (decision IN ('accept','edit','reject')),
  corrected_level epistemic_level,
  reason          TEXT,
  decided_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  prev_hash       BYTEA,
  row_hash        BYTEA NOT NULL,
  CONSTRAINT reason_required CHECK (decision='accept' OR reason IS NOT NULL)
);
CREATE INDEX idx_adj_verdict ON adjudications (verdict_id);
REVOKE UPDATE, DELETE ON adjudications FROM pramaan_app;

-- ============ INDICATORS, ALERTS, REPORTS ============
CREATE TABLE indicator_values (
  id          BIGSERIAL PRIMARY KEY,
  mws_id      BIGINT NOT NULL REFERENCES micro_watersheds(id),
  code        TEXT NOT NULL,
  season      TEXT, year SMALLINT,
  value       NUMERIC(12,5),
  dispersion  NUMERIC(12,5),
  n_obs       INTEGER,
  sufficiency NUMERIC(4,3),
  UNIQUE (mws_id, code, season, year)
);

CREATE TABLE alerts (
  id         BIGSERIAL PRIMARY KEY,
  verdict_id BIGINT REFERENCES verdicts(id),
  mws_id     BIGINT REFERENCES micro_watersheds(id),
  kind       TEXT NOT NULL,
  priority   SMALLINT NOT NULL CHECK (priority BETWEEN 1 AND 5),
  message    TEXT NOT NULL,
  state      TEXT NOT NULL DEFAULT 'open',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_alert_open ON alerts (state, priority);

CREATE TABLE reports (
  id           BIGSERIAL PRIMARY KEY,
  scope_kind   TEXT NOT NULL,
  scope_id     TEXT NOT NULL,
  object_key   TEXT NOT NULL,
  generated_by UUID REFERENCES users(id),
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  lineage      JSONB NOT NULL
);

-- ============ AUDIT LOG (partitioned monthly by `at` — no incoming FK) ============
CREATE TABLE audit_log (
  id         BIGINT GENERATED ALWAYS AS IDENTITY,
  user_id    UUID, action TEXT NOT NULL, entity TEXT, entity_id TEXT,
  ip         INET, user_agent TEXT, payload JSONB,
  at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);

CREATE TABLE audit_log_2026_01 PARTITION OF audit_log
  FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE audit_log_2026_02 PARTITION OF audit_log
  FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE audit_log_2026_03 PARTITION OF audit_log
  FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;
-- Ops note: a monthly cron (scripts/rotate_audit_partitions.py, T-Stage 6)
-- creates the next month's partition and detaches/archives partitions older
-- than the retention window. The DEFAULT partition exists so writes never
-- fail if the cron falls behind; it is monitored and should stay empty.

CREATE INDEX idx_audit_at ON audit_log (at DESC);

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO pramaan_app;
REVOKE UPDATE, DELETE ON adjudications FROM pramaan_app;
"""

DDL_DOWN = """
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS reports CASCADE;
DROP TABLE IF EXISTS alerts CASCADE;
DROP TABLE IF EXISTS indicator_values CASCADE;
DROP TABLE IF EXISTS adjudications CASCADE;
DROP TABLE IF EXISTS verdicts CASCADE;
DROP TABLE IF EXISTS control_sites CASCADE;
DROP TABLE IF EXISTS evidence CASCADE;
DROP TABLE IF EXISTS claims CASCADE;
DROP TABLE IF EXISTS raster_layers CASCADE;
DROP TABLE IF EXISTS satellite_scenes CASCADE;
DROP TABLE IF EXISTS image_analysis CASCADE;
DROP TABLE IF EXISTS field_images CASCADE;
DROP TABLE IF EXISTS interventions CASCADE;
DROP TABLE IF EXISTS projects CASCADE;
DROP TABLE IF EXISTS micro_watersheds CASCADE;
DROP TABLE IF EXISTS sub_watersheds CASCADE;
DROP TABLE IF EXISTS watersheds CASCADE;
DROP TABLE IF EXISTS users CASCADE;

DROP TYPE IF EXISTS epistemic_level;
DROP TYPE IF EXISTS evidence_family;
DROP TYPE IF EXISTS coord_provenance;
DROP TYPE IF EXISTS work_status;
DROP TYPE IF EXISTS intervention_type;
DROP TYPE IF EXISTS project_phase;
DROP TYPE IF EXISTS user_role;

-- Dropping the role needs care. `GRANT ... ON ALL TABLES IN SCHEMA public`
-- above also granted on PostGIS's own `spatial_ref_sys`, which this migration
-- did not create and therefore must not drop. That grant outlives every DROP
-- TABLE above, and a bare `DROP ROLE` fails on it with
-- "role cannot be dropped because some objects depend on it".
-- Found by adding the reversibility check to CI, not by reading this file.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pramaan_app') THEN
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM pramaan_app';
    EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM pramaan_app';
    EXECUTE 'REVOKE ALL ON SCHEMA public FROM pramaan_app';
    -- Catches anything the explicit REVOKEs missed, in this database only.
    EXECUTE 'DROP OWNED BY pramaan_app';
    EXECUTE 'DROP ROLE pramaan_app';
  END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute(DDL_UP)


def downgrade() -> None:
    op.execute(DDL_DOWN)
