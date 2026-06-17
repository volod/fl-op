"""Bearer-token authenticators for the serving API.

Three implementations share one ``authenticate(authorization_header) ->
Principal`` contract so routes never branch on the auth mode:

* :class:`NullAuthenticator` -- open local-dev mode; every request is the
  anonymous wildcard principal.
* :class:`StaticTokenAuthenticator` -- a set of accepted bearer tokens, each
  mapped to scopes. Holding several tokens at once is what makes rotation
  zero-downtime: add the new token, roll clients over, then retire the old one.
* :class:`OidcJwtAuthenticator` -- validates RFC 7519 JWTs (signature, issuer,
  audience, expiry) and reads scopes from the configured claims. PyJWT is an
  optional dependency (the ``[auth]`` extra), imported lazily with an
  actionable error, mirroring the broker client.
"""

import logging
import secrets
from typing import Any, Optional, Protocol

from fl_op.serving.security.errors import AuthenticationError
from fl_op.serving.security.principal import (
    SCOPE_WILDCARD,
    Principal,
    anonymous_principal,
)

logger = logging.getLogger(__name__)


def parse_bearer(authorization: Optional[str]) -> str:
    """Extract the credential from an ``Authorization: Bearer <token>`` header."""
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied.strip():
        raise AuthenticationError("missing bearer token")
    return supplied.strip()


class Authenticator(Protocol):
    """Resolves an Authorization header into a :class:`Principal`."""

    def authenticate(self, authorization: Optional[str]) -> Principal:
        ...


class NullAuthenticator:
    """Open mode: no credential required, every request is anonymous-wildcard."""

    def authenticate(self, authorization: Optional[str]) -> Principal:
        return anonymous_principal()


class StaticTokenAuthenticator:
    """Accepts a set of bearer tokens, each granting a scope set.

    ``tokens`` maps a token string to the scopes it grants; a token mapped to an
    empty set (or the wildcard) grants every route. Comparison is constant-time
    so a wrong token leaks no timing signal about which prefix matched.
    """

    def __init__(self, tokens: dict[str, frozenset[str]]) -> None:
        if not tokens:
            raise ValueError("StaticTokenAuthenticator needs at least one token")
        # Normalize: an empty scope set means "unrestricted" -> wildcard.
        self._tokens = {
            token: (scopes or frozenset({SCOPE_WILDCARD}))
            for token, scopes in tokens.items()
        }

    def authenticate(self, authorization: Optional[str]) -> Principal:
        supplied = parse_bearer(authorization)
        for token, scopes in self._tokens.items():
            if secrets.compare_digest(supplied, token):
                return Principal(
                    subject=f"token:{_token_fingerprint(token)}",
                    scopes=scopes,
                    token_id=_token_fingerprint(token),
                )
        raise AuthenticationError("invalid bearer token")


def _token_fingerprint(token: str) -> str:
    """A short, non-reversible token id for audit lines (never the token)."""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


class OidcJwtAuthenticator:
    """Validates OIDC/JWT bearer tokens and derives scopes from claims."""

    def __init__(
        self,
        issuer: str,
        audience: str = "",
        jwks_url: str = "",
        hs256_secret: str = "",
        algorithms: tuple[str, ...] = ("RS256",),
        scope_claims: tuple[str, ...] = ("scope", "scp", "roles"),
    ) -> None:
        if not issuer:
            raise ValueError("OidcJwtAuthenticator needs an issuer")
        if not jwks_url and not hs256_secret:
            raise ValueError(
                "OidcJwtAuthenticator needs SERVE_OIDC_JWKS_URL (RS256) or "
                "SERVE_OIDC_HS256_SECRET (HS256)"
            )
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url
        self.hs256_secret = hs256_secret
        self.algorithms = tuple(algorithms)
        self.scope_claims = tuple(scope_claims)
        self._jwks_client: Optional[Any] = None

    def _jwt(self) -> Any:
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError(
                "OIDC/JWT validation requires the auth extra: "
                "pip install 'fl-op[auth]'"
            ) from exc
        return jwt

    def _signing_key(self, jwt_module: Any, token: str) -> Any:
        if self.hs256_secret:
            return self.hs256_secret
        if self._jwks_client is None:
            self._jwks_client = jwt_module.PyJWKClient(self.jwks_url)
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def authenticate(self, authorization: Optional[str]) -> Principal:
        token = parse_bearer(authorization)
        jwt_module = self._jwt()
        options = {"require": ["exp"], "verify_aud": bool(self.audience)}
        try:
            claims = jwt_module.decode(
                token,
                self._signing_key(jwt_module, token),
                algorithms=list(self.algorithms),
                audience=self.audience or None,
                issuer=self.issuer,
                options=options,
            )
        except Exception as exc:  # noqa: BLE001 - any decode failure is a 401
            logger.info("Rejected JWT: %s", exc)
            raise AuthenticationError("invalid or expired token") from exc
        return Principal(
            subject=str(claims.get("sub", "")),
            scopes=self._extract_scopes(claims),
            token_id=str(claims.get("jti", "")),
            claims=claims,
        )

    def _extract_scopes(self, claims: dict[str, Any]) -> frozenset[str]:
        scopes: set[str] = set()
        for claim in self.scope_claims:
            value = claims.get(claim)
            if isinstance(value, str):
                scopes.update(value.split())
            elif isinstance(value, (list, tuple)):
                scopes.update(str(item) for item in value)
        return frozenset(scopes)
