"""EXIF/XMP metadata extraction — where a photograph's coordinate comes from.

The whole value of a geotagged field photograph is that the coordinate was
recorded by the device at the moment of capture rather than typed afterwards.
That distinction survives into the database as `field_images.coord_provenance`,
and this module is the only place it is decided. An endpoint that resolved a
coordinate without recording how would leave an auditor unable to tell a camera
fix from an officer's guess, and those two carry very different weight.

## What is deliberately not attempted

`XMP` is read only for the metadata Pillow already surfaces through `getexif()`.
A full XMP/RDF parse (Adobe sidecar packets, `exif:GPSLatitude` in XML form) is
not implemented: no camera in the pilot writes GPS *only* to XMP, and a partial
RDF parser that silently misreads a namespace is worse than a stated gap. If a
future source needs it, `sidecar_json` is already a `coord_provenance` value
waiting for a real implementation.

No timezone is inferred. `DateTimeOriginal` is a naive local timestamp with no
offset in the vast majority of phone images, and `OffsetTimeOriginal` is absent
from all of them. Guessing IST because the district is in Maharashtra would
produce a timestamp that looks authoritative and is not, so the value is
returned naive and the caller stores it as-is. A ±5:30 uncertainty that is
visible beats a wrong instant that is not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

#: EXIF's own name for the GPS sub-IFD, as an integer tag. Pillow exposes the
#: block through `get_ifd(34853)`; the constant is spelled out rather than
#: imported because `PIL.ExifTags.IFD` moved between Pillow majors and a name
#: that disappears is a runtime failure at upload time.
_GPS_IFD = 0x8825

#: Tag 0x9003 `DateTimeOriginal` — when the shutter fired. Deliberately *not*
#: 0x0132 `DateTime`, which many editors rewrite on save, nor 0x9004
#: `DateTimeDigitized`. For evidence the shutter time is the only one that means
#: anything.
_DATETIME_ORIGINAL = 0x9003

#: `%Y:%m:%d %H:%M:%S` — colons in the date part, per the EXIF specification.
#: Cameras that write ISO-8601 here exist; they are handled by the fallback.
_EXIF_DATETIME = "%Y:%m:%d %H:%M:%S"


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    """Everything read out of one image's headers, normalised.

    Frozen: the resolution rules below decide provenance exactly once, and a
    caller that could overwrite `lat` after the fact would be able to change a
    coordinate without changing the provenance that describes it.

    Every field except the pixel dimensions is optional, because every one of
    them is genuinely absent from some real phone image. `None` here means "the
    file did not say", never "zero".
    """

    lat: float | None
    lon: float | None
    #: From `GPSHorizontalPositioningError` (metres, authoritative) or derived
    #: from `GPSDOP`. See `_accuracy` for why the derivation is coarse.
    gps_accuracy_m: float | None
    captured_at: datetime | None
    #: `GPSImgDirection`, degrees clockwise from north. Which way the camera
    #: faced, not which way the device was held.
    orientation_deg: float | None
    altitude_m: float | None
    device_make: str | None
    device_model: str | None
    width_px: int
    height_px: int
    #: The full header dump, JSON-safe. Stored verbatim in `raw_exif` so a
    #: dispute about any of the fields above can be settled against the source.
    raw: dict[str, Any]

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lon is not None


def _finite(value: Any) -> float | None:
    """A float, or None if the value is missing or not a real number.

    EXIF rationals carry a denominator that is legitimately zero when a field
    is present-but-unset, and Pillow renders that as `inf` or `nan`. Both are
    rejected by PostgreSQL's `jsonb` and by `NUMERIC`, so they are collapsed to
    None here rather than at each call site.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return out if math.isfinite(out) else None


def _ref(value: Any) -> str:
    """A GPS hemisphere reference as an upper-case single character.

    Pillow returns these as `str` for most files and as `bytes` for some
    (notably older Samsung and GoPro firmware). Treating `b'S'` as a string
    makes the comparison `b'S' in ('S', 'W')` false, which silently puts a
    southern-hemisphere photograph in the north. That is the exact bug this
    helper exists to prevent, and it is asserted in the unit suite.
    """
    if isinstance(value, bytes):
        return value.decode("ascii", "ignore").strip().upper()[:1]
    return str(value or "").strip().upper()[:1]


