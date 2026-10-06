"""JWT claims from the HTTP API authorizer (sprint-1.md D-07).

API Gateway's JWT authorizer (Cognito rw-users) has already checked the token on every route that
needs it; the Lambda only reads who the caller is from the request context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class Unauthorized(Exception):
    """No verified claims on a route that needs them."""


@dataclass(frozen=True)
class User:
    sub: str
    email: str


def claims(event: dict[str, Any]) -> dict[str, Any]:
    context = event.get("requestContext") or {}
    jwt = (context.get("authorizer") or {}).get("jwt") or {}
    return jwt.get("claims") or {}


def require_user(event: dict[str, Any]) -> User:
    found = claims(event)
    sub, email = found.get("sub"), found.get("email")
    if not sub or not email:
        raise Unauthorized("sign in required")
    return User(sub=str(sub), email=str(email))
