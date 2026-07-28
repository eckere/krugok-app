import secrets

from django.conf import settings


class SecurityHeadersMiddleware:
    """CSP для Mini App с ограничением встраивания доверенными Telegram-клиентами."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)
        nonce = request.csp_nonce
        directives = [
            "default-src 'self'",
            (
                "script-src 'self' "
                f"'nonce-{nonce}' https://telegram.org https://unpkg.com"
            ),
            (
                "style-src 'self' 'unsafe-inline' "
                "https://fonts.googleapis.com"
            ),
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data: https:",
            "connect-src 'self'",
            (
                "frame-src 'self' https://oauth.telegram.org "
                "https://telegram.org https://*.telegram.org"
            ),
            (
                "frame-ancestors 'self' https://web.telegram.org "
                "https://*.telegram.org https://t.me"
            ),
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        if not settings.DEBUG:
            directives.append('upgrade-insecure-requests')
        response.headers['Content-Security-Policy'] = '; '.join(directives)
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), payment=()'
        )
        return response
