from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from .access import is_app_admin, redeem_invite_code, user_has_access
from .telegram_auth import extract_invite_code_from_start_param


def require_verified_user(view_func):
    """Не даёт войти без allow-list Telegram ID или приглашения."""

    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        # Анонимный пользователь нужен только для стартовой страницы: её
        # JavaScript сначала отправит initData в неизменённый auth_telegram.
        if not request.user.is_authenticated or user_has_access(request.user):
            return view_func(request, *args, **kwargs)

        # Telegram добавляет startapp в URL Mini App также как
        # tgWebAppStartParam. Здесь код только сохраняется для формы:
        # право доступа всё равно выдаст POST invite_redeem после проверки БД.
        invite_code = extract_invite_code_from_start_param(
            request.GET.get('tgWebAppStartParam', '')
        )
        if invite_code:
            if redeem_invite_code(request.user, invite_code):
                request.session.pop('pending_invite_code', None)
                return redirect('index')
            request.session['pending_invite_code'] = invite_code

        invite_url = reverse('invite_redeem')
        if request.headers.get('HX-Request') == 'true':
            return HttpResponse(status=204, headers={'HX-Redirect': invite_url})
        return redirect(invite_url)

    return wrapped_view


def verified_login_required(view_func):
    """Комбинация проверки сессии и права по ID или приглашению."""
    from django.contrib.auth.decorators import login_required

    return login_required(require_verified_user(view_func))


def app_admin_required(view_func):
    """Разрешает view только глобальному администратору приложения."""

    @verified_login_required
    @wraps(view_func)
    def wrapped_view(request, *args, **kwargs):
        if not is_app_admin(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped_view
