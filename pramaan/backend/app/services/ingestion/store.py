"""Object storage for field photographs, and the decode/re-encode boundary.

Two responsibilities that belong together because the ordering between them is
the whole safety property: nothing reaches the bucket until it has been proven
decodable, and no database row is written until the bucket holds the bytes it
points at.

## Bytes in are never bytes out

What a field officer uploads is an untrusted file that a browser will later be
asked to render. Serving it back verbatim makes this service a delivery
mechanism for whatever is embedded in it — a polyglot JPEG/HTML, a crafted
EXIF that trips a downstream parser, an image bomb. So the upload is decoded
with Pillow, checked, and **re-encoded from the decoded pixel array**. What
lands in the bucket is a JPEG this process wrote, byte for byte, and it carries
no metadata segments at all.

The libvips pipeline named in the design notes is not used: Pillow is already a
dependency of this backend, does the same decode-and-re-encode, and adding a
second imaging stack for one operation is not a trade worth making. The security
property required — that stored bytes are our bytes — is identical.

### The forensic cost, stated plainly

Re-encoding means the exact bytes the officer's device produced are not
retained. A dispute about the file itself, as opposed to the photograph in it,
cannot be settled from the bucket. That is a real loss and it is compensated
rather than hidden: the SHA-256 of the original upload is recorded in the audit
trail entry for the capture, so an officer who still holds the original can
prove it is the file they sent, and cannot substitute a different one afterwards.

### What is not done

Faces are not detected or blurred. The design notes list face blurring as a
requirement before any UI displays a field photograph, and it is genuinely not
implemented here: it needs a face detector, this module deliberately contains no
model, and a blur applied to the wrong region would give the appearance of
privacy protection without the substance. Consequence, stated because it must be
before anyone shows these images publicly: **the stored derivative may contain
identifiable faces.**

## Store before insert

`put_image` runs to completion before the transaction in `service.py` inserts
anything. The failure modes are asymmetric. An object with no row is an orphan:
it wastes storage and a sweeper can find it by asking the database which keys
are known. A row with no object is a claim whose evidence does not exist —
`field_images.object_key` is `NOT NULL`, so the record asserts a photograph is
there, and every screen, report and Evidence Pack downstream believes it. The
first is a housekeeping problem, the second is a lie in the ledger's input.

So the order is: decode, measure, store, then insert. If the store is
unreachable the request fails with that reason named, and no claim is created.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover - import-time only, for the checker
    from mypy_boto3_s3.client import S3Client

#: Bucket for field evidence. A module constant rather than a setting because
#: `app/core/config.py` is not this module's to change and because the value is
#: not environment-dependent: the same name is correct in dev, in CI and in the
#: pilot, and the endpoint and credentials — which *are* environment-dependent —
#: already come from settings.
BUCKET = "pramaan-field-images"

#: Largest upload accepted, bytes. A 108 MP phone JPEG is about 20 MB; 25 MiB
#: leaves headroom without letting a single request pull an arbitrary amount of
#: memory into the process. Enforced by counting bytes as they are read, not by
#: trusting `Content-Length`, which a client sets.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

#: Long edge of the stored derivative. Enough for a monitoring officer to judge
#: what is in the frame on screen; small enough that a register page of them is
#: not a multi-megabyte load.
THUMBNAIL_LONG_EDGE = 512

#: Quality for both re-encodes. 88 is above the point where JPEG artefacts are
#: visible on structure edges and below the point where the file size stops
#: buying anything.
JPEG_QUALITY = 88

#: Formats accepted, by Pillow's own format name. The list is what Pillow
#: decodes without an extra plugin, and it is an allowlist rather than a
#: denylist: a new Pillow plugin must be admitted deliberately, not inherited.
ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "TIFF", "MPO"})

#: Leading bytes for each accepted container, checked before Pillow is handed the
#: file. Defence in depth, not identification: Pillow's decoder is the authority
#: on what the file is, and this table exists so that arbitrary bytes are not
#: routed through every image plugin compiled into the library.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
)

#: ISO base-media brands that mean HEIF/HEIC. Detected specifically so the
#: refusal can say "convert to JPEG" — iPhones shoot HEIC by default, so this is
#: the single most likely rejection in the field, and "unsupported file" would
#: send an officer looking for a fault that does not exist.
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"heim", b"heis", b"hevc", b"mif1", b"msf1"})


class UploadRejected(ValueError):
    """The uploaded bytes are not an image this service will accept.

    Distinct from `ObjectStoreUnavailable` because the two map to opposite
    halves of the HTTP status space: this one is the client's file, that one is
    our infrastructure, and conflating them tells a field officer to fix a photo
    when the bucket is down.
    """


class UploadTooLarge(UploadRejected):
    """Separate class so the route can answer 413 rather than 415."""


class ObjectStoreUnavailable(RuntimeError):
    """The bucket could not be read or written. Never swallowed."""


@dataclass(frozen=True, slots=True)
class StoredImage:
    """Where one photograph's bytes ended up, and what they were."""

    object_key: str
    derivative_key: str
    #: SHA-256 of the *original* upload, hex. The only surviving link to the
    #: bytes the device produced; see the module docstring.
    original_sha256: str
    original_bytes: int


