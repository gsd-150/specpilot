from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

import pytest

from specpilot.sessions.tokens import (
    SessionIssuer,
    SessionTokenError,
    SessionVerifier,
)

SECRET = b"test-session-signing-secret-32b!"
OTHER_SECRET = b"other-session-signing-secret-32!"
NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.now


class FailingClock:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        raise self.error


class ExplodingTimestamp(datetime):
    def timestamp(self) -> float:
        raise RuntimeError("timestamp raw marker")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _valid_claims(**changes: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "alg": "HS256",
        "aud": "specpilot-api",
        "exp": 1786521900,
        "iat": 1786521600,
        "nonce": "MDEyMzQ1Njc4OWFiY2RlZg",
        "profile": "fixture",
        "session_id": "session-a",
        "v": 1,
    }
    claims.update(changes)
    return claims


def _signed_payload(payload: bytes, *, secret: bytes = SECRET) -> str:
    payload_segment = _encode(payload)
    signed = f"v1.{payload_segment}".encode("ascii")
    signature = hmac.new(secret, signed, hashlib.sha256).digest()
    return f"v1.{payload_segment}.{_encode(signature)}"


def _signed_claims(**changes: Any) -> str:
    payload = json.dumps(
        _valid_claims(**changes),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return _signed_payload(payload)


def _verifier(
    *,
    secret: bytes = SECRET,
    audience: str = "specpilot-api",
    profile: str = "fixture",
    clock: Clock | None = None,
) -> SessionVerifier:
    return SessionVerifier(
        secret=secret,
        audience=audience,
        profile=profile,
        clock=clock or Clock(),
    )


def _assert_invalid(token: str, verifier: SessionVerifier | None = None) -> None:
    with pytest.raises(SessionTokenError, match="^invalid_session$") as caught:
        (verifier or _verifier()).verify(token)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_token_is_short_lived_profile_bound_and_tamper_evident() -> None:
    """Catches unsigned payload acceptance or missing ownership claims."""
    issue_clock = Clock()
    token = SessionIssuer(
        secret=SECRET, audience="specpilot-api", clock=issue_clock
    ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
    claims = _verifier().verify(token)

    assert claims.session_id == "session-a"
    assert claims.profile == "fixture"
    assert claims.audience == "specpilot-api"
    assert claims.version == 1
    assert len(_decode(claims.nonce)) == 16
    assert claims.expires_at - claims.issued_at == timedelta(seconds=300)
    _assert_invalid(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_issue_and_verify_call_their_clocks_once() -> None:
    """Catches internally inconsistent claims produced from multiple clock reads."""
    issue_clock = Clock()
    issuer = SessionIssuer(
        secret=SECRET, audience="specpilot-api", clock=issue_clock
    )
    token = issuer.issue(session_id="session-a", profile="fixture", ttl_seconds=1)
    verify_clock = Clock()

    _verifier(clock=verify_clock).verify(token)

    assert issue_clock.calls == 1
    assert verify_clock.calls == 1


def test_rejected_issue_and_verify_still_call_their_clocks_once() -> None:
    """Catches conditional clock reads making one operation sample zero times."""
    issue_clock = Clock()
    issuer = SessionIssuer(
        secret=SECRET, audience="specpilot-api", clock=issue_clock
    )
    with pytest.raises(SessionTokenError, match="^invalid_session$"):
        issuer.issue(session_id=" session-a", profile="fixture", ttl_seconds=300)
    verify_clock = Clock()
    with pytest.raises(SessionTokenError, match="^invalid_session$"):
        _verifier(clock=verify_clock).verify("not-a-token")

    assert issue_clock.calls == 1
    assert verify_clock.calls == 1


def test_subsecond_aware_clock_issues_integer_claims() -> None:
    """Catches requiring production clocks to land exactly on whole seconds."""
    clock = Clock(NOW.replace(microsecond=500_000))

    token = SessionIssuer(
        secret=SECRET, audience="specpilot-api", clock=clock
    ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
    claims = _verifier(clock=clock).verify(token)

    assert claims.issued_at == NOW
    assert claims.expires_at == NOW + timedelta(seconds=300)


@pytest.mark.parametrize("operation", ["issue", "verify"])
def test_arbitrary_clock_failures_are_sanitized(operation: str) -> None:
    """Catches raw injected-clock exceptions escaping the session boundary."""
    clock = FailingClock(RuntimeError("clock leaked raw detail"))
    with pytest.raises(SessionTokenError, match="^invalid_session$") as caught:
        if operation == "issue":
            SessionIssuer(
                secret=SECRET, audience="specpilot-api", clock=clock
            ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
        else:
            _verifier(clock=clock).verify(_signed_claims())

    assert clock.calls == 1
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "raw detail" not in repr(caught.value)


@pytest.mark.parametrize("operation", ["issue", "verify"])
@pytest.mark.parametrize("failure", [SystemExit("stop"), KeyboardInterrupt()])
def test_clock_base_exceptions_are_not_swallowed(
    operation: str, failure: BaseException
) -> None:
    """Catches process-control exceptions becoming authentication failures."""
    class BaseExceptionClock:
        def __call__(self) -> datetime:
            raise failure

    clock = BaseExceptionClock()
    with pytest.raises(type(failure)):
        if operation == "issue":
            SessionIssuer(
                secret=SECRET, audience="specpilot-api", clock=clock
            ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
        else:
            _verifier(clock=clock).verify(_signed_claims())


@pytest.mark.parametrize("secret", ["x" * 32, b"x" * 31, bytearray(b"x" * 32)])
@pytest.mark.parametrize("owner", [SessionIssuer, SessionVerifier])
def test_signing_key_must_be_immutable_bytes_of_at_least_32_bytes(
    secret: object, owner: type[SessionIssuer] | type[SessionVerifier]
) -> None:
    """Catches text, short, or mutable signing keys entering token state."""
    with pytest.raises(SessionTokenError, match="^invalid_session$") as caught:
        if owner is SessionIssuer:
            owner(secret=secret, audience="specpilot-api", clock=Clock())  # type: ignore[arg-type]
        else:
            owner(  # type: ignore[arg-type]
                secret=secret,
                audience="specpilot-api",
                profile="fixture",
                clock=Clock(),
            )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert str(secret) not in repr(caught.value)


@pytest.mark.parametrize(
    ("session_id", "profile", "audience"),
    [
        (" session-a", "fixture", "specpilot-api"),
        ("session-a ", "fixture", "specpilot-api"),
        ("session-a", " fixture", "specpilot-api"),
        ("session-a", "fixture ", "specpilot-api"),
        ("session-a", "fixture", " specpilot-api"),
        ("session-a", "fixture", "specpilot-api "),
        ("session\u2010a", "fixture", "specpilot-api"),
        ("session-a", "f\u0456xture", "specpilot-api"),
    ],
)
def test_issue_rejects_transformed_or_confusable_identifiers(
    session_id: str, profile: str, audience: str
) -> None:
    """Catches normalization or Unicode confusables creating owner aliases."""
    with pytest.raises(SessionTokenError, match="^invalid_session$"):
        SessionIssuer(secret=SECRET, audience=audience, clock=Clock()).issue(
            session_id=session_id,
            profile=profile,
            ttl_seconds=300,
        )


@pytest.mark.parametrize("ttl", [True, False, 0, -1, 301, 1.0, "300"])
def test_issue_rejects_non_integer_or_out_of_bounds_ttl(ttl: object) -> None:
    """Catches coercion or unbounded credential lifetimes."""
    issuer = SessionIssuer(secret=SECRET, audience="specpilot-api", clock=Clock())
    with pytest.raises(SessionTokenError, match="^invalid_session$"):
        issuer.issue(  # type: ignore[arg-type]
            session_id="session-a", profile="fixture", ttl_seconds=ttl
        )


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 8, 12, 8, 0),
        datetime.max.replace(tzinfo=UTC),
        datetime(1960, 1, 1, tzinfo=UTC),
    ],
)
def test_issue_rejects_unusable_clock_values(now: datetime) -> None:
    """Catches naive, negative, or overflowing token timestamps."""
    issuer = SessionIssuer(secret=SECRET, audience="specpilot-api", clock=Clock(now))
    with pytest.raises(SessionTokenError, match="^invalid_session$") as caught:
        issuer.issue(session_id="session-a", profile="fixture", ttl_seconds=300)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_claims_payload_is_canonical_base64url_json_without_padding() -> None:
    """Catches nondeterministic JSON or padded/non-URL-safe token encoding."""
    token = SessionIssuer(
        secret=SECRET, audience="specpilot-api", clock=Clock()
    ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
    version, payload_segment, signature_segment = token.split(".")
    payload = _decode(payload_segment)

    assert version == "v1"
    assert "=" not in token
    assert "+" not in token
    assert "/" not in token
    assert payload_segment == _encode(payload)
    assert payload == json.dumps(
        json.loads(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    assert len(_decode(signature_segment)) == hashlib.sha256().digest_size


def test_verify_rejects_wrong_secret_audience_and_profile() -> None:
    """Catches failure to bind credentials to deployment and runtime profile."""
    token = _signed_claims()

    _assert_invalid(token, _verifier(secret=OTHER_SECRET))
    _assert_invalid(token, _verifier(audience="other-api"))
    _assert_invalid(token, _verifier(profile="real"))


def test_verifier_always_rejects_token_for_another_profile() -> None:
    """Catches profile checking being optional or disabled by a caller."""
    token = _signed_claims(profile="fixture")

    _assert_invalid(token, _verifier(profile="real"))


def test_verifier_constructor_requires_profile_at_runtime() -> None:
    """Catches restoring a constructor path with no mandatory profile binding."""
    with pytest.raises(TypeError):
        SessionVerifier(  # type: ignore[call-arg]
            secret=SECRET,
            audience="specpilot-api",
            clock=Clock(),
        )


@pytest.mark.parametrize(
    "profile", [" fixture", "fixture ", "f\u0456xture", b"fixture"]
)
def test_verifier_constructor_rejects_transformed_profiles(profile: object) -> None:
    """Catches normalized or confusable deployment profiles authorizing tokens."""
    with pytest.raises(SessionTokenError, match="^invalid_session$"):
        SessionVerifier(
            secret=SECRET,
            audience="specpilot-api",
            profile=profile,  # type: ignore[arg-type]
            clock=Clock(),
        )


def test_exact_expiry_boundary_is_expired() -> None:
    """Catches accepting a credential when now equals its expiry."""
    token = _signed_claims()
    clock = Clock(NOW + timedelta(seconds=300))

    with pytest.raises(SessionTokenError, match="^expired_session$") as caught:
        _verifier(clock=clock).verify(token)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_verify_rejects_future_issued_time_without_clock_skew() -> None:
    """Catches accepting credentials not yet issued by the trusted clock."""
    _assert_invalid(_signed_claims(iat=1786521601, exp=1786521901))


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iat", True),
        ("iat", 1786521600.0),
        ("iat", -1),
        ("exp", False),
        ("exp", 1786521900.0),
        ("exp", -1),
    ],
)
def test_verify_rejects_non_integer_or_negative_timestamps(
    claim: str, value: object
) -> None:
    """Catches JSON numeric coercion at the temporal authorization boundary."""
    _assert_invalid(_signed_claims(**{claim: value}))


@pytest.mark.parametrize(
    ("iat", "exp"),
    [
        (1786521600, 1786521600),
        (1786521600, 1786521599),
        (1786521600, 1786521901),
    ],
)
def test_verify_rejects_zero_negative_or_overlong_signed_lifetime(
    iat: int, exp: int
) -> None:
    """Catches validly signed credentials outside the one-to-five-minute bound."""
    _assert_invalid(_signed_claims(iat=iat, exp=exp))


@pytest.mark.parametrize("key", list(_valid_claims()))
def test_verify_rejects_each_missing_claim(key: str) -> None:
    """Catches partial signed claims being interpreted with defaults."""
    claims = _valid_claims()
    del claims[key]
    payload = json.dumps(
        claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    _assert_invalid(_signed_payload(payload))


def test_verify_rejects_unknown_claim() -> None:
    """Catches an extensible claims bag bypassing the closed token contract."""
    _assert_invalid(_signed_claims(role="admin"))


@pytest.mark.parametrize(
    "changes",
    [
        {"v": 2},
        {"v": True},
        {"alg": "none"},
        {"alg": "hs256"},
        {"session_id": " session-a"},
        {"profile": "fixture "},
        {"aud": "specpilot-api\n"},
        {"session_id": "session\u2010a"},
        {"nonce": "AA"},
    ],
)
def test_verify_rejects_wrong_metadata_or_nonexact_identifiers(
    changes: dict[str, object]
) -> None:
    """Catches algorithm/version downgrade and authorization normalization."""
    _assert_invalid(_signed_claims(**changes))


def test_verify_rejects_duplicate_claim_names_even_with_a_valid_signature() -> None:
    """Catches parser disagreement over duplicate security-sensitive fields."""
    payload = (
        b'{"alg":"HS256","aud":"specpilot-api","exp":1786521900,'
        b'"iat":1786521600,"nonce":"MDEyMzQ1Njc4OWFiY2RlZg",'
        b'"profile":"fixture","session_id":"session-a",'
        b'"session_id":"session-b","v":1}'
    )

    _assert_invalid(_signed_payload(payload))


@pytest.mark.parametrize(
    "payload",
    [
        b'{"alg":NaN}',
        b"[]",
        b"null",
        b"not-json",
        b"\xff",
    ],
)
def test_verify_rejects_invalid_json_shapes(payload: bytes) -> None:
    """Catches non-object, non-finite, or undecodable signed payloads."""
    _assert_invalid(_signed_payload(payload))


def test_verify_rejects_noncanonical_json_even_with_a_valid_signature() -> None:
    """Catches whitespace/key-order malleability in otherwise valid claims."""
    payload = json.dumps(_valid_claims(), sort_keys=False, indent=1).encode("utf-8")
    _assert_invalid(_signed_payload(payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda token: token.replace(".", "=.", 1),
        lambda token: token + "=",
        lambda token: token.replace("_", "/", 1),
        lambda token: token + ".extra",
        lambda token: token.rsplit(".", 1)[0] + ".AA",
        lambda token: "v2" + token[2:],
        lambda token: token.replace(".", "..", 1),
    ],
)
def test_verify_rejects_malformed_or_noncanonical_base64_variants(
    mutate: Any,
) -> None:
    """Catches padding, alternate alphabet, segment, and signature malleability."""
    token = _signed_claims(session_id="session_a")
    _assert_invalid(mutate(token))


def test_verify_rejects_noncanonical_unused_base64_bits() -> None:
    """Catches alternate encodings that decode to the same signature bytes."""
    token = _signed_claims()
    signing_input, signature = token.rsplit(".", 1)
    assert signature[-1] == "E"

    _assert_invalid(f"{signing_input}.{signature[:-1]}F")


def test_verify_rejects_huge_token_before_claim_parsing() -> None:
    """Catches unbounded token or claims memory consumption."""
    _assert_invalid("v1." + "A" * 5000 + "." + "A" * 43)


def test_verify_rejects_huge_signed_claims_independently_of_token_limit() -> None:
    """Catches unbounded decoded claims below the overall token size limit."""
    claims = _valid_claims(extra="A" * 850)
    payload = json.dumps(
        claims, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    token = _signed_payload(payload)
    assert len(token) < 2048

    _assert_invalid(token)


def test_signature_comparison_uses_constant_time_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches replacing compare_digest with ordinary byte equality."""
    calls: list[tuple[bytes, bytes]] = []
    original = hmac.compare_digest

    def reject(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return False

    monkeypatch.setattr(hmac, "compare_digest", reject)
    _assert_invalid(_signed_claims())
    monkeypatch.setattr(hmac, "compare_digest", original)

    assert len(calls) == 1
    assert len(calls[0][0]) == 32
    assert len(calls[0][1]) == 32


def test_signature_length_is_rejected_before_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches comparison being reached before fixed signature-size validation."""
    calls = 0

    def compare(left: bytes, right: bytes) -> bool:
        del left, right
        nonlocal calls
        calls += 1
        return False

    monkeypatch.setattr(hmac, "compare_digest", compare)
    token = _signed_claims().rsplit(".", 1)[0] + ".AA"

    _assert_invalid(token)
    assert calls == 0


def test_public_errors_and_object_reprs_never_expose_token_or_secret() -> None:
    """Catches credentials leaking through public exception or owner reprs."""
    token = _signed_claims()
    verifier = _verifier(secret=OTHER_SECRET)

    with pytest.raises(SessionTokenError) as caught:
        verifier.verify(token)

    exposed = repr(caught.value) + repr(verifier)
    assert token not in exposed
    assert SECRET.decode("ascii") not in exposed
    assert OTHER_SECRET.decode("ascii") not in exposed
    assert "session-a" not in repr(caught.value)


class RaisingTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta:
        del value
        raise RuntimeError("tzinfo raw marker")


@pytest.mark.parametrize("operation", ["issue", "verify"])
@pytest.mark.parametrize(
    "value",
    [
        ExplodingTimestamp(2026, 8, 12, tzinfo=UTC),
        datetime(2026, 8, 12, tzinfo=RaisingTimezone()),  # type: ignore[arg-type]
    ],
)
def test_hostile_datetime_and_timezone_failures_are_sanitized(
    operation: str, value: datetime
) -> None:
    """Catches clock conversion callbacks escaping with token-adjacent detail."""
    clock = Clock(value)
    with pytest.raises(SessionTokenError, match="^invalid_session$") as caught:
        if operation == "issue":
            SessionIssuer(
                secret=SECRET, audience="specpilot-api", clock=clock
            ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
        else:
            _verifier(clock=clock).verify(_signed_claims())

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "raw marker" not in repr(caught.value)


@pytest.mark.parametrize("operation", ["issue", "verify"])
@pytest.mark.parametrize("raw", [-0.5, math.nan, math.inf, -math.inf])
def test_nonfinite_or_negative_subsecond_raw_timestamp_is_rejected(
    operation: str, raw: float
) -> None:
    """Catches int() flooring a negative fractional clock to authorized zero."""
    class RawTimestamp(datetime):
        def timestamp(self) -> float:
            return raw

    clock = Clock(RawTimestamp(1970, 1, 1, tzinfo=UTC))
    with pytest.raises(SessionTokenError, match="^invalid_session$"):
        if operation == "issue":
            SessionIssuer(
                secret=SECRET, audience="specpilot-api", clock=clock
            ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)
        else:
            _verifier(clock=clock).verify(_signed_claims(iat=0, exp=300))


@pytest.mark.parametrize("raw", [0.0, 0.5])
def test_epoch_and_positive_subsecond_timestamp_floor_to_zero(
    raw: float,
) -> None:
    """Catches rejecting valid epoch or positive subsecond aware clocks."""
    class RawTimestamp(datetime):
        def timestamp(self) -> float:
            return raw

    clock = Clock(RawTimestamp(1970, 1, 1, tzinfo=UTC))
    token = SessionIssuer(
        secret=SECRET, audience="specpilot-api", clock=clock
    ).issue(session_id="session-a", profile="fixture", ttl_seconds=300)

    assert _verifier(clock=clock).verify(token).issued_at.timestamp() == 0


def test_verify_rejects_non_string_tokens() -> None:
    """Catches coercion of foreign transport values into credentials."""
    verifier = _verifier()
    for token in (None, b"token", 1, True):
        with pytest.raises(SessionTokenError, match="^invalid_session$"):
            verifier.verify(token)  # type: ignore[arg-type]
