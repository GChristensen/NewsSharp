"""The thin API layer (api/http.py): plain Django views with hand-written validation, the fallback
named in decision 25. Errors are {"error": code, "message": text} with an HTTP status (0.3)."""
import inspect
import json
import logging

from django.http import Http404, HttpResponseBase, JsonResponse

from feeds.util import now

log = logging.getLogger("web.api")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


def error_response(status: int, code: str, message: str = "", **extra) -> JsonResponse:
    return JsonResponse({"error": code, "message": message or code, **extra}, status=status)


def csrf_failure(request, reason=""):
    """Django's reason (a missing cookie, a mismatched token, a foreign Origin) is returned and logged,
    because it is what tells a cookie problem from a proxy or host-name problem."""
    logging.getLogger("newssharp.csrf").warning(
        "CSRF failure on %s %s: %s (Origin %s, Host %s)",
        request.method, request.path, reason, request.headers.get("Origin", "-"), request.get_host(),
    )
    return error_response(403, "csrf", "The request was rejected (CSRF check failed). Reload the page.", reason=reason)


def _body(request) -> dict:
    # Only JSON bodies are read here. A multipart upload has already been consumed as a stream by
    # the CSRF middleware (it looks at request.POST), and touching request.body then raises.
    if request.method in ("GET", "HEAD") or not (request.content_type or "").startswith("application/json"):
        return {}
    if request.body:
        try:
            data = json.loads(request.body)
        except ValueError:
            raise ApiError(400, "bad_request", "The request body is not valid JSON.") from None
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "The request body must be a JSON object.")
        return data
    return {}


def _finish(result):
    if isinstance(result, HttpResponseBase):
        return result
    return JsonResponse({} if result is None else result, safe=False)


def _failure(request, e: Exception):
    if isinstance(e, ApiError):
        return error_response(e.status, e.code, e.message)
    if isinstance(e, Http404):
        return error_response(404, "not_found", "Not found.")
    log.exception("%s %s failed", request.method, request.path)
    return error_response(500, "server_error", "The server failed to handle the request.")


def client_id(request) -> str | None:
    return request.headers.get("X-Client-Id")


def endpoint(auth: bool = True, admin: bool = False, **handlers):
    """Build a view from per-method handlers `fn(request, data, **kwargs)`. The view is async when
    any handler is (R0.32); every /api route except login and setup requires a session (0.3)."""
    is_async = any(inspect.iscoroutinefunction(fn) for fn in handlers.values())

    if is_async:

        async def view(request, **kwargs):
            handler = handlers.get(request.method)
            if handler is None:
                return error_response(405, "method_not_allowed")
            try:
                if auth:
                    user = await request.auser()
                    if not user.is_authenticated:
                        return error_response(401, "unauthenticated", "Log in to continue.")
                    if admin and not user.is_superuser:
                        return error_response(403, "forbidden", "Administrators only.")
                    request.user = user
                return _finish(await handler(request, _body(request), **kwargs))
            except Exception as e:
                return _failure(request, e)

    else:

        def view(request, **kwargs):
            handler = handlers.get(request.method)
            if handler is None:
                return error_response(405, "method_not_allowed")
            try:
                if auth:
                    if not request.user.is_authenticated:
                        return error_response(401, "unauthenticated", "Log in to continue.")
                    if admin and not request.user.is_superuser:
                        return error_response(403, "forbidden", "Administrators only.")
                    # R22.3: the 90-day session is renewed once a day on use.
                    if now() - request.session.get("_renewed", 0) > 86400:
                        request.session["_renewed"] = now()
                return _finish(handler(request, _body(request), **kwargs))
            except Exception as e:
                return _failure(request, e)

    return view


def require_int(data: dict, key: str, default=None, minimum=None, maximum=None) -> int | None:
    value = data.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ApiError(422, "invalid", f"'{key}' must be a number.")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ApiError(422, "invalid", f"'{key}' must be a number.") from None
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ApiError(422, "invalid", f"'{key}' is out of range.")
    return value


def require_str(data: dict, key: str, default=None, max_len: int = 4000) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or len(value) > max_len:
        raise ApiError(422, "invalid", f"'{key}' must be text.")
    return value