def dms_to_degrees(dms: Any, ref: Any) -> float | None:
    """Convert an EXIF `(degrees, minutes, seconds)` rational triple to degrees.

    `degrees + minutes/60 + seconds/3600`, then negated as a whole for the
    southern and western hemispheres.

    Negating the whole sum is the point. The mistake that keeps recurring is
    negating only the degrees term — `-d + m/60 + s/3600` — which for
    18°30'00"S yields -17.5 instead of -18.5. That error is roughly 110 km, it
    is largest for coordinates closest to a whole degree, and it produces a
    perfectly plausible coordinate that lands in the wrong micro-watershed. So
    the conversion is unsigned throughout and the sign is applied once, last.

    Returns None rather than raising for a malformed triple: a file with a
    broken GPS block should fall through to the form-supplied coordinate, not
    500 the upload.
    """
    if dms is None:
        return None
    try:
        parts = list(dms)
    except TypeError:
        return None
    if len(parts) != 3:
        return None

    degrees, minutes, seconds = (_finite(part) for part in parts)
    if degrees is None or minutes is None or seconds is None:
        return None

    magnitude = abs(degrees) + abs(minutes) / 60.0 + abs(seconds) / 3600.0
    if not math.isfinite(magnitude):
        return None
    return -magnitude if _ref(ref) in ("S", "W") else magnitude


def _accuracy(gps: dict[str, Any]) -> float | None:
    """Horizontal accuracy in metres, from the best field the file offers.

    `GPSHorizontalPositioningError` is already metres and is used directly.
    Failing that, `GPSDOP` is a dimensionless dilution-of-precision figure, and
    converting it needs an assumed receiver error: the conventional
    `accuracy ≈ DOP × UERE` with a 5 m UERE for consumer GNSS. That constant is
    an assumption, so the derived value is deliberately coarse and is recorded
    as an accuracy rather than as a measurement — it feeds `uncertainty_m`,
    which is exactly a statement of doubt.

    Returning None is a real outcome and must stay one. Substituting a
    default — 15 m, say — would make every un-tagged photograph indistinguishable
    from one carrying a genuine 15 m fix.
    """
    direct = _finite(gps.get("GPSHorizontalPositioningError"))
    if direct is not None and direct >= 0:
        return direct

    dop = _finite(gps.get("GPSDOP"))
    if dop is not None and dop > 0:
        return dop * _ASSUMED_UERE_M
    return None


#: User-equivalent range error for consumer GNSS, metres. Used only to turn a
#: DOP figure into an accuracy. Textbook value for civilian single-frequency
#: receivers; not measured against the pilot's handsets.
_ASSUMED_UERE_M = 5.0


def _altitude(gps: dict[str, Any]) -> float | None:
    """Altitude in metres, signed by `GPSAltitudeRef` (1 means below sea level)."""
    altitude = _finite(gps.get("GPSAltitude"))
    if altitude is None:
        return None
    ref = gps.get("GPSAltitudeRef")
    below = False
    if isinstance(ref, bytes):
        below = ref[:1] == b"\x01"
    elif isinstance(ref, int):
        below = ref == 1
    return -abs(altitude) if below else altitude


def _captured_at(exif: dict[str, Any]) -> datetime | None:
    """`DateTimeOriginal` as a naive datetime, or None.

    Two formats are accepted: the EXIF-specified `%Y:%m:%d %H:%M:%S` and
    ISO-8601, which a minority of devices write instead. Anything else is None:
    a half-parsed timestamp is worse than an absent one, because absence is
    visible in the UI and a wrong date is not.
    """
    raw = exif.get("DateTimeOriginal")
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("\x00", "")
    if not text:
        return None
    try:
        return datetime.strptime(text, _EXIF_DATETIME)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


#: Longest bytes value copied into `raw_exif`. MakerNote blocks routinely run to
#: tens of kilobytes of undocumented vendor binary; storing them would bloat
#: every row for data nobody can interpret. Truncation is recorded in the value
#: itself so the record never pretends to be complete.
_MAX_BYTES_IN_RAW = 64


def _jsonable(value: Any, depth: int = 0) -> Any:
    """Coerce one EXIF value into something `jsonb` accepts.

    EXIF is not a JSON-shaped format. It carries `IFDRational`, raw `bytes`,
    nested tuples, and floats that are `inf`. Every one of those either raises
    in `json.dumps` or is rejected by PostgreSQL, and this function exists so
    that a single unusual camera cannot fail an upload. Losing fidelity on an
    undocumented MakerNote is acceptable; refusing a field officer's evidence
    because of one is not.
    """
    if depth > 4:
        # Depth-limited rather than cycle-detected: EXIF IFDs nest at most a
        # couple of levels, so anything deeper is a malformed file, and a bound
        # is cheaper than tracking visited ids.
        return "<nested>"
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        head = value[:_MAX_BYTES_IN_RAW]
        suffix = "..." if len(value) > _MAX_BYTES_IN_RAW else ""
        return f"0x{head.hex()}{suffix}"
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item, depth + 1) for item in value]
    # IFDRational and anything else vendor-specific: try a number, else its text.
    number = _finite(value)
    return number if number is not None else str(value)


