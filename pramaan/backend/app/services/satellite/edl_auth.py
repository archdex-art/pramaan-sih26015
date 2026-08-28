"""NASA Earthdata Login (EDL) access for HLS assets.

This module exists because the obvious way to do this does not work, and the
failure is silent enough to cost a day. Everything below was measured against
LP DAAC on 2026-08-28.

## The problem

HLS granules live behind EDL. A bearer token works fine with a plain HTTP
client, but handing the same URL to GDAL/rasterio fails with:

    '/vsicurl/https://data.lpdaac.earthdatacloud.nasa.gov/...B04.tif'
    not recognized as being in a supported file format.

which reads like a corrupt file and is actually an authentication failure.

## Why

    GET https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/...B04.tif
        Authorization: Bearer <token>
    -> 303 See Other
       Location: https://d1nklfio7vscoe.cloudfront.net/...?X-Amz-Signature=...

LP DAAC redirects to a **CloudFront presigned URL on a different host**. That
presigned URL needs no credentials at all — fetching it with no `Authorization`
header returns `206` and a valid TIFF.

GDAL sets custom headers via `CURLOPT_HTTPHEADER`, which curl sends on *every*
request in a redirect chain. So GDAL forwards `Authorization: Bearer ...` to
CloudFront, and AWS rejects a request that carries both a presigned query
signature and an `Authorization` header. Hence the unparseable body.

Note this is *not* fixed by `GDAL_HTTP_BEARER`, `GDAL_HTTP_AUTH=BEARER`, or a
cookie jar — all three were tried and all three fail identically.

## The fix

Resolve the redirect ourselves with the bearer token, then hand GDAL the
presigned URL with no auth at all. Measured: zero failures across 64 reads at
1/4/8/12 concurrency.

## A second trap: never probe a presigned URL with HEAD

An AWS presigned URL is signed for **one specific HTTP method**. A `HEAD`
against a GET-presign returns:

    403 Forbidden  <Error><Code>SignatureDoesNotMatch</Code>

which again reads like an auth failure when the token is perfectly good and
only the verb is wrong. Anything needing an object's size must use a ranged
`GET` and read `Content-Range`, which is what
`scripts/measure_window_cost.py` does. This resolver deliberately exposes no
size probe so the mistake has nowhere to live.

## Why not direct S3

LP DAAC publishes an `/s3credentials` endpoint that returns temporary AWS
credentials. It works (HTTP 200) but the role granted is
`s3-same-region-access-role`, and from outside `us-west-2` every call is denied:

    User: .../s3-same-region-access-role/<uid> is not authorized to perform:
    s3:ListBucket ... with an explicit deny in an identity-based policy

So direct S3 is only an option for compute running in AWS us-west-2. For a demo
VM anywhere else — including the SIH venue — HTTPS plus this presign step is the
only path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

#: Hosts that issue EDL-protected redirects. A URL on one of these needs the
#: presign step; anything else (AWS open data, for instance) is passed through.
EDL_PROTECTED_HOSTS = (
    "data.lpdaac.earthdatacloud.nasa.gov",
    "data.ornldaac.earthdata.nasa.gov",
    "archive.podaac.earthdata.nasa.gov",
)

#: GDAL configuration for windowed COG reads over HTTPS.
#:
#: `GDAL_DISABLE_READDIR_ON_OPEN` is not optional: without it GDAL issues a
#: directory listing on every open, which roughly doubles the cost of a read.
GDAL_COG_ENV: dict[str, str] = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "67108864",
}

#: Measured optimum. Scaling from 1 -> 8 workers is 5.2x; going to 12 workers
#: REGRESSES to below the 4-worker rate as server-side throttling engages.
#: Raising this without re-measuring will make the pipeline slower, not faster.
#:
#:     workers   reads/s
#:           1      0.22
#:           4      0.68
#:           8      1.14   <- optimum
#:          12      0.61   <- throttled
OPTIMAL_CONCURRENCY = 8


class EarthdataTokenMissing(RuntimeError):
    """Raised when an EDL-protected asset is requested with no token configured."""


@dataclass(frozen=True, slots=True)
class EdlResolver:
    """Resolves EDL-protected asset URLs to anonymously-fetchable presigned URLs.

    The token is read from the environment and never stored on the instance's
    repr, logged, or written to a lineage record. A verdict's lineage records the
    *asset URL*, never the credential used to reach it.
    """

    token_env_var: str = "EARTHDATA_TOKEN"
    timeout_s: float = 120.0

    def token(self) -> str:
        tok = os.environ.get(self.token_env_var, "").strip()
        if not tok:
            raise EarthdataTokenMissing(
                f"{self.token_env_var} is not set. HLS assets are EDL-protected: "
                "register free at https://urs.earthdata.nasa.gov and generate a "
                "user token. Without it the satellite worker cannot read HLS, and "
                "GDAL's failure mode is a misleading 'not recognized as being in "
                "a supported file format'."
            )
        return tok

    @staticmethod
    def needs_presign(url: str) -> bool:
        return any(host in url for host in EDL_PROTECTED_HOSTS)

    def resolve(self, url: str, client: httpx.Client | None = None) -> str:
        """Return a URL that GDAL can open with no credentials.

        Pass-through for anything not on an EDL-protected host, so the satellite
        worker can mix HLS and AWS open-data sources without branching.
        """
        if not self.needs_presign(url):
            return url

        headers = {
            "Authorization": f"Bearer {self.token()}",
            "User-Agent": "PRAMAAN-SIH2026/0.1",
        }
        owns_client = client is None
        c = client or httpx.Client(timeout=self.timeout_s)
        try:
            # follow_redirects=False is the whole point: we must intercept the
            # 303 rather than let the client carry the bearer token onward to
            # CloudFront, which rejects signature + Authorization together.
            r = c.get(url, headers=headers, follow_redirects=False)
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("location")
                if not location:
                    raise RuntimeError(f"EDL host returned {r.status_code} with no Location header")
                return str(location)
            if r.status_code == 401:
                raise EarthdataTokenMissing(
                    f"EDL rejected the token in {self.token_env_var} (HTTP 401). "
                    "Tokens expire — check the `exp` claim and regenerate."
                )
            # 200 without a redirect: unusual, but the URL is directly readable.
            return url
        finally:
            if owns_client:
                c.close()
