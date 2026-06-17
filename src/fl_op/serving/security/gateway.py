"""The security gateway: one dependency that guards each protected route.

A gateway bundles an authenticator, the per-route scope check, an optional rate
limiter, and the audit logger, and exposes :meth:`requires` -- a FastAPI
dependency factory parameterized by the scope a route demands. Every protected
request flows authenticate -> authorize -> rate-limit -> audit, and any refusal
is audited before it is translated into the matching HTTP status. This is the
only module that depends on FastAPI, keeping the primitives framework-agnostic.
"""

import logging
import pathlib
from typing import Callable, Optional

from fastapi import Header, HTTPException, Request

from fl_op.core import constants
from fl_op.core.paths import DATA_ROOT
from fl_op.serving.security.audit import DECISION_ALLOW, DECISION_DENY, AuditLogger
from fl_op.serving.security.authenticators import (
    Authenticator,
    NullAuthenticator,
    OidcJwtAuthenticator,
    StaticTokenAuthenticator,
)
from fl_op.serving.security.errors import AuthorizationError, SecurityError
from fl_op.serving.security.principal import ANONYMOUS_SUBJECT, Principal
from fl_op.serving.security.ratelimit import FixedWindowRateLimiter

logger = logging.getLogger(__name__)


class SecurityGateway:
    """Authenticate, authorize, rate-limit, and audit each protected request."""

    def __init__(
        self,
        authenticator: Authenticator,
        rate_limiter: FixedWindowRateLimiter,
        audit: AuditLogger,
    ) -> None:
        self.authenticator = authenticator
        self.rate_limiter = rate_limiter
        self.audit = audit

    @property
    def auth_configured(self) -> bool:
        """True when a real authenticator (not open mode) is installed."""
        return not isinstance(self.authenticator, NullAuthenticator)

    def requires(self, scope: str) -> Callable[..., "Principal"]:
        """Build the dependency that guards a route demanding ``scope``."""

        async def dependency(
            request: Request,
            authorization: Optional[str] = Header(default=None),
        ) -> Principal:
            client = request.client.host if request.client else "-"
            method = request.method
            path = request.url.path
            principal: Optional[Principal] = None
            try:
                principal = self.authenticator.authenticate(authorization)
                if not principal.has_scope(scope):
                    raise AuthorizationError(f"missing required scope '{scope}'")
                self.rate_limiter.check(_rate_limit_key(principal, client))
            except SecurityError as exc:
                self.audit.record(
                    principal=principal,
                    method=method,
                    path=path,
                    client=client,
                    decision=DECISION_DENY,
                    status_code=exc.status_code,
                    reason=exc.detail,
                )
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers=exc.headers or None,
                )
            self.audit.record(
                principal=principal,
                method=method,
                path=path,
                client=client,
                decision=DECISION_ALLOW,
                status_code=200,
            )
            return principal

        return dependency


def _rate_limit_key(principal: Principal, client: str) -> str:
    """Throttle per identity; fall back to client host for anonymous traffic."""
    if principal.subject == ANONYMOUS_SUBJECT or not principal.subject:
        return f"ip:{client}"
    return f"sub:{principal.subject}"


def _configured_tokens() -> list[str]:
    """Bearer tokens from SERVE_AUTH_TOKEN(S), order-preserving and deduped."""
    raw = [constants.SERVE_AUTH_TOKEN, *constants.SERVE_AUTH_TOKENS.split(",")]
    tokens: list[str] = []
    for token in (item.strip() for item in raw):
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _build_authenticator(auth_token_override: Optional[str]) -> Authenticator:
    """Select the authenticator from constants (or an explicit override)."""
    if auth_token_override is not None:
        # Programmatic override (back-compat with create_app(auth_token=...)):
        # a non-empty token is a single static credential; "" forces open mode.
        if auth_token_override:
            return StaticTokenAuthenticator({auth_token_override: frozenset()})
        return NullAuthenticator()

    mode = (constants.SERVE_AUTH_MODE or "").strip().lower()
    tokens = _configured_tokens()
    if not mode:
        mode = "oidc" if constants.SERVE_OIDC_ISSUER else ("static" if tokens else "none")

    if mode == "none":
        return NullAuthenticator()
    if mode == "static":
        if not tokens:
            raise ValueError(
                "SERVE_AUTH_MODE=static requires SERVE_AUTH_TOKENS or SERVE_AUTH_TOKEN"
            )
        return StaticTokenAuthenticator({token: frozenset() for token in tokens})
    if mode == "oidc":
        return OidcJwtAuthenticator(
            issuer=constants.SERVE_OIDC_ISSUER,
            audience=constants.SERVE_OIDC_AUDIENCE,
            jwks_url=constants.SERVE_OIDC_JWKS_URL,
            hs256_secret=constants.SERVE_OIDC_HS256_SECRET,
            algorithms=_split_csv(constants.SERVE_OIDC_ALGORITHMS, ("RS256",)),
            scope_claims=_split_csv(
                constants.SERVE_OIDC_SCOPE_CLAIMS, ("scope", "scp", "roles")
            ),
        )
    raise ValueError(f"unknown SERVE_AUTH_MODE '{mode}'")


def _split_csv(value: str, default: tuple[str, ...]) -> tuple[str, ...]:
    parts = tuple(item.strip() for item in value.split(",") if item.strip())
    return parts or default


def _audit_file_path() -> Optional[pathlib.Path]:
    if not constants.SERVE_AUDIT_LOG_FILENAME:
        return None
    return DATA_ROOT / constants.SERVE_AUDIT_DIRNAME / constants.SERVE_AUDIT_LOG_FILENAME


def build_gateway(auth_token: Optional[str] = None) -> SecurityGateway:
    """Assemble the gateway from constants, honoring an auth-token override."""
    return SecurityGateway(
        authenticator=_build_authenticator(auth_token),
        rate_limiter=FixedWindowRateLimiter(
            constants.SERVE_RATE_LIMIT_REQUESTS,
            constants.SERVE_RATE_LIMIT_WINDOW_S,
        ),
        audit=AuditLogger(
            enabled=constants.SERVE_AUDIT_LOG_ENABLED,
            file_path=_audit_file_path(),
        ),
    )