def _named(block: dict[int, Any], names: dict[int, str]) -> dict[str, Any]:
    """Re-key a raw EXIF IFD from integer tags to their standard names.

    Unknown tags keep a `tag_<decimal>` key rather than being dropped: an
    unrecognised tag is still evidence about the device that produced the file.
    """
    out: dict[str, Any] = {}
    for tag, value in block.items():
        key = names.get(tag) or f"tag_{tag}"
        out[key] = _jsonable(value)
    return out


def read_metadata(image: Image.Image) -> ImageMetadata:
    """Extract normalised metadata from an already-opened image.

    Takes an `Image` rather than bytes so the caller — which has already had to
    decode the file to validate it — does not parse the same bytes twice.

    Never raises for a file with absent, partial, or malformed metadata. Every
    unreadable field becomes None, and the decision about whether the result is
    usable belongs to the service layer, which is the only place that knows
    whether the form supplied a coordinate instead.
    """
    try:
        exif_raw = image.getexif()
    except Exception:  # noqa: BLE001 - Pillow raises plugin-specific errors here
        # A corrupt APP1 segment must not cost us the image. The photograph is
        # still evidence; it simply arrives without a camera-recorded position,
        # and the caller will then require a manual pin or refuse.
        return ImageMetadata(
            lat=None,
            lon=None,
            gps_accuracy_m=None,
            captured_at=None,
            orientation_deg=None,
            altitude_m=None,
            device_make=None,
            device_model=None,
            width_px=image.width,
            height_px=image.height,
            raw={"error": "exif block unreadable"},
        )

    base = _named(dict(exif_raw), TAGS)

    gps_block: dict[int, Any] = {}
    try:
        gps_block = dict(exif_raw.get_ifd(_GPS_IFD))
    except Exception:  # noqa: BLE001 - same reasoning as above, GPS sub-IFD only
        gps_block = {}
    gps = _named(gps_block, GPSTAGS)

    # The DMS triples are read from the *raw* block, not from `gps`: `_jsonable`
    # has already flattened rationals to floats there, and while that happens to
    # be lossless for these tags, converting from the sanitised copy would make
    # the coordinate depend on the serialisation format. The coordinate is the
    # one value in this file that must be derived from the source.
    lat = dms_to_degrees(gps_block.get(_GPS_TAG_LATITUDE), gps_block.get(_GPS_TAG_LATITUDE_REF))
    lon = dms_to_degrees(gps_block.get(_GPS_TAG_LONGITUDE), gps_block.get(_GPS_TAG_LONGITUDE_REF))
    # A pair is all or nothing. Half a coordinate is not a location, and keeping
    # a lone latitude would let it combine with a form-supplied longitude into a
    # position no device ever reported.
    if lat is None or lon is None or not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        lat = lon = None

    raw: dict[str, Any] = {"exif": base}
    if gps:
        raw["gps"] = gps

    return ImageMetadata(
        lat=lat,
        lon=lon,
        gps_accuracy_m=_accuracy(gps),
        captured_at=_captured_at(base),
        orientation_deg=_finite(gps.get("GPSImgDirection")),
        altitude_m=_altitude(gps),
        device_make=_text(base.get("Make")),
        device_model=_text(base.get("Model")),
        width_px=image.width,
        height_px=image.height,
        raw=raw,
    )


#: GPS sub-IFD tags, by number. Read from the raw block, so the names in
#: `GPSTAGS` are not usable as keys here.
_GPS_TAG_LATITUDE_REF = 1
_GPS_TAG_LATITUDE = 2
_GPS_TAG_LONGITUDE_REF = 3
_GPS_TAG_LONGITUDE = 4


def _text(value: Any) -> str | None:
    """A trimmed string, or None. EXIF text fields are NUL-padded to a fixed
    width by many encoders, and the NULs survive into PostgreSQL as literal
    `\\u0000`, which `TEXT` rejects."""
    if value is None:
        return None
    out = str(value).replace("\x00", "").strip()
    return out or None
