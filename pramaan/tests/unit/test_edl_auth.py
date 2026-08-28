"""Tests for the Earthdata Login presign resolver.

These use a stubbed transport rather than the live DAAC: the behaviour being
protected is *our* redirect handling, and a test that needs a credential and a
network is a test nobody runs.

What matters here is the one thing that is easy to "simplify" later and thereby
break silently: the bearer token must NOT be carried onto the presigned URL.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.satellite.edl_auth import (  # noqa: E402
    GDAL_COG_ENV,
    OPTIMAL_CONCURRENCY,
    EarthdataTokenMissing,
    EdlResolver,
)

LP_URL = (
    "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/HLSS30.020/"
    "HLS.S30.T43QGB.2024276T051649.v2.0/HLS.S30.T43QGB.2024276T051649.v2.0.B04.tif"
)
PRESIGNED = (
    "https://d1nklfio7vscoe.cloudfront.net/s3-abc/lp-prod-protected.s3.us-west-2"
    ".amazonaws.com/HLSS30.020/x.B04.tif?X-Amz-Signature=deadbeef"
)
AWS_OPEN = (
    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
    "43/Q/FA/2024/12/S2B_43QFA_20241231_0_L2A/B04.tif"
)


def stub_client(recorder: list[httpx.Request], status: int = 303) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        if status in (301, 302, 303, 307, 308):
            return httpx.Response(status, headers={"location": PRESIGNED})
        return httpx.Response(status)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolves_lp_daac_redirect_to_presigned_url(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok-123")
    seen: list[httpx.Request] = []
    with stub_client(seen) as c:
        assert EdlResolver().resolve(LP_URL, client=c) == PRESIGNED
    assert len(seen) == 1


def test_bearer_is_sent_to_the_daac_host(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok-123")
    seen: list[httpx.Request] = []
    with stub_client(seen) as c:
        EdlResolver().resolve(LP_URL, client=c)
    assert seen[0].headers["authorization"] == "Bearer tok-123"


def test_only_one_request_is_made_so_the_token_cannot_reach_cloudfront(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """The load-bearing assertion.

    AWS rejects a request carrying both a presigned query signature and an
    Authorization header. If someone "simplifies" this to
    `follow_redirects=True`, the client would make a second request to
    CloudFront *with* the bearer header and every read would fail with a
    misleading 'not recognized as being in a supported file format'.

    One request to the DAAC host, and the presigned URL handed onward untouched,
    is the entire contract.
    """
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok-123")
    seen: list[httpx.Request] = []
    with stub_client(seen) as c:
        result = EdlResolver().resolve(LP_URL, client=c)
    assert len(seen) == 1, "resolver must not follow the redirect itself"
    assert "cloudfront" not in str(seen[0].url)
    assert result == PRESIGNED


def test_non_edl_urls_pass_through_untouched(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """AWS open data needs no presign, and must not require a token."""
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    seen: list[httpx.Request] = []
    with stub_client(seen) as c:
        assert EdlResolver().resolve(AWS_OPEN, client=c) == AWS_OPEN
    assert seen == [], "no request should be made for a non-EDL host"


def test_missing_token_fails_with_an_actionable_message(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    with pytest.raises(EarthdataTokenMissing, match="urs.earthdata.nasa.gov"):
        EdlResolver().resolve(LP_URL)


def test_missing_token_message_names_the_misleading_gdal_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Whoever hits this should not have to rediscover the diagnosis."""
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    with pytest.raises(EarthdataTokenMissing, match="supported file format"):
        EdlResolver().resolve(LP_URL)


def test_401_is_reported_as_an_expired_or_bad_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EARTHDATA_TOKEN", "stale")
    seen: list[httpx.Request] = []
    with (
        stub_client(seen, status=401) as c,
        pytest.raises(EarthdataTokenMissing, match="Tokens expire"),
    ):
        EdlResolver().resolve(LP_URL, client=c)


def test_redirect_without_location_header_fails_loudly(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(303)  # no Location

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as c,
        pytest.raises(RuntimeError, match="no Location header"),
    ):
        EdlResolver().resolve(LP_URL, client=c)


def test_direct_200_is_returned_as_is(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
    seen: list[httpx.Request] = []
    with stub_client(seen, status=200) as c:
        assert EdlResolver().resolve(LP_URL, client=c) == LP_URL


def test_resolver_creates_and_closes_its_own_client(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The owns_client branch: a self-made client must be closed, not leaked.

    Patched rather than networked. A leaked httpx.Client per band-read would
    exhaust file descriptors partway through a 3,000-read district ingest — the
    kind of failure that only appears at full scale.
    """
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok-123")
    closed: list[bool] = []
    real_client = httpx.Client

    def factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(303, headers={"location": PRESIGNED})

        c = real_client(transport=httpx.MockTransport(handler))
        original_close = c.close

        def tracked_close() -> None:
            closed.append(True)
            original_close()

        c.close = tracked_close  # type: ignore[method-assign]
        return c

    monkeypatch.setattr(httpx, "Client", factory)
    assert EdlResolver().resolve(LP_URL) == PRESIGNED
    assert closed == [True], "resolver leaked the client it created"


def test_missing_token_raises_before_any_client_is_created(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    with pytest.raises(EarthdataTokenMissing):
        EdlResolver().resolve(LP_URL)


def test_needs_presign_recognises_the_protected_hosts() -> None:
    assert EdlResolver.needs_presign(LP_URL)
    assert not EdlResolver.needs_presign(AWS_OPEN)


def test_gdal_env_disables_readdir_on_open() -> None:
    """Without this, GDAL lists the directory on every open and reads cost ~2x."""
    assert GDAL_COG_ENV["GDAL_DISABLE_READDIR_ON_OPEN"] == "EMPTY_DIR"
    assert GDAL_COG_ENV["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] == ".tif"


def test_concurrency_default_is_the_measured_optimum() -> None:
    """8 was measured; 12 regressed below the 4-worker rate under throttling.

    Pinned so that raising it is a deliberate act accompanied by a new
    measurement, rather than an intuition that makes the pipeline slower.
    """
    assert OPTIMAL_CONCURRENCY == 8
