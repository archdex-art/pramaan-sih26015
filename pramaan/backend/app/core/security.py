"""Password hashing and token minting. The only module that touches crypto.

Nothing is hand-rolled here. Argon2id comes from `argon2-cffi` and RS256 from
`pyjwt[crypto]`; this module's whole job is to choose parameters, define the
claim set, and refuse the mistakes that make correct libraries insecure.

## Choices, each with a reason

- **Argon2id**, per docs §25.1. Not bcrypt: bcrypt silently truncates at 72
  bytes and has no memory-hardness parameter.
- **RS256**, per docs §25.1, over HS256. Asymmetric means a verifier needs only
  the public key, so a future report worker or gateway can validate a token
  without holding the power to mint one.
- **`typ` claim on every token, and checked on decode.** Without it an attacker
  presents a 12-hour refresh token as a 20-minute access token and gets a
  36-fold extension of their session. This is the single most common JWT
  implementation bug and it costs one line to close.
- **`jti` on refresh tokens** so reuse is detectable at all. Rotation without a
  per-token identity cannot distinguish "second use of an old token" from
  "first use of a new one".

## Key material

The private key is read from configuration. In development a keypair is
generated once into `.keys/` and gitignored, because a repository that ships a
signing key ships every session it will ever issue. There is no default key and
no fallback to a hardcoded secret — a missing key raises at startup rather than
silently degrading to something guessable.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.authz import Principal, Role
from app.core.config import get_settings

ALGORITHM: Final = "RS256"
ISSUER: Final = "pramaan"
AUDIENCE: Final = "pramaan-console"

#: OWASP's second recommended Argon2id profile: 64 MiB, 3 iterations, 4 lanes.
#: Chosen over the 19 MiB profile because this is a server-side login path with
#: no throughput pressure — an officer signs in once per shift.
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

#: Minimum password length. Length is the only knob that reliably buys entropy;
#: composition rules mostly buy "Password1!".
MIN_PASSWORD_LENGTH: Final = 12

#: Failed attempts before an account locks, and the base of the backoff.
#: docs §25.1 asks for lockout with exponential backoff.
MAX_FAILED_ATTEMPTS: Final = 5
LOCKOUT_BASE_SECONDS: Final = 30


class WeakPassword(ValueError):
    """Raised when a password fails policy. Message is safe to show a user."""


class TokenInvalid(Exception):
    """Raised for any token that must not be honoured.

    Deliberately one exception with a coarse message. Distinguishing "expired"
    from "bad signature" from "wrong audience" to the caller is free
    reconnaissance for an attacker; the server logs the detail, the client is
    told to authenticate again.
    """


def hash_password(password: str) -> str:
    """Hash after checking policy, so a weak password is never stored at all."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish verification that never raises on a bad password.

    A corrupt or truncated hash in the database is treated as a failed login
    rather than a 500. It is a data-integrity problem, but the correct answer to
    "can this person in?" is still no.
    """
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash predates the current cost parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, ValueError):
        return False


def lockout_seconds(failed_attempts: int) -> int:
    """Exponential backoff, capped.

    Zero below the threshold so a fat-fingered password does not lock an
    officer out mid-shift. Capped at an hour because an unbounded doubling
    becomes a permanent denial of service against a legitimate user, which is
    an availability failure dressed as a security control.
    """
    if failed_attempts < MAX_FAILED_ATTEMPTS:
        return 0
    # Cap the exponent, not just the product. Without this, a bot hammering an
    # account for a few hours computes 2**10000 before min() ever sees it.
    over = min(failed_attempts - MAX_FAILED_ATTEMPTS, 12)
    return min(LOCKOUT_BASE_SECONDS << over, 3600)


@dataclass(frozen=True, slots=True)
class Keypair:
    private_pem: bytes
    public_pem: bytes


@lru_cache(maxsize=1)
def get_keypair() -> Keypair:
    """Load the signing keypair, generating a development one if absent.

    Generation is confined to a path under the repo that is gitignored. It is
    not a fallback secret: if generation is impossible the exception propagates
    and the API does not start, which is the correct behaviour for a service
    whose entire authorisation story rests on this key.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    settings = get_settings()
    key_dir = Path(settings.jwt_key_dir)
    private_path = key_dir / "jwt_private.pem"
    public_path = key_dir / "jwt_public.pem"

    if private_path.is_file() and public_path.is_file():
        return Keypair(private_path.read_bytes(), public_path.read_bytes())

    key_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    private_path.write_bytes(private_pem)
    # 0600: the key is readable by its owner and nobody else. Default umask
    # would leave it world-readable on many systems.
    private_path.chmod(0o600)
    public_path.write_bytes(public_pem)
    return Keypair(private_pem, public_pem)


