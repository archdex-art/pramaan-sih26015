/**
 * Evidence capture — the field workspace's only write action.
 *
 * ## Why this screen exists
 *
 * A WDT or PIA account holds `claim:create` and, until now, had nowhere to use
 * it: the field workspace could read the rows someone else's seed script had
 * written and nothing else. The register and the verification queue are both
 * downstream of this form — without it the product's first step was a database
 * insert performed by a developer.
 *
 * ## The two things this screen refuses to do
 *
 * **No GPS-lock readout.** The browser is not the capture device. The
 * photograph was taken on a phone in a field and the coordinate that matters is
 * the one the camera wrote into the file's EXIF, which only the server can read.
 * A "GPS LOCK: CONNECTED (±4.2 m)" indicator on a laptop is a drawing of a
 * sensor. There is a geolocation button instead, and it fills the override
 * fields with the accuracy figure the browser actually reported — a real number
 * or a stated failure, never a decorative one.
 *
 * **No verdict.** A successful capture produces a stored claim, an image and a
 * quality reading. It does not produce a level, because reconciliation is a
 * separate step that has not run. The receipt says so in those words; printing
 * a level here would invent the one thing this product exists to withhold.
 *
 * ## Where the intervention vocabulary comes from
 *
 * `GET /api/v1/method/signatures`, whose keys are the engine's own
 * `SIGNATURES` table and match the PostgreSQL `intervention_type` enum. It is
 * fetched rather than retyped: a second copy of that vocabulary in a frontend
 * constant would drift from the enum, and a form that offers a type the database
 * rejects fails at the last possible moment. The same response carries each
 * type's confidence ceiling and whether it is optically assessable at all, which
 * is worth telling the recorder *before* they submit.
 */

import { type FormEvent, useEffect, useMemo, useState } from "react";
import { ApiError, get } from "../lib/api";
import { authFetch, can, getSession } from "../lib/auth";

/** One row of `/api/v1/method/signatures`, mirroring the `Signature` dataclass
 *  as that endpoint projects it. Declared here rather than in `lib/api` because
 *  this is the only screen that reads the table's non-key columns. */
interface SignatureRow {
  purpose: string;
  expect_increase: string[];
  expect_decrease: string[];
  aoi: string;
  footprint_min_m2: number;
  footprint_max_m2: number;
  typical_footprint_m2: number;
  terrain_rule: string;
  confidence_ceiling: string;
  optically_assessable: boolean;
  note: string;
}

interface SignatureTable {
  signatures: Record<string, SignatureRow>;
  not_optically_assessable: string[];
}

/** The server's reading of the submitted frame. `passed` gates the insert, so a
 *  receipt only ever carries `passed: true` — `flags` may still be non-empty,
 *  and a flag that survived the gate is exactly the sort of thing that must not
 *  be swallowed. */
interface Quality {
  blur_score: number;
  exposure_ok: boolean;
  passed: boolean;
  flags: string[];
}

/** The 201 body of `POST /api/v1/claims`. Every field is rendered: this is the
 *  only moment at which the recorder can see what was actually stored against
 *  what they typed, and a silently dropped field is a silently changed record. */
interface CaptureReceipt {
  claim_id: number;
  unique_id: string;
  image_id: string;
  district_lgd: string;
  intervention_type: string;
  asserted_date: string;
  lat: number;
  lon: number;
  coord_provenance: string;
  gps_accuracy_m: number | null;
  duplicate_of: string | null;
  quality: Quality;
}

/**
 * The one multipart request in the product.
 *
 * The shared `post` helper in `lib/api` cannot carry it: that helper sets
 * `Content-Type: application/json`, and *any* fixed content type on a
 * `FormData` body strips the multipart boundary the server needs to split the
 * parts — the request arrives as an unparseable blob. `authFetch` is used
 * directly because it leaves the header alone, so the browser writes the
 * boundary itself.
 *
 * Token refresh is deliberately not reimplemented here. `authFetch` owns the
 * single-retry-on-401 behaviour, and a second copy of it would eventually
 * disagree about when a session is over. The error unwrap below is the same
 * shape as `get`'s so the server's `detail` reaches the screen either way —
 * which matters more here than anywhere else, since 409 and 422 are the two
 * outcomes a recorder can actually act on.
 */
