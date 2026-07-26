from functools import wraps

from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse


def require_verified_user(view_func):
    """Не даёт авторизованному, но не приглашённому пользователю открыть приложение."""

    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        # Анонимный пользователь нужен только для стартовой страницы: её
        # JavaScript сначала отправит initData в неизменённый auth_telegram.
        if not request.user.is_authenticated or request.user.is_verified:
            return view_func(request, *args, **kwargs)

        invite_url = reverse('invite_redeem')
        if request.headers.get('HX-Request') == 'true':
            return HttpResponse(status=204, headers={'HX-Redirect': invite_url})
        return redirect(invite_url)

    return wrapped_view


def verified_login_required(view_func):
    """Комбинация обычной проверки сессии и проверки приглашения."""
    from django.contrib.auth.decorators import login_required

    return login_required(require_verified_user(view_func))
