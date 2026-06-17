"""Authenticated principal and the route authorization scopes.

A :class:`Principal` is the resolved identity behind a request: who they are
(``subject``), how they proved it (``token_id``), and what they may do
(``scopes``). Authenticators build it; the scope authorizer reads it. The
wildcard scope ``*`` grants every route and is what the open (no-auth) local
development mode and an unrestricted static token carry.
"""

from dataclasses import dataclass, field
from typing import Any

# Per-route authorization scopes. Health is public and carries no scope.
SCOPE_PLANS_READ: str = "plans:read"
SCOPE_FEASIBILITY: str = "feasibility:evaluate"

# Grants every route. Held by anonymous principals in open mode and by static
# tokens configured without an explicit scope list.
SCOPE_WILDCARD: str = "*"

# Subject reported for an unauthenticated principal in open (no-auth) mode.
ANONYMOUS_SUBJECT: str = "anonymous"


@dataclass(frozen=True)
class Principal:
    """The resolved identity and authorities behind one request."""

    subject: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    token_id: str = ""
    claims: dict[str, Any] = field(default_factory=dict)

    def has_scope(self, scope: str) -> bool:
        """True if the principal carries ``scope`` (or the wildcard)."""
        return SCOPE_WILDCARD in self.scopes or scope in self.scopes


def anonymous_principal() -> Principal:
    """The open-mode principal: a named anonymous identity with every scope."""
    return Principal(
        subject=ANONYMOUS_SUBJECT,
        scopes=frozenset({SCOPE_WILDCARD}),
        token_id="",
    )