def sniff(data: bytes) -> str:
    """The container format of `data`, or raise `UploadRejected`.

    Signature-based, and it runs before any decoder does. `content-type` from the
    multipart part is not consulted anywhere in this package: it is a string the
    client chose, it is trivially wrong (`application/octet-stream` from several
    Android upload paths) and trivially forged, so believing it would make the
    allowlist decorative.
    """
    if len(data) < 12:
        raise UploadRejected("the uploaded file is empty or truncated")

    for magic, name in _SIGNATURES:
        if data.startswith(magic):
            return name

    # RIFF....WEBP — the size field sits between the two markers.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"

    # ISO base media: [4-byte size]['ftyp'][4-byte brand].
    if data[4:8] == b"ftyp":
        brand = data[8:12].lower()
        if brand in _HEIF_BRANDS:
            raise UploadRejected(
                "HEIC/HEIF images cannot be decoded by this build (pillow-heif is "
                "not installed). Please convert the photograph to JPEG and upload "
                "it again — the file itself is not damaged."
            )
        raise UploadRejected(
            f"the uploaded file is an ISO media container (brand "
            f"{brand.decode('ascii', 'replace')!r}), not a photograph"
        )

    raise UploadRejected(
        "the uploaded file is not a JPEG, PNG, WebP or TIFF image; "
        "its leading bytes match no accepted image format"
    )


def decode(data: bytes) -> Image.Image:
    """Sniff, decode and orient the upload, or raise `UploadRejected`.

    `ImageOps.exif_transpose` is applied here, at the boundary, so that every
    consumer downstream — the quality gate, the perceptual hash, the stored
    derivative — sees the image the right way up. Skipping it would make the blur
    measurement and the hash depend on how the phone was held, which is the
    definition of a measurement that cannot be reproduced.

    The decode happens twice on purpose. `Image.verify()` consumes the file
    object and leaves the image unusable, which is exactly what it is for: it
    walks the whole stream and raises on a truncated or malformed file before
    any pixels are read into memory. A single lazy `Image.open` would defer the
    error to the first `load()` deep inside the quality gate, where it would
    surface as a 500 rather than as a stated refusal.
    """
    sniff(data)

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise UploadRejected(f"the uploaded image could not be decoded: {exc}") from exc

    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise UploadRejected(f"the uploaded image could not be decoded: {exc}") from exc

    if opened.format not in ACCEPTED_FORMATS:
        raise UploadRejected(
            f"decoded format {opened.format!r} is not accepted; "
            f"accepted formats: {', '.join(sorted(ACCEPTED_FORMATS))}"
        )

    oriented = ImageOps.exif_transpose(opened)
    # `exif_transpose` returns None only for a None input, which cannot happen
    # here; the fallback keeps the checker honest without inventing a branch
    # that runs.
    return oriented if oriented is not None else opened


def _as_jpeg(image: Image.Image, *, long_edge: int | None = None) -> bytes:
    """Re-encode from pixels to a JPEG carrying no metadata.

    `convert("RGB")` first: a PNG with alpha, a CMYK TIFF and a paletted GIF all
    fail to save as JPEG otherwise, and the conversion is where transparency is
    flattened onto black. No `exif=` argument is passed, so the output has none —
    the metadata lives in `field_images.raw_exif`, where it is queryable and
    where it cannot travel with a file that gets forwarded.
    """
    out = image.convert("RGB")
    if long_edge is not None and max(out.width, out.height) > long_edge:
        scale = long_edge / float(max(out.width, out.height))
        out = out.resize(
            (max(1, round(out.width * scale)), max(1, round(out.height * scale))),
            Image.LANCZOS,
        )
    buffer = io.BytesIO()
    out.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


