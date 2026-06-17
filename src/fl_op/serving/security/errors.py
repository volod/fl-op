"""Framework-agnostic security errors.

Authenticators, the authorizer, and the rate limiter raise these; the FastAPI
gateway is the only place that translates them into ``HTTPException`` responses,
so the security primitives stay independent of the web framework and are
testable in isolation.
"""

from typing import Optional


class SecurityError(Exception):
    """A request the security layer refuses, carrying the HTTP mapping."""

    def __init__(
        self,
        detail: str,
        status_code: int,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.headers = headers or {}


class AuthenticationError(SecurityError):
    """Identity could not be established (HTTP 401)."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            detail, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )


class AuthorizationError(SecurityError):
    """Identity is known but lacks the required scope (HTTP 403)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, status_code=403)


class RateLimitError(SecurityError):
    """Per-principal request budget exhausted (HTTP 429)."""

    def __init__(self, detail: str, retry_after_s: int) -> None:
        super().__init__(
            detail,
            status_code=429,
            headers={"Retry-After": str(retry_after_s)},
        )
