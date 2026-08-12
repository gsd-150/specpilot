"""Canonical, short-lived credentials binding an API run to its owner.

The transport boundary (bearer header or HTTP-only cookie) deliberately lives
outside this module.  Both transports pass the opaque value to the same
``SessionVerifier`` so neither can acquire weaker parsing or validation rules.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

_TOKEN_VERSION: Final = "v1"
_CLAIMS_VERSION: Final = 1
_ALGORITHM: Final = "HS256"
_MIN_SECRET_BYTES: Final = 32
_MIN_TTL_SECONDS: Final = 1
_MAX_TTL_SECONDS: Final = 300
_SIGNATURE_BYTES: Final = hashlib.sha256().digest_size
_NONCE_BYTES: Final = 16
_MAX_TOKEN_CHARS: Final = 2048
_MAX_PAYLOAD_BYTES: Final = 1024
_MAX_IDENTIFIER_CHARS: Final = 128
_IDENTIFIER_CHARS: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-"
)
_CLAIM_KEYS: Final = frozenset(
    {"alg", "aud", "exp", "iat", "nonce", "profile", "session_id", "v"}
)

Clock = Callable[[], datetime]


class SessionTokenError(RuntimeError):
    """Stable, detail-free public session failure."""

    def __init__(self, code: Literal["invalid_session", "expired_session"]) -> None:
        super().__init__(code)


class SessionClaims(BaseModel):
    """Verified owner identity and deployment bindings, with no signing state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: str
    profile: str
    audience: str
    nonce: str
    version: Literal[1]
    issued_at: datetime
    expires_at: datetime

    @field_validator("session_id", "profile", "audience")
    @classmethod
    def _identifiers_are_exact(cls, value: str) -> str:
        return _validated_identifier(value)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def _timestamps_are_aware_utc(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class SessionIssuer:
    """Mint a canonical HMAC credential with a maximum five-minute lifetime."""

    __slots__ = ("_audience", "_clock", "_secret")

    def __init__(self, *, secret: bytes, audience: str, clock: Clock) -> None:
        self._secret = _validated_secret(secret)
        self._audience = _validated_identifier(audience)
        self._clock = _validated_clock(clock)

    def issue(self, *, session_id: str, profile: str, ttl_seconds: int) -> str:
        try:
            return self._issue(
                session_id=session_id,
                profile=profile,
                ttl_seconds=ttl_seconds,
            )
        except Exception:
            pass
        # Construct the stable error after leaving the handler so an injected
        # clock or entropy failure cannot remain attached as ``__context__``.
        raise SessionTokenError("invalid_session") from None

    def _issue(self, *, session_id: str, profile: str, ttl_seconds: int) -> str:
        issued = _clock_seconds(self._clock)
        validated_session_id = _validated_identifier(session_id)
        validated_profile = _validated_identifier(profile)
        ttl = _validated_ttl(ttl_seconds)
        expires = issued + ttl
        datetime.fromtimestamp(expires, tz=UTC)
        claims = {
            "alg": _ALGORITHM,
            "aud": self._audience,
            "exp": expires,
            "iat": issued,
            "nonce": _encode_base64url(secrets.token_bytes(_NONCE_BYTES)),
            "profile": validated_profile,
            "session_id": validated_session_id,
            "v": _CLAIMS_VERSION,
        }
        payload_segment = _encode_base64url(_canonical_json(claims))
        signing_input = f"{_TOKEN_VERSION}.{payload_segment}".encode("ascii")
        signature = hmac.new(self._secret, signing_input, hashlib.sha256).digest()
        return f"{_TOKEN_VERSION}.{payload_segment}.{_encode_base64url(signature)}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(audience={self._audience!r})"


class SessionVerifier:
    """Strictly parse and authenticate a canonical session credential."""

    __slots__ = ("_audience", "_clock", "_profile", "_secret")

    def __init__(
        self, *, secret: bytes, audience: str, profile: str, clock: Clock
    ) -> None:
        self._secret = _validated_secret(secret)
        self._audience = _validated_identifier(audience)
        self._profile = _validated_identifier(profile)
        self._clock = _validated_clock(clock)

    def verify(self, token: str) -> SessionClaims:
        error_code: Literal["invalid_session", "expired_session"]
        try:
            return self._verify(token)
        except SessionTokenError as error:
            error_code = cast(
                Literal["invalid_session", "expired_session"], str(error)
            )
        except (
            AttributeError,
            binascii.Error,
            json.JSONDecodeError,
            UnicodeError,
            OverflowError,
            OSError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            error_code = "invalid_session"
        # Raise after leaving the handler.  Otherwise Python retains the parser
        # exception as ``__context__``, which can carry token-derived JSON.
        raise SessionTokenError(error_code) from None

    def _verify(self, token: str) -> SessionClaims:
        try:
            now_seconds = _clock_seconds(self._clock)
            version, payload_segment, signature_segment = _split_token(token)
            payload = _decode_base64url(payload_segment, max_bytes=_MAX_PAYLOAD_BYTES)
            signature = _decode_base64url(
                signature_segment, expected_bytes=_SIGNATURE_BYTES
            )
            signing_input = f"{version}.{payload_segment}".encode("ascii")
            expected_signature = hmac.new(
                self._secret, signing_input, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(signature, expected_signature):
                raise ValueError
            raw_claims = _load_claims(payload)
            _validate_claims_shape(raw_claims)
            issued = _strict_timestamp(raw_claims["iat"])
            expires = _strict_timestamp(raw_claims["exp"])
            if expires - issued not in range(
                _MIN_TTL_SECONDS, _MAX_TTL_SECONDS + 1
            ):
                raise ValueError
            if (
                raw_claims["v"] != _CLAIMS_VERSION
                or isinstance(raw_claims["v"], bool)
                or raw_claims["alg"] != _ALGORITHM
                or raw_claims["aud"] != self._audience
                or raw_claims["profile"] != self._profile
            ):
                raise ValueError
            session_id = _validated_identifier(raw_claims["session_id"])
            claims_profile = _validated_identifier(raw_claims["profile"])
            audience = _validated_identifier(raw_claims["aud"])
            nonce = _validated_nonce(raw_claims["nonce"])
            if issued > now_seconds:
                raise ValueError
            if now_seconds >= expires:
                raise SessionTokenError("expired_session")
            return SessionClaims(
                session_id=session_id,
                profile=claims_profile,
                audience=audience,
                nonce=nonce,
                version=_CLAIMS_VERSION,
                issued_at=datetime.fromtimestamp(issued, tz=UTC),
                expires_at=datetime.fromtimestamp(expires, tz=UTC),
            )
        except SessionTokenError:
            raise

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(audience={self._audience!r}, "
            f"profile={self._profile!r})"
        )


def _validated_secret(secret: bytes) -> bytes:
    if type(secret) is not bytes or len(secret) < _MIN_SECRET_BYTES:
        raise SessionTokenError("invalid_session") from None
    return secret


def _validated_clock(clock: Clock) -> Clock:
    if not callable(clock):
        raise SessionTokenError("invalid_session") from None
    return clock


def _validated_identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= _MAX_IDENTIFIER_CHARS
        or value[0] not in _IDENTIFIER_CHARS
        or any(character not in _IDENTIFIER_CHARS for character in value)
    ):
        raise SessionTokenError("invalid_session") from None
    return value


def _validated_ttl(value: int) -> int:
    if (
        type(value) is not int
        or value < _MIN_TTL_SECONDS
        or value > _MAX_TTL_SECONDS
    ):
        raise SessionTokenError("invalid_session") from None
    return value


def _aware_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise SessionTokenError("invalid_session") from None
    return value.astimezone(UTC)


def _clock_seconds(clock: Clock) -> int:
    invalid = False
    try:
        value = clock()
        aware = _aware_utc(value)
        raw = aware.timestamp()
        if not math.isfinite(raw) or raw < 0:
            raise ValueError
        seconds = int(raw)
        datetime.fromtimestamp(seconds, tz=UTC)
    except Exception:
        invalid = True
        seconds = 0
    if invalid:
        raise SessionTokenError("invalid_session") from None
    return seconds


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(
    segment: str,
    *,
    expected_bytes: int | None = None,
    max_bytes: int | None = None,
) -> bytes:
    if (
        type(segment) is not str
        or not segment
        or "=" in segment
        or any(
            character
            not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for character in segment
        )
    ):
        raise ValueError
    if len(segment) % 4 == 1:
        raise ValueError
    if expected_bytes is not None and len(segment) != (expected_bytes * 8 + 5) // 6:
        raise ValueError
    if max_bytes is not None and len(segment) > (max_bytes * 8 + 5) // 6:
        raise ValueError
    decoded = base64.b64decode(
        segment + "=" * (-len(segment) % 4), altchars=b"-_", validate=True
    )
    if _encode_base64url(decoded) != segment:
        raise ValueError
    if expected_bytes is not None and len(decoded) != expected_bytes:
        raise ValueError
    if max_bytes is not None and len(decoded) > max_bytes:
        raise ValueError
    return decoded


def _split_token(token: str) -> tuple[str, str, str]:
    if type(token) is not str or not token or len(token) > _MAX_TOKEN_CHARS:
        raise ValueError
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        raise ValueError
    return parts[0], parts[1], parts[2]


def _reject_duplicate_claim(name: str, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    del name
    claims: dict[str, Any] = {}
    for key, value in pairs:
        if key in claims:
            raise ValueError
        claims[key] = value
    return claims


def _reject_nonfinite_number(value: str) -> None:
    del value
    raise ValueError


def _load_claims(payload: bytes) -> dict[str, Any]:
    claims = json.loads(
        payload,
        object_pairs_hook=lambda pairs: _reject_duplicate_claim("claim", pairs),
        parse_constant=_reject_nonfinite_number,
    )
    if type(claims) is not dict or _canonical_json(claims) != payload:
        raise ValueError
    return claims


def _validate_claims_shape(claims: dict[str, Any]) -> None:
    if frozenset(claims) != _CLAIM_KEYS:
        raise ValueError
    for key in ("alg", "aud", "nonce", "profile", "session_id"):
        if type(claims[key]) is not str:
            raise ValueError
    if type(claims["v"]) is not int:
        raise ValueError


def _strict_timestamp(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError
    datetime.fromtimestamp(value, tz=UTC)
    return value


def _validated_nonce(value: str) -> str:
    nonce = _decode_base64url(value, expected_bytes=_NONCE_BYTES)
    if not nonce:
        raise ValueError
    return value
