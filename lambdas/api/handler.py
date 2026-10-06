"""rw-api Lambda entry point (sprint-1.md D-07): one handler, routed on event["routeKey"].

Every response is JSON. Errors are {"error": code, "message": text}; unexpected exceptions are
logged with their traceback and answered with a plain 500, never the stack trace.
"""

from __future__ import annotations

import json
import os
import traceback
from decimal import Decimal
from typing import Any

from auth import Unauthorized, require_user
from routes import ROUTES, ApiError, Request


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    if origin := os.environ.get("RW_ALLOWED_ORIGIN"):
        headers["Access-Control-Allow-Origin"] = origin
    return {"statusCode": status, "headers": headers, "body": json.dumps(body, default=_default)}


def _error(status: int, code: str, message: str) -> dict[str, Any]:
    return respond(status, {"error": code, "message": message})


def _log(**fields: Any) -> None:
    print(json.dumps({"service": "rw-api", **fields}, default=str))


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    route_key = event.get("routeKey", "")
    route = ROUTES.get(route_key)
    if route is None:
        return _error(404, "not_found", f"no route {route_key}")
    fn, needs_user = route
    try:
        if needs_user:
            require_user(event)
        body = fn(Request(event))
        _log(route=route_key, status=200)
        return respond(200, body)
    except Unauthorized as exc:
        _log(route=route_key, status=401)
        return _error(401, "unauthorized", str(exc))
    except ApiError as exc:
        _log(route=route_key, status=exc.status, error=exc.code)
        return _error(exc.status, exc.code, exc.message)
    except Exception:  # noqa: BLE001 (the caller gets a plain 500; the log keeps the details)
        _log(route=route_key, status=500, traceback=traceback.format_exc())
        return _error(500, "internal", "internal error")