def _encode(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, get_keypair().private_pem, algorithm=ALGORITHM)


def _now() -> datetime:
    return datetime.now(UTC)


def issue_access_token(principal: Principal) -> tuple[str, int]:
    """Mint a short-lived access token. Returns the token and its TTL seconds.

    The principal's role and scope travel *in* the token, so an authorised
    request costs no database round trip. The tradeoff is that a role change
    takes effect at the next refresh rather than instantly — acceptable at a
    20-minute access TTL, and the reason the TTL is 20 minutes.

    **Capabilities are deliberately not a claim.** They are derived from the
    role by `CAPABILITIES` at decode time. Embedding them would put the
    authorisation policy in two places — a signed copy inside every live token
    and the map in `authz.py` — and the two would disagree the first time the
    policy changed, with the stale signed copy winning for up to a full TTL.
    Clients that need the list for UI gating read it from the login response,
    which is generated from the same map.
    """
    settings = get_settings()
    ttl = settings.jwt_access_ttl_minutes * 60
    now = _now()
    return (
        _encode(
            {
                "sub": principal.user_id,
                "username": principal.username,
                "name": principal.full_name,
                "role": str(principal.role),
                "scope_state": principal.scope_state,
                "scope_district": principal.scope_district,
                "typ": "access",
                "iss": ISSUER,
                "aud": AUDIENCE,
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=ttl)).timestamp()),
                "jti": uuid.uuid4().hex,
            }
        ),
        ttl,
    )


def issue_refresh_token(user_id: str, family: str | None = None) -> tuple[str, str, str, datetime]:
    """Mint a refresh token. Returns (token, jti, family, expiry).

    `family` threads through a rotation chain so that reuse of any token in the
    chain can revoke the whole chain rather than just the replayed token. A new
    login starts a new family.
    """
    settings = get_settings()
    now = _now()
    expires = now + timedelta(hours=settings.jwt_refresh_ttl_hours)
    jti = uuid.uuid4().hex
    fam = family or uuid.uuid4().hex
    token = _encode(
        {
            "sub": user_id,
            "typ": "refresh",
            "family": fam,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": jti,
        }
    )
    return token, jti, fam, expires


def decode(token: str, expect: Literal["access", "refresh"]) -> dict[str, Any]:
    """Verify a token and assert its type.

    `expect` is mandatory and has no default. A default would eventually be
    wrong at one call site, and the wrong one is the vulnerability: a refresh
    token accepted as an access token extends a session from 20 minutes to 12
    hours.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            get_keypair().public_pem,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenInvalid("token is not valid") from exc

    if claims.get("typ") != expect:
        raise TokenInvalid("token is not valid")
    return claims


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    """Rebuild the principal from access-token claims.

    An unknown role raises rather than defaulting. A token naming a role this
    build does not implement must not be honoured as some safe-looking
    fallback — there is no safe fallback for "I do not know what you are
    allowed to do".
    """
    try:
        role = Role(str(claims["role"]))
    except (KeyError, ValueError) as exc:
        raise TokenInvalid("token is not valid") from exc

    state = claims.get("scope_state")
    district = claims.get("scope_district")
    return Principal(
        user_id=str(claims["sub"]),
        username=str(claims.get("username", "")),
        full_name=str(claims.get("name", "")),
        role=role,
        scope_state=None if state is None else str(state),
        scope_district=None if district is None else str(district),
    )


def generate_password(length: int = 16) -> str:
    """A URL-safe random password, for seeding demo accounts.

    `secrets`, never `random`: the seeded accounts are real credentials against
    a real authorisation system, and a Mersenne Twister password is a published
    password.
    """
    return secrets.token_urlsafe(length)