@lru_cache(maxsize=1)
def client() -> S3Client:
    """The one S3 client for this process.

    Cached because botocore's client construction parses the endpoint, loads the
    signing machinery and opens a connection pool — per request that is
    measurable, and the object is thread-safe for the operations used here.

    `s3v4` and path-style addressing are both required by MinIO: virtual-host
    addressing would resolve `pramaan-field-images.minio` in DNS and fail. The
    retry budget is small and bounded on purpose — a slow store must surface as a
    named failure to the officer standing at the site, not as a request that
    hangs until the gateway times out.
    """
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.object_store_endpoint,
        aws_access_key_id=settings.object_store_access_key,
        aws_secret_access_key=settings.object_store_secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 2, "mode": "standard"},
            connect_timeout=5,
            read_timeout=20,
        ),
    )


def ensure_bucket() -> None:
    """Create `BUCKET` if it is absent. Idempotent.

    Nothing else in this repository creates it — there is no bootstrap job and
    the compose file starts a bare MinIO — so the first upload would otherwise
    fail against a working store. `head_bucket` first, so the common path is one
    cheap call and `CreateBucket` is not issued on every request.

    `BucketAlreadyOwnedByYou` is caught rather than prevented: two concurrent
    first uploads can both see the bucket missing, and losing that race is not an
    error.
    """
    s3 = client()
    try:
        s3.head_bucket(Bucket=BUCKET)
        return
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise ObjectStoreUnavailable(
                f"object store rejected a bucket check for {BUCKET!r}: {exc}"
            ) from exc
    except BotoCoreError as exc:
        raise ObjectStoreUnavailable(
            f"object store at {get_settings().object_store_endpoint} is unreachable: {exc}"
        ) from exc

    try:
        s3.create_bucket(Bucket=BUCKET)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise ObjectStoreUnavailable(f"could not create bucket {BUCKET!r}: {exc}") from exc
    except BotoCoreError as exc:
        raise ObjectStoreUnavailable(f"could not create bucket {BUCKET!r}: {exc}") from exc


def keys_for(district_lgd: str, image_id: str) -> tuple[str, str]:
    """`(object_key, derivative_key)` for one image.

    District first so a district's evidence is one prefix — that is what makes a
    per-district export or retention policy a prefix operation rather than a
    full scan. The UUID is the filename because it is already unique and already
    the primary key; deriving the name from the officer's upload filename would
    put user-controlled text into a key.
    """
    base = f"field/{district_lgd}/{image_id}"
    return f"{base}.jpg", f"{base}.thumb.jpg"


def put_image(
    image: Image.Image, original: bytes, *, district_lgd: str, image_id: str
) -> StoredImage:
    """Re-encode and store the full-size image and its thumbnail.

    Raises `ObjectStoreUnavailable` on any store failure, before the caller has
    written anything to the database. See the module docstring for why that
    ordering is not negotiable.

    The digest is taken over `original` — the bytes as uploaded — not over what
    is stored, because its purpose is to let the officer prove which file they
    sent.
    """
    object_key, derivative_key = keys_for(district_lgd, image_id)
    full = _as_jpeg(image)
    thumb = _as_jpeg(image, long_edge=THUMBNAIL_LONG_EDGE)

    ensure_bucket()
    s3 = client()
    for key, payload in ((object_key, full), (derivative_key, thumb)):
        try:
            s3.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=payload,
                ContentType="image/jpeg",
            )
        except (ClientError, BotoCoreError) as exc:
            # The full-size object may already be stored when the thumbnail
            # fails. That orphan is deliberate: deleting it here would need a
            # second call that can also fail, and the caller is about to abort
            # without writing a row, so nothing will ever point at it.
            raise ObjectStoreUnavailable(f"could not store {key!r} in {BUCKET!r}: {exc}") from exc

    return StoredImage(
        object_key=object_key,
        derivative_key=derivative_key,
        original_sha256=hashlib.sha256(original).hexdigest(),
        original_bytes=len(original),
    )