async function submitCapture(form: FormData): Promise<CaptureReceipt> {
  const response = await authFetch("/api/v1/claims", { method: "POST", body: form });
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
  return (await response.json()) as CaptureReceipt;
}

/** The server's rejection, said in the recorder's terms.
 *
 *  The `detail` string is always shown verbatim and first — it is the only part
 *  that names *which* gate failed or *which* existing record was matched. The
 *  sentence after it explains why the gate exists, because "blurred" without
 *  "so the photo family cannot be read" reads as fussiness. The detail is never
 *  parsed: matching on its wording here would silently degrade to the generic
 *  branch the first time the server rephrases itself. */
function failureOf(err: ApiError): { head: string; why: string } {
  if (err.status === 409) {
    return {
      head: "Not recorded — this photograph is already in the register.",
      why:
        "The frame's perceptual hash is within six bits of an image already " +
        "stored in this district. Nothing was written — not the claim, not the " +
        "image object. Re-photographing a structure produces a near-identical " +
        "hash, so a second record would double-count one work and inflate the " +
        "district's totals. If this is a genuinely different structure, " +
        "photograph it from a position that shows it is; if it is the same one, " +
        "open the existing record instead of adding to it.",
    };
  }
  if (err.status === 422) {
    return {
      head: "Not recorded — the submission did not clear a pre-condition.",
      why:
        "Three things are checked before anything is stored. The frame must be " +
        "sharp and correctly exposed, because a photo family reading taken " +
        "from an unreadable frame is a number with nothing behind it. A " +
        "coordinate must be resolvable — from the file's EXIF or from the " +
        "override fields — because a claim with no location cannot be placed " +
        "on terrain or in imagery at all, and none of the other five evidence " +
        "families can be computed for it. And the district must be known: " +
        "there is no district-boundary table in this system, so a coordinate " +
        "cannot be resolved to a district honestly, and an account with no " +
        "assigned district has to state one.",
    };
  }
  if (err.status === 415) {
    return {
      head: "Not recorded — the server cannot decode this file.",
      why:
        "JPEG, PNG, WebP and TIFF are read. HEIC is not: the decoder for it " +
        "is not installed in this deployment, and an iPhone photographing at " +
        "default settings writes HEIC. That is a missing library, not a " +
        "damaged photograph — export or convert the frame to JPEG and submit " +
        "the same image again.",
    };
  }
  if (err.status === 413) {
    return {
      head: "Not recorded — the file is larger than the 25 MiB limit.",
      why:
        "Nothing was stored. Reducing the frame's resolution is safe for the " +
        "quality gate, which reads sharpness and exposure rather than pixel " +
        "count; re-encoding at a lower JPEG quality is not, because it adds " +
        "the compression artefacts the blur measure would then read.",
    };
  }
  if (err.status === 403) {
    return {
      head: "Refused by the server.",
      why:
        "Either this account does not hold `claim:create`, or the district " +
        "code supplied lies outside the jurisdiction the account is scoped " +
        "to. Both are re-checked on every request regardless of what this " +
        "interface allows, and the server's reason is the line above.",
    };
  }
  return {
    head: "Not recorded.",
    why:
      "Nothing was stored. The claim, the image and the quality reading are " +
      "written in one transaction, so a failure leaves no partial record " +
      "behind and the form can be resubmitted unchanged.",
  };
}

