"""Serving-API security: authentication, authorization, rate limiting, audit.

The serving API was previously a single static-bearer-token check. This package
hardens that into a small, composable layer:

* ``authenticators`` -- open / static-token-with-rotation / OIDC-JWT identity;
* ``principal`` -- the resolved identity and the per-route authorization scopes;
* ``ratelimit`` -- an opt-in in-process per-principal request budget;
* ``audit`` -- one structured record per protected request;
* ``gateway`` -- the FastAPI dependency wiring all of the above per route.

``build_gateway`` assembles the gateway from constants; ``serving/api.py`` calls
``gateway.requires(<scope>)`` on each protected route.
"""

from fl_op.serving.security.audit import AuditLogger
from fl_op.serving.security.authenticators import (
    Authenticator,
    NullAuthenticator,
    OidcJwtAuthenticator,
    StaticTokenAuthenticator,
)
from fl_op.serving.security.errors import (
    AuthenticationError,
    AuthorizationError,
    RateLimitError,
    SecurityError,
)
from fl_op.serving.security.gateway import SecurityGateway, build_gateway
from fl_op.serving.security.principal import (
    SCOPE_FEASIBILITY,
    SCOPE_PLANS_READ,
    SCOPE_WILDCARD,
    Principal,
)
from fl_op.serving.security.ratelimit import FixedWindowRateLimiter

__all__ = [
    "AuditLogger",
    "Authenticator",
    "AuthenticationError",
    "AuthorizationError",
    "FixedWindowRateLimiter",
    "NullAuthenticator",
    "OidcJwtAuthenticator",
    "Principal",
    "RateLimitError",
    "SCOPE_FEASIBILITY",
    "SCOPE_PLANS_READ",
    "SCOPE_WILDCARD",
    "SecurityError",
    "SecurityGateway",
    "StaticTokenAuthenticator",
    "build_gateway",
]
