"""Response middleware of the web process (api/middleware.py): per-response Secure cookie flags
(R5.5) and the upgrade-insecure-requests policy (R14.2)."""
from django.conf import settings


def app_middleware(get_response):
    def middleware(request):
        response = get_response(request)
        if request.is_secure():
            for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                if name in response.cookies:
                    response.cookies[name]["secure"] = True
            # R14.2. Sent only on https pages: on an http page there is no mixed content to upgrade,
            # and the directive would upgrade the application's own requests too.
            if "Content-Security-Policy" not in response:
                response["Content-Security-Policy"] = "upgrade-insecure-requests"
        return response

    return middleware