export function Capture({ onCreated }: { onCreated: (claimId: number) => void }) {
  const session = getSession();

  const [table, setTable] = useState<SignatureTable | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);

  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const [type, setType] = useState("");
  const [assertedDate, setAssertedDate] = useState("");
  const [claimText, setClaimText] = useState("");

  // Held as strings, not numbers. An empty string is "not supplied", which the
  // server must be able to distinguish from a supplied 0 — 0°N 0°E is a real
  // coordinate in the Gulf of Guinea, and a numeric state initialised to 0
  // would submit it.
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [accuracy, setAccuracy] = useState("");
  const [district, setDistrict] = useState("");

  const [geoBusy, setGeoBusy] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [receipt, setReceipt] = useState<CaptureReceipt | null>(null);

  useEffect(() => {
    let cancelled = false;
    void get<SignatureTable>("/api/v1/method/signatures")
      .then((t) => {
        if (!cancelled) setTable(t);
      })
      .catch((err: unknown) => {
        if (!cancelled) setTableError(err instanceof ApiError ? err.detail : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // One object URL per selected file, revoked by the same effect that created
  // it. The cleanup runs both when the selection changes and on unmount, which
  // is the whole point: an un-revoked blob URL pins the entire decoded image in
  // memory for the lifetime of the document, and a recorder working through a
  // morning's photographs would accumulate every one of them.
  useEffect(() => {
    if (photo === null) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(photo);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  const types = useMemo(
    () => (table === null ? [] : Object.keys(table.signatures).sort()),
    [table],
  );

  const signature = table === null || type === "" ? null : (table.signatures[type] ?? null);

  /** The server derives the district from the caller's jurisdiction scope and
   *  never from the coordinate — there is no district-boundary table, so a
   *  point cannot be resolved to a district honestly. An account with an empty
   *  scope (unrestricted) therefore has nothing to derive from and must state
   *  the district itself, which the server enforces with a 422. Derived from
   *  the session's own scope rather than from a role name, so a new
   *  unrestricted role is handled without a change here. */
  const districtRequired = session === null || session.districts.length === 0;

  const locate = () => {
    setGeoError(null);
    setGeoBusy(true);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLat(pos.coords.latitude.toFixed(6));
        setLon(pos.coords.longitude.toFixed(6));
        // The browser's own 68 % radius, written through unrounded. It is the
        // only uncertainty figure that exists for a typed coordinate, and
        // smoothing it to a tidier number would be inventing precision.
        setAccuracy(pos.coords.accuracy.toFixed(1));
        setGeoBusy(false);
      },
      (err) => {
        setGeoError(
          err.message === ""
            ? "The browser reported no position and gave no reason."
            : err.message,
        );
        setGeoBusy(false);
      },
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 0 },
    );
  };

  const reset = () => {
    setReceipt(null);
    setError(null);
    setPhoto(null);
    setType("");
    setAssertedDate("");
    setClaimText("");
    setLat("");
    setLon("");
    setAccuracy("");
    setDistrict("");
    setGeoError(null);
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy || photo === null) return;

    const form = new FormData();
    form.set("photo", photo);
    form.set("intervention_type", type);
    form.set("asserted_date", assertedDate);
    form.set("claim_text", claimText);
    // Blank optional fields are omitted entirely rather than sent as "". The
    // server decides between reading the EXIF and honouring an override, and an
    // empty string is neither answer.
    if (lat.trim() !== "") form.set("lat", lat.trim());
    if (lon.trim() !== "") form.set("lon", lon.trim());
    if (accuracy.trim() !== "") form.set("gps_accuracy_m", accuracy.trim());
    if (district.trim() !== "") form.set("district_lgd", district.trim());

    setBusy(true);
    setError(null);
    void submitCapture(form)
      .then(setReceipt)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err : new ApiError(0, String(err)));
      })
      .finally(() => setBusy(false));
  };

  if (!can("claim:create")) {
    return (
      <div className="screen">
        <header className="screen-head rise">
          <div>
            <h1>Record a work</h1>
            <p className="sub">Not available to this account.</p>
          </div>
        </header>
        <div className="panel strip-panel rise">
          <h2 className="label">Why this is empty</h2>
          <p className="note">
            Your role (<code>{session?.role ?? "unknown"}</code>) does not hold{" "}
            <code>claim:create</code>. Recording a work is a WDT or PIA action;
            a monitoring or audit account reads and signs what the field
            submitted but does not submit on its behalf, so that the ledger
            never shows an officer adjudicating their own entry. The server
            enforces this on every request — this screen only reports it.
          </p>
        </div>
      </div>
    );
  }

  if (receipt) {
    const q = receipt.quality;
    return (
      <div className="screen">
        <header className="screen-head rise">
          <div>
            <h1>Recorded</h1>
            <p className="sub mono">
              {receipt.unique_id} · district {receipt.district_lgd} · image{" "}
              {receipt.image_id}
            </p>
          </div>
          <div className="head-actions">
            <button className="btn" onClick={() => onCreated(receipt.claim_id)}>
              Track this submission
            </button>
            <button className="btn" onClick={reset}>
              Record another
            </button>
          </div>
        </header>

        <div className="cols">
          <section className="panel col rise">
            <h2 className="label">What was stored</h2>
            <dl className="kv">
              <dt>Unique ID</dt>
              <dd className="mono">{receipt.unique_id}</dd>
              <dt>Image</dt>
              <dd className="mono">{receipt.image_id}</dd>
              <dt>Type</dt>
              <dd className="mono">{receipt.intervention_type}</dd>
              <dt>Claimed date</dt>
              <dd className="mono">{receipt.asserted_date}</dd>
              <dt>Coordinate</dt>
              <dd className="mono">
                {receipt.lat.toFixed(5)}°N {receipt.lon.toFixed(5)}°E
              </dd>
              <dt>Coordinate source</dt>
              <dd className="mono">{receipt.coord_provenance}</dd>
              <dt>GPS accuracy</dt>
              <dd className="mono">
                {receipt.gps_accuracy_m === null
                  ? "not recorded"
                  : `±${receipt.gps_accuracy_m.toFixed(1)} m`}
              </dd>
            </dl>
            <p className="note">
              The coordinate source is stored, not inferred: an auditor reading
              this record later can tell a camera's own fix from a figure typed
              into a form. Where the accuracy is not recorded, it is shown as
              absent rather than as a zero — an unrecorded radius is not a
              perfect one, and the terrain family reads every variable as a
              distribution over that radius.
            </p>
          </section>

          <section className="panel col centre rise">
            <h2 className="label">Frame quality, as measured</h2>
            <dl className="metrics">
              <div>
                <dt className="label">blur score</dt>
                <dd className="mono figure">{q.blur_score.toFixed(3)}</dd>
              </div>
              <div>
                <dt className="label">exposure</dt>
                <dd className="mono figure">{q.exposure_ok ? "ok" : "out of range"}</dd>
              </div>
              <div>
                <dt className="label">gate</dt>
                <dd className="mono figure">{q.passed ? "passed" : "not passed"}</dd>
              </div>
              <div>
                <dt className="label">flags</dt>
                <dd className="mono figure">{q.flags.length}</dd>
              </div>
            </dl>
            {q.flags.length > 0 && (
              <>
                <h3 className="label">Flags raised</h3>
                <ul>
                  {q.flags.map((f) => (
                    <li key={f} className="mono">
                      {f}
                    </li>
                  ))}
                </ul>
                <p className="note">
                  These were raised but did not block the insert. They travel
                  with the record and lower the photo family's contribution;
                  they are shown here so the recorder knows before the officer
                  does.
                </p>
              </>
            )}
            {receipt.duplicate_of !== null && (
              <>
                <h3 className="label">Resembles an existing record</h3>
                <p className="note mono">{receipt.duplicate_of}</p>
                <p className="note">
                  The frame's perceptual hash sits between seven and twelve bits
                  from that record's image — near, but outside the range treated
                  as the same photograph. It was therefore stored, with the
                  resemblance recorded against it and a{" "}
                  <span className="mono">similar_image</span> flag. A monitoring
                  officer sees both. This is not a rejection and this interface
                  does not present it as one; it exists so that two records of
                  one structure cannot pass unnoticed.
                </p>
              </>
            )}
          </section>

          <section className="col rise">
            <div className="panel dissent">
              <h2 className="label">There is no verdict yet</h2>
              <ul>
                <li>
                  No epistemic level has been computed for this claim. Nothing
                  on this page is a finding.
                </li>
                <li>
                  Reconciliation is a separate step: the engine reads terrain,
                  imagery, temporal, control and context evidence for this
                  coordinate and issues a level with its dissent. It has not run
                  for this record.
                </li>
                <li>
                  Even once it has, the verdict stays provisional until a
                  monitoring officer signs it. Until then this is a recorded
                  assertion with a photograph and a coordinate attached.
                </li>
              </ul>
            </div>
          </section>
        </div>
      </div>
    );
  }

  const failure = error === null ? null : failureOf(error);

  return (
    <div className="screen">
      <header className="screen-head rise">
        <div>
          <h1>Record a work</h1>
          <p className="sub">
            One photograph, one structure, one record. Stored against your
            account and your district.
          </p>
        </div>
      </header>

      {failure && (
        <div className="error-inline rise">
          <strong>{failure.head}</strong>
          <p className="note mono">{error?.detail}</p>
          <p className="note">{failure.why}</p>
        </div>
      )}

      <div className="panel strip-panel rise">
        <form className="login-form" onSubmit={submit}>
          <label className="login-field">
            <span className="login-label">Photograph (required)</span>
            {/* `accept` lists the formats the server can actually decode rather
                than `image/*`: a picker that offers HEIC on an iPhone produces
                a 415 the recorder cannot act on from the file dialog. */}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp,image/tiff"
              required
              onChange={(e) => setPhoto(e.target.files?.[0] ?? null)}
            />
          </label>

          {preview !== null && (
            /* Sized with the HTML `width` attribute, not CSS: the height then
               follows the frame's own aspect ratio, and this screen adds no
               stylesheet of its own. */
            <img src={preview} width={280} alt="The frame selected for upload." />
          )}
          <p className="note">
            The file is read on the server, not here. The coordinate in its EXIF
            is preferred over anything typed below, because a camera's fix is a
            record and a typed figure is an assertion. JPEG, PNG, WebP and TIFF
            up to 25 MiB are decodable in this deployment;{" "}
            <strong>HEIC is not</strong>, so an iPhone shooting at its default
            setting must export to JPEG first. Said here rather than after the
            upload fails, because the failure would otherwise look like a
            damaged photograph.
          </p>

          <label className="login-field">
            <span className="login-label">Intervention type (required)</span>
            <select value={type} required onChange={(e) => setType(e.target.value)}>
              <option value="">— select —</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          {tableError !== null && (
            <p className="login-error">
              The intervention vocabulary could not be loaded ({tableError}), so
              this list is empty. It is served from the engine's own signature
              table and is deliberately not duplicated here, which means there
              is no fallback list to fall back to.
            </p>
          )}
          {signature && (
            <p className="note">
              <strong>{signature.purpose}.</strong> Expected footprint around{" "}
              <span className="mono">
                {signature.typical_footprint_m2.toFixed(0)} m²
              </span>{" "}
              (range <span className="mono">{signature.footprint_min_m2.toFixed(0)}</span>–
              <span className="mono">{signature.footprint_max_m2.toFixed(0)} m²</span>).
              The engine cannot take this type above{" "}
              <span className="mono">{signature.confidence_ceiling}</span> however
              much evidence agrees.
              {!signature.optically_assessable &&
                " This type has no reliable signature at 30 m resolution, so the satellite, temporal and control families will be unavailable for it — and their absence will not be read as evidence against the claim."}
              {signature.note !== "" && ` ${signature.note}`}
            </p>
          )}

          <label className="login-field">
            <span className="login-label">Date the work was completed (required)</span>
            <input
              type="date"
              required
              value={assertedDate}
              onChange={(e) => setAssertedDate(e.target.value)}
            />
          </label>

          <label className="login-field">
            <span className="login-label">What was built (required)</span>
            <textarea
              rows={3}
              maxLength={4000}
              required
              value={claimText}
              onChange={(e) => setClaimText(e.target.value)}
              placeholder="What the structure is and where it sits, in your own words."
            />
          </label>
          <p className="note">
            This description is stored on the audit event for the capture, not on
            the claim: the schema has no narrative column and this prototype does
            not add one for a field it cannot yet use. It is therefore readable
            in the audit trail and will <strong>not</strong> appear when the
            record is opened. Said plainly because a form that implies a note
            will show up on the record, and then does not, teaches the recorder
            to stop writing them.
          </p>

          <h3 className="label">Coordinate override — only if the camera recorded no fix</h3>
          <p className="note">
            Leave these blank. The server reads the photograph's EXIF GPS tags and
            prefers them, and the record stores which source was used so an
            auditor can tell the two apart. Fill them in only when the camera
            wrote no position, in which case the record will say the coordinate
            was supplied by hand.
          </p>
          <div className="filters">
            <label className="login-field">
              <span className="login-label">Latitude</span>
              <input
                type="number"
                step="any"
                inputMode="decimal"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
              />
            </label>
            <label className="login-field">
              <span className="login-label">Longitude</span>
              <input
                type="number"
                step="any"
                inputMode="decimal"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
              />
            </label>
            <label className="login-field">
              <span className="login-label">Accuracy (metres)</span>
              <input
                type="number"
                step="0.1"
                min="0"
                inputMode="decimal"
                value={accuracy}
                onChange={(e) => setAccuracy(e.target.value)}
              />
            </label>
          </div>
          <button className="btn" type="button" onClick={locate} disabled={geoBusy}>
            {geoBusy ? "Asking the browser…" : "Use this device's position"}
          </button>
          <p className="note">
            That button fills the three fields above from{" "}
            <code>navigator.geolocation</code> and writes the accuracy radius the
            browser itself reports. On a laptop indoors that radius is often
            hundreds of metres, and it will be stored as such — this is the
            device's position, not the structure's, and it is only worth using
            when the two are the same place.
          </p>
          {geoError !== null && (
            <p className="login-error">
              No position was obtained: {geoError}. Nothing was filled in.
            </p>
          )}

          <label className="login-field">
            <span className="login-label">
              {districtRequired
                ? "District LGD code (required for this account)"
                : "District LGD code (optional)"}
            </span>
            <input
              type="text"
              inputMode="numeric"
              required={districtRequired}
              value={district}
              onChange={(e) => setDistrict(e.target.value)}
              placeholder={
                districtRequired
                  ? "no district is assigned to this account"
                  : `defaults to ${session === null ? "" : session.districts.join(", ")}`
              }
            />
          </label>
          <p className="note">
            {districtRequired
              ? "This account has no assigned district, so there is nothing for the server to default to. The district is not read from the coordinate: there is no district-boundary table in this system and guessing one from a point would be a fabricated attribution."
              : "Leave blank and the server uses the district your account is assigned to. It is not read from the coordinate — there is no district-boundary table here, so a point cannot be resolved to a district honestly. A code outside your jurisdiction is refused, not silently corrected."}{" "}
            Whatever is stored comes back in the receipt, and the receipt is the
            authority — not this field.
          </p>

          <button
            type="submit"
            className="adj-submit"
            disabled={
              busy ||
              photo === null ||
              type === "" ||
              assertedDate === "" ||
              claimText.trim() === "" ||
              (districtRequired && district.trim() === "")
            }
          >
            {busy ? "Sending…" : "Record this work"}
          </button>
          {busy && (
            <p className="note">
              The upload, the quality reading, the duplicate check and the insert
              happen in one request. There is no progress figure here because
              there is nothing to measure it against — the server reports either
              a stored record or a reason it refused.
            </p>
          )}
        </form>
      </div>

      <p className="foot-note rise">
        Recording a work does not assess it. This form stores a photograph, a
        coordinate and a claim; the epistemic level comes later, from evidence
        this account does not supply, and the record does not become government
        evidence until a monitoring officer signs it.
      </p>
    </div>
  );
}
