"""Serving security: authentication, scope authorization, rotation, JWT,
rate limiting, and audit logging."""

import json
import pathlib
import time
from typing import Any

import anyio
import jwt
import pytest
from starlette import testclient as starlette_testclient

from fl_op.serving import api as serving_api
from fl_op.serving.artifacts import FilesystemArtifactStore
from fl_op.serving.security import (
    SCOPE_FEASIBILITY,
    SCOPE_PLANS_READ,
    AuditLogger,
    FixedWindowRateLimiter,
    NullAuthenticator,
    OidcJwtAuthenticator,
    SecurityGateway,
    StaticTokenAuthenticator,
)
from fl_op.serving.security.audit import DECISION_ALLOW, DECISION_DENY
from fl_op.serving.security.errors import AuthenticationError

httpx = starlette_testclient.httpx

# A >=32-byte secret keeps PyJWT from warning about short HMAC keys.
HS256_SECRET = "unit-test-hs256-shared-secret-bytes-0123456789"
ISSUER = "https://issuer.test"
AUDIENCE = "fl-op"


class AsgiClient:
    """Synchronous ASGITransport helper (the sandbox can hang the portal)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    def get(self, url: str, **kwargs: Any):
        return anyio.run(self._request, "GET", url, kwargs)

    def post(self, url: str, **kwargs: Any):
        return anyio.run(self._request, "POST", url, kwargs)

    async def _request(self, method: str, url: str, kwargs: dict[str, Any]):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, url, **kwargs)


@pytest.fixture
def artifact_store(tmp_path) -> FilesystemArtifactStore:
    root = tmp_path / "artifacts"
    run_dir = root / "plan-periodic" / "20260101T000000"
    run_dir.mkdir(parents=True)
    (run_dir / "plan.json").write_text(json.dumps({"plan_id": "p1"}))
    return FilesystemArtifactStore(root)


def _gateway(
    authenticator,
    rate_limiter: FixedWindowRateLimiter | None = None,
    audit: AuditLogger | None = None,
) -> SecurityGateway:
    return SecurityGateway(
        authenticator=authenticator,
        rate_limiter=rate_limiter or FixedWindowRateLimiter(0, 60),
        audit=audit or AuditLogger(enabled=False),
    )


def _app(artifact_store, gateway: SecurityGateway) -> AsgiClient:
    return AsgiClient(
        serving_api.create_app(artifact_store=artifact_store, security=gateway)
    )


# --- unit: authenticators ------------------------------------------------


def test_static_token_rotation_accepts_every_listed_token() -> None:
    auth = StaticTokenAuthenticator(
        {"retiring-token": frozenset(), "fresh-token": frozenset()}
    )
    # Both tokens authenticate, so a rotation window where clients straddle the
    # old and new token never rejects a valid caller.
    assert auth.authenticate("Bearer retiring-token").has_scope(SCOPE_PLANS_READ)
    assert auth.authenticate("Bearer fresh-token").has_scope(SCOPE_FEASIBILITY)


def test_static_token_rejects_unknown_and_malformed_headers() -> None:
    auth = StaticTokenAuthenticator({"tok": frozenset()})
    for header in (None, "", "Basic tok", "Bearer ", "Bearer wrong"):
        with pytest.raises(AuthenticationError):
            auth.authenticate(header)


def test_static_token_scopes_are_per_token() -> None:
    auth = StaticTokenAuthenticator(
        {"reader": frozenset({SCOPE_PLANS_READ})}
    )
    principal = auth.authenticate("Bearer reader")
    assert principal.has_scope(SCOPE_PLANS_READ)
    assert not principal.has_scope(SCOPE_FEASIBILITY)


def _jwt(scopes: str = "plans:read feasibility:evaluate", **overrides) -> str:
    payload = {
        "sub": "svc-a",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + 60,
        "scope": scopes,
    }
    payload.update(overrides)
    return jwt.encode(payload, HS256_SECRET, algorithm="HS256")


def _oidc_authenticator() -> OidcJwtAuthenticator:
    return OidcJwtAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        hs256_secret=HS256_SECRET,
        algorithms=("HS256",),
    )


def test_oidc_validates_signature_issuer_audience_and_scopes() -> None:
    principal = _oidc_authenticator().authenticate("Bearer " + _jwt())
    assert principal.subject == "svc-a"
    assert principal.has_scope(SCOPE_PLANS_READ)
    assert principal.has_scope(SCOPE_FEASIBILITY)


def test_oidc_rejects_expired_wrong_issuer_audience_and_signature() -> None:
    auth = _oidc_authenticator()
    expired = _jwt(exp=int(time.time()) - 10)
    wrong_iss = _jwt(iss="https://evil.test")
    wrong_aud = _jwt(aud="someone-else")
    bad_sig = jwt.encode(
        {"sub": "x", "iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 60},
        "a-different-secret-key-that-is-also-32-bytes!!",
        algorithm="HS256",
    )
    for token in (expired, wrong_iss, wrong_aud, bad_sig):
        with pytest.raises(AuthenticationError):
            auth.authenticate("Bearer " + token)


def test_oidc_reads_scopes_from_scp_and_roles_lists() -> None:
    auth = OidcJwtAuthenticator(
        issuer=ISSUER,
        audience=AUDIENCE,
        hs256_secret=HS256_SECRET,
        algorithms=("HS256",),
        scope_claims=("scope", "scp", "roles"),
    )
    token = jwt.encode(
        {
            "sub": "svc",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "exp": int(time.time()) + 60,
            "scp": [SCOPE_PLANS_READ],
            "roles": [SCOPE_FEASIBILITY],
        },
        HS256_SECRET,
        algorithm="HS256",
    )
    principal = auth.authenticate("Bearer " + token)
    assert principal.has_scope(SCOPE_PLANS_READ)
    assert principal.has_scope(SCOPE_FEASIBILITY)


def test_oidc_missing_pyjwt_raises_actionable_error(monkeypatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "jwt", None)
    auth = _oidc_authenticator()
    with pytest.raises(RuntimeError, match=r"fl-op\[auth\]"):
        auth.authenticate("Bearer " + "x.y.z")


# --- integrated: per-route authorization ---------------------------------


def test_static_token_grants_only_its_scopes_per_route(artifact_store) -> None:
    gateway = _gateway(
        StaticTokenAuthenticator({"reader": frozenset({SCOPE_PLANS_READ})})
    )
    client = _app(artifact_store, gateway)
    headers = {"Authorization": "Bearer reader"}

    assert client.get("/health").status_code == 200
    assert client.get("/plans/periodic", headers=headers).status_code == 200
    # The reader scope does not cover feasibility -> 403, not 401.
    assert client.post(
        "/feasibility", json={"order": {"order_id": "o-1"}}, headers=headers
    ).status_code == 403


def test_oidc_protected_routes_accept_valid_jwt(artifact_store, monkeypatch) -> None:
    gateway = _gateway(_oidc_authenticator())
    client = _app(artifact_store, gateway)

    assert client.get("/plans/periodic").status_code == 401
    ok = client.get(
        "/plans/periodic", headers={"Authorization": "Bearer " + _jwt()}
    )
    assert ok.status_code == 200
    assert ok.json()["runs"] == ["20260101T000000"]

    # A token without the feasibility scope is forbidden from that route.
    plans_only = _jwt(scopes="plans:read")
    forbidden = client.post(
        "/feasibility",
        json={"order": {"order_id": "o-1"}},
        headers={"Authorization": "Bearer " + plans_only},
    )
    assert forbidden.status_code == 403


def test_open_mode_allows_all_routes(artifact_store) -> None:
    client = _app(artifact_store, _gateway(NullAuthenticator()))
    assert client.get("/plans/periodic").status_code == 200


# --- integrated: rate limiting -------------------------------------------


def test_rate_limit_returns_429_after_budget(artifact_store) -> None:
    gateway = _gateway(
        StaticTokenAuthenticator({"tok": frozenset()}),
        rate_limiter=FixedWindowRateLimiter(max_requests=2, window_s=60),
    )
    client = _app(artifact_store, gateway)
    headers = {"Authorization": "Bearer tok"}

    assert client.get("/plans/periodic", headers=headers).status_code == 200
    assert client.get("/plans/periodic", headers=headers).status_code == 200
    limited = client.get("/plans/periodic", headers=headers)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
    # Health is never rate limited.
    assert client.get("/health").status_code == 200


# --- integrated: audit ----------------------------------------------------


def test_audit_records_allow_and_deny(artifact_store, tmp_path) -> None:
    audit_file = tmp_path / "audit.jsonl"
    raw_token = "super-secret-raw-credential-9f3a"
    gateway = _gateway(
        StaticTokenAuthenticator({raw_token: frozenset({SCOPE_PLANS_READ})}),
        audit=AuditLogger(enabled=True, file_path=audit_file),
    )
    client = _app(artifact_store, gateway)

    client.get("/plans/periodic", headers={"Authorization": f"Bearer {raw_token}"})
    client.get("/plans/periodic", headers={"Authorization": "Bearer wrong"})

    records = [json.loads(line) for line in audit_file.read_text().splitlines()]
    decisions = {r["decision"] for r in records}
    assert DECISION_ALLOW in decisions
    assert DECISION_DENY in decisions
    allow = next(r for r in records if r["decision"] == DECISION_ALLOW)
    assert allow["path"] == "/plans/periodic"
    assert allow["token_id"]  # a fingerprint, never the raw token
    assert raw_token not in audit_file.read_text()


def test_rate_limiter_disabled_by_default_is_noop() -> None:
    limiter = FixedWindowRateLimiter(0, 60)
    assert not limiter.enabled
    for _ in range(1000):
        limiter.check("k")  # never raises


def test_fixed_window_resets_after_window() -> None:
    clock = {"t": 0.0}
    limiter = FixedWindowRateLimiter(
        max_requests=1, window_s=10, clock=lambda: clock["t"]
    )
    limiter.check("k")
    with pytest.raises(Exception):
        limiter.check("k")
    clock["t"] = 11.0
    limiter.check("k")  # new window, allowed again
