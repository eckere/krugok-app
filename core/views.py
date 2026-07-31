"""
core/views.py
"""
import json
import logging
import secrets
from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required as django_login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection, models, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST

from .access import is_app_admin, redeem_invite_code, user_has_access
from .audit import record_audit
from .decorators import app_admin_required, require_verified_user, verified_login_required
from .forms import (
    AccountDeleteForm,
    CommentForm,
    DiscussionForm,
    InviteCodeCreateForm,
    InviteCodeRedeemForm,
    MessageForm,
    ProfileSettingsForm,
    ProjectForm,
    ProjectMembershipCreateForm,
    ProjectMembershipRoleForm,
    ProjectOwnershipTransferForm,
    StageForm,
    TaskForm,
)
from .models import (
    Discussion,
    InviteCode,
    Message,
    Notification,
    OutboundMessage,
    Project,
    ProjectMembership,
    Stage,
    Task,
    TelegramUser,
)
from .permissions import (
    can_delete_project,
    can_view_profile,
    get_accessible_projects,
    get_accessible_tasks,
    get_discussion_or_403,
    get_project_or_403,
    get_stage_or_403,
    get_task_or_403,
)
from .rate_limit import allow_request
from .services import (
    archive_stage as archive_stage_service,
)
from .services import (
    place_stage,
)
from .services import (
    restore_stage as restore_stage_service,
)
from .telegram_auth import (
    InitDataValidationError,
    LoginWidgetValidationError,
    extract_invite_code,
    extract_invite_code_from_start_param,
    validate_init_data,
    validate_login_widget_data,
)
from .telegram_notifications import enqueue_outbound, enqueue_task_event, notify

logger = logging.getLogger(__name__)

TASK_QUICK_FILTERS = {'overdue', 'today', 'no_deadline', 'done'}


def _user_day_bounds(user):
    try:
        user_timezone = ZoneInfo(user.timezone)
    except (AttributeError, ZoneInfoNotFoundError):
        user_timezone = timezone.get_current_timezone()
    today = timezone.now().astimezone(user_timezone).date()
    start = datetime.combine(today, time.min, tzinfo=user_timezone)
    return start, start + timedelta(days=1)


def _with_project_summary(projects):
    return projects.annotate(
        summary_task_count=models.Count('tasks', distinct=True),
        summary_done_count=models.Count(
            'tasks',
            filter=models.Q(tasks__status=Task.Status.DONE),
            distinct=True,
        ),
        summary_member_count=models.Count(
            'project_memberships',
            filter=models.Q(project_memberships__user__is_active=True),
            distinct=True,
        ),
        summary_next_deadline=models.Min(
            'tasks__deadline',
            filter=(
                ~models.Q(tasks__status=Task.Status.DONE)
                & models.Q(tasks__deadline__isnull=False)
            ),
        ),
    )

# Все ранее существовавшие прикладные view уже были помечены
# @login_required. Сохраняем это объявление, но добавляем к нему проверку
# приглашения. Исключение ниже — только форма активации инвайта.
login_required = verified_login_required


def healthcheck(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return JsonResponse({'status': 'ok'})


def readiness(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return JsonResponse({'status': 'ready'})


def privacy_policy(request):
    return render(
        request,
        'core/legal/privacy.html',
        {
            'operator_name': settings.PRIVACY_OPERATOR_NAME,
            'contact': settings.PRIVACY_CONTACT,
        },
    )


def terms_of_use(request):
    return render(
        request,
        'core/legal/terms.html',
        {'support_contact': settings.SUPPORT_CONTACT},
    )


@app_admin_required
def operational_status(request):
    max_attempts = settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS
    stuck_before = timezone.now() - timedelta(minutes=5)
    exhausted = OutboundMessage.objects.filter(
        status=OutboundMessage.Status.FAILED,
        attempt_count__gte=max_attempts,
    ).count()
    stuck = OutboundMessage.objects.filter(
        status=OutboundMessage.Status.SENDING,
        last_attempt_at__lt=stuck_before,
    ).count()
    return JsonResponse(
        {
            'status': 'degraded' if exhausted or stuck else 'ok',
            'release_sha': settings.APP_RELEASE_SHA,
            'outbound': {
                'pending': OutboundMessage.objects.filter(
                    status=OutboundMessage.Status.PENDING
                ).count(),
                'failed': OutboundMessage.objects.filter(
                    status=OutboundMessage.Status.FAILED
                ).count(),
                'sending': OutboundMessage.objects.filter(
                    status=OutboundMessage.Status.SENDING
                ).count(),
                'exhausted': exhausted,
                'stuck': stuck,
                'cancelled': OutboundMessage.objects.filter(
                    status=OutboundMessage.Status.CANCELLED
                ).count(),
            },
            'active_users': TelegramUser.objects.filter(is_active=True).count(),
            'active_projects': Project.objects.filter(is_archived=False).count(),
        }
    )


@app_admin_required
def outbound_queue(request):
    max_attempts = settings.TELEGRAM_NOTIFICATION_MAX_ATTEMPTS
    exhausted_messages = OutboundMessage.objects.filter(
        status=OutboundMessage.Status.FAILED,
        attempt_count__gte=max_attempts,
    ).select_related('recipient', 'notification').order_by('-last_attempt_at')[:100]
    retryable_messages = OutboundMessage.objects.filter(
        status=OutboundMessage.Status.FAILED,
        attempt_count__lt=max_attempts,
    ).select_related('recipient', 'notification').order_by('next_retry_at')[:50]
    return render(
        request,
        'core/ops/outbound_queue.html',
        {
            'release_sha': settings.APP_RELEASE_SHA,
            'max_attempts': max_attempts,
            'exhausted_messages': exhausted_messages,
            'retryable_messages': retryable_messages,
            'pending_count': OutboundMessage.objects.filter(
                status=OutboundMessage.Status.PENDING
            ).count(),
            'failed_count': OutboundMessage.objects.filter(
                status=OutboundMessage.Status.FAILED
            ).count(),
            'cancelled_count': OutboundMessage.objects.filter(
                status=OutboundMessage.Status.CANCELLED
            ).count(),
        },
    )


@app_admin_required
@require_POST
def outbound_retry(request, message_id):
    with transaction.atomic():
        outbound = get_object_or_404(
            OutboundMessage.objects.select_for_update(),
            pk=message_id,
            status__in=[
                OutboundMessage.Status.FAILED,
                OutboundMessage.Status.CANCELLED,
            ],
        )
        outbound.status = OutboundMessage.Status.PENDING
        outbound.attempt_count = 0
        outbound.last_attempt_at = None
        outbound.next_retry_at = timezone.now()
        outbound.error_message = ''
        outbound.sent_at = None
        outbound.save(
            update_fields=[
                'status',
                'attempt_count',
                'last_attempt_at',
                'next_retry_at',
                'error_message',
                'sent_at',
            ]
        )
        if outbound.notification_id:
            Notification.objects.filter(pk=outbound.notification_id).update(
                status=Notification.Status.PENDING,
                attempt_count=0,
                last_attempt_at=None,
                next_retry_at=timezone.now(),
                error_message='',
                sent_at=None,
            )
        record_audit(
            request,
            'outbound.retry',
            outbound,
            changes={'message_id': outbound.pk},
        )
    return redirect('outbound_queue')


@app_admin_required
@require_POST
def outbound_cancel(request, message_id):
    with transaction.atomic():
        outbound = get_object_or_404(
            OutboundMessage.objects.select_for_update(),
            pk=message_id,
            status=OutboundMessage.Status.FAILED,
        )
        outbound.status = OutboundMessage.Status.CANCELLED
        outbound.next_retry_at = None
        outbound.save(update_fields=['status', 'next_retry_at'])
        record_audit(
            request,
            'outbound.cancel',
            outbound,
            changes={'message_id': outbound.pk},
        )
    return redirect('outbound_queue')


def _close_modal(response, modal_id):
    """Просит клиент закрыть модальное окно после успешной HTMX-операции."""
    response.headers['HX-Trigger'] = json.dumps(
        {'closeModal': {'id': modal_id}}
    )
    # Формы могут менять счётчики, порядок и empty-state сразу в нескольких
    # местах страницы. Полная HTMX-перезагрузка после успешного сохранения
    # оставляет весь экран согласованным с БД.
    response.headers['HX-Refresh'] = 'true'
    return response




@ensure_csrf_cookie
@require_verified_user
def index(request):
    context = {}
    if request.user.is_authenticated:
        tasks = get_accessible_tasks(request.user).select_related(
            'project', 'assignee'
        ).order_by('status', 'deadline')
        projects = _with_project_summary(
            get_accessible_projects(request.user)
        ).order_by('-created_at')
        discussions = Discussion.objects.filter(
            models.Q(created_by=request.user) | models.Q(participants=request.user)
        ).distinct()
        context.update(
            {
                'tasks': tasks,
                'filter': 'all',
                'projects': projects,
                'task_stats': {
                    'total': tasks.count(),
                    'active': tasks.exclude(status=Task.Status.DONE).count(),
                    'done': tasks.filter(status=Task.Status.DONE).count(),
                    'overdue': tasks.exclude(status=Task.Status.DONE).filter(
                        deadline__lt=timezone.now()
                    ).count(),
                },
                'project_count': projects.count(),
                'discussion_count': discussions.count(),
            }
        )
    elif settings.TELEGRAM_BOT_USERNAME:
        # The legacy Telegram widget can redirect signed data to our backend.
        # Keep a few one-time states so login remains usable in multiple tabs
        # without weakening the main page CSP with ``unsafe-eval``.
        login_state = secrets.token_urlsafe(32)
        login_states = request.session.get('telegram_login_states', [])
        login_states = [
            state for state in login_states if isinstance(state, str)
        ][-3:]
        login_states.append(login_state)
        request.session['telegram_login_states'] = login_states
        callback_url = request.build_absolute_uri(reverse('auth_telegram_widget'))
        context.update(
            {
                'telegram_widget_auth_url': (
                    f'{callback_url}?{urlencode({"state": login_state})}'
                ),
                'telegram_browser_auth_error': (
                    request.GET.get('telegram_auth') == 'error'
                ),
            }
        )
    return render(request, 'core/index.html', context)


def dev_login(request):
    """Временный вход в обход Telegram — ТОЛЬКО для локальной разработки."""
    if not settings.DEBUG:
        raise Http404
    user, _created = TelegramUser.objects.get_or_create(
        telegram_id=111222333,
        defaults={
            'username': 'dev_test',
            'first_name': 'Тестировщик',
            'last_name': '',
            'is_verified': True,
        },
    )
    if not user.is_verified:
        user.is_verified = True
        user.save(update_fields=['is_verified'])
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('index')


@verified_login_required
@require_POST
def dev_switch_account(request, user_id):
    """Переключает локальную сессию между тестовыми пользователями."""
    if not settings.DEBUG:
        raise Http404

    user = get_object_or_404(TelegramUser, pk=user_id, is_active=True)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('index')


def _telegram_login_response(redirect_url=None):
    return JsonResponse(
        {'success': True, 'redirect_url': redirect_url or reverse('index')}
    )


@require_POST
def auth_telegram(request):
    """
    POST /auth/telegram/
    Тело запроса (JSON): {"init_data": "<window.Telegram.WebApp.initData>"}
    """
    if not allow_request(
        request, 'telegram-auth', limit=30, window_seconds=5 * 60
    ):
        return JsonResponse({'error': 'Слишком много попыток'}, status=429)
    try:
        payload = json.loads(request.body or '{}')
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        payload = {}

    init_data = payload.get('init_data', '') if isinstance(payload, dict) else ''
    if not isinstance(init_data, str):
        init_data = ''

    try:
        tg_user = validate_init_data(init_data)
    except InitDataValidationError as exc:
        logger.warning('Отклонена попытка входа в Mini App: %s', exc)
        # Причину держим только в серверном логе: она не должна попадать в UI.
        return JsonResponse({'error': 'Не удалось войти'}, status=401)

    invite_code = extract_invite_code(init_data)
    user = _login_telegram_user(request, tg_user)

    if invite_code and not user_has_access(user):
        if redeem_invite_code(user, invite_code):
            request.session.pop('pending_invite_code', None)
            return _telegram_login_response(reverse('index'))

        # Недействительный startapp не раскрывает причину отказа. Оставляем
        # прежнюю ручную форму как безопасный запасной сценарий.
        request.session['pending_invite_code'] = invite_code
        return _telegram_login_response(reverse('invite_redeem'))

    return _telegram_login_response(reverse('index'))


@require_http_methods(['GET', 'POST'])
def auth_telegram_widget(request):
    """Принимает подписанный ответ Telegram Login Widget из обычного браузера."""
    if not allow_request(
        request, 'telegram-widget-auth', limit=30, window_seconds=5 * 60
    ):
        return JsonResponse({'error': 'Слишком много попыток'}, status=429)

    if request.method == 'GET':
        received_state = request.GET.get('state', '')
        login_states = request.session.get('telegram_login_states', [])
        login_states = [
            state for state in login_states if isinstance(state, str)
        ]
        state_is_valid = bool(received_state) and any(
            secrets.compare_digest(received_state, state)
            for state in login_states
        )
        if not state_is_valid:
            logger.warning('Отклонён Telegram Login Widget с неверным state')
            return redirect(f'{reverse("index")}?telegram_auth=error')
        request.session['telegram_login_states'] = [
            state
            for state in login_states
            if not secrets.compare_digest(received_state, state)
        ]
        auth_data = {
            key: value
            for key, value in request.GET.items()
            if key != 'state'
        }
    else:
        try:
            payload = json.loads(request.body or '{}')
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            payload = {}
        auth_data = payload.get('auth_data', {}) if isinstance(payload, dict) else {}

    try:
        tg_user = validate_login_widget_data(auth_data)
    except LoginWidgetValidationError as exc:
        logger.warning('Отклонена попытка входа через Telegram Login Widget: %s', exc)
        if request.method == 'GET':
            return redirect(f'{reverse("index")}?telegram_auth=error')
        return JsonResponse({'error': 'Не удалось войти'}, status=401)

    _login_telegram_user(request, tg_user)
    if request.method == 'GET':
        return redirect('index')
    return _telegram_login_response()


def _login_telegram_user(request, tg_user):
    """Обновляет профиль и создаёт Django-сессию после проверки Telegram."""
    telegram_id = tg_user['id']
    telegram_username = str(tg_user.get('username') or '')[:64]
    username = f'tg_{telegram_id}'

    user, created = TelegramUser.objects.update_or_create(
        telegram_id=telegram_id,
        defaults={
            'username': username,
            'telegram_username': telegram_username,
            'first_name': str(tg_user.get('first_name') or '')[:150],
            'last_name': str(tg_user.get('last_name') or '')[:150],
            'photo_url': str(tg_user.get('photo_url') or '')[:200] or None,
            'language_code': str(tg_user.get('language_code') or '')[:12],
            'is_premium': bool(tg_user.get('is_premium', False)),
        },
    )
    login(request, user)
    record_audit(
        request,
        'auth.login',
        user,
        changes={'created': created, 'method': 'telegram'},
    )
    return user


@django_login_required
@require_http_methods(['GET', 'POST'])
def invite_redeem(request):
    """Позволяет уже HMAC-авторизованному пользователю активировать инвайт."""
    if user_has_access(request.user):
        return redirect('index')

    if request.method == 'POST':
        if not allow_request(
            request, 'invite-redeem', limit=10, window_seconds=5 * 60
        ):
            return HttpResponse('Слишком много попыток.', status=429)
        form = InviteCodeRedeemForm(request.POST)
        if form.is_valid() and redeem_invite_code(
            request.user, form.cleaned_data['code']
        ):
            request.session.pop('pending_invite_code', None)
            record_audit(request, 'invite.redeem', request.user)
            return redirect('index')

        # Одна и та же формулировка для несуществующего, истёкшего и уже
        # использованного кода не раскрывает состояние приглашения.
        form = InviteCodeRedeemForm({'code': request.POST.get('code', '').strip()})
        form.is_valid()
        form.add_error('code', 'Код недействителен.')
    else:
        form = InviteCodeRedeemForm(
            initial={'code': request.session.get('pending_invite_code', '')}
        )

    return render(request, 'core/invites/redeem.html', {'form': form})


def invite_link(request, code):
    """Запоминает код из персональной ссылки до Telegram HMAC-входа."""
    invite_code = extract_invite_code_from_start_param(f'invite_{code}')
    if invite_code is None:
        raise Http404
    request.session['pending_invite_code'] = invite_code
    return redirect('invite_redeem' if request.user.is_authenticated else 'index')


@app_admin_required
def invite_list(request):
    now = timezone.now()
    invites = (
        InviteCode.objects.filter(
            created_by=request.user,
            is_active=True,
            used_by__isnull=True,
        )
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .order_by('-created_at')
    )
    invite_rows = [
        {
            'invite': invite,
            'url': (
                invite.get_telegram_url()
                or request.build_absolute_uri(invite.get_absolute_url())
            ),
        }
        for invite in invites
    ]
    return render(
        request,
        'core/invites/list.html',
        {
            'invite_rows': invite_rows,
            'create_form': InviteCodeCreateForm(user=request.user),
        },
    )


@app_admin_required
@require_POST
def invite_create(request):
    form = InviteCodeCreateForm(request.POST, user=request.user)
    if not form.is_valid():
        now = timezone.now()
        invites = InviteCode.objects.filter(
            created_by=request.user,
            is_active=True,
            used_by__isnull=True,
        ).filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        invite_rows = [
            {
                'invite': invite,
                'url': invite.get_telegram_url()
                or request.build_absolute_uri(invite.get_absolute_url()),
            }
            for invite in invites
        ]
        return render(
            request,
            'core/invites/list.html',
            {'invite_rows': invite_rows, 'create_form': form},
            status=422,
        )
    invite = form.save(commit=False)
    invite.created_by = request.user
    invite.save()
    record_audit(request, 'invite.create', invite)
    return redirect('invite_list')


@app_admin_required
@require_POST
def invite_revoke(request, invite_id):
    invite = get_object_or_404(InviteCode, pk=invite_id, used_by__isnull=True)
    invite.is_active = False
    invite.save(update_fields=['is_active'])
    record_audit(request, 'invite.revoke', invite)
    return redirect('invite_list')


@app_admin_required
def user_list(request):
    filter_value = request.GET.get('filter', 'active')
    if filter_value not in {'all', 'active', 'deleted'}:
        filter_value = 'active'
    query = request.GET.get('q', '').strip()

    users = TelegramUser.objects.annotate(
        active_owned_project_count=models.Count(
            'owned_projects',
            filter=models.Q(owned_projects__is_archived=False),
            distinct=True,
        ),
        project_membership_count=models.Count(
            'project_memberships',
            distinct=True,
        ),
        owned_project_count=models.Count(
            'owned_projects',
            distinct=True,
        ),
    )
    if filter_value == 'active':
        users = users.filter(is_active=True)
    elif filter_value == 'deleted':
        users = users.filter(is_active=False)
    if query:
        search_filter = (
            models.Q(first_name__icontains=query)
            | models.Q(last_name__icontains=query)
            | models.Q(username__icontains=query)
            | models.Q(telegram_username__icontains=query)
        )
        if query.isdigit():
            search_filter |= models.Q(telegram_id=int(query))
        users = users.filter(search_filter)

    users = users.order_by(
        '-is_active',
        'first_name',
        'last_name',
        'username',
    )
    page = Paginator(users, 50).get_page(request.GET.get('page'))
    for listed_user in page.object_list:
        listed_user.is_global_admin = is_app_admin(listed_user)
        listed_user.is_current_admin = listed_user.pk == request.user.pk
        listed_user.can_be_removed = bool(
            listed_user.is_active
            and not listed_user.is_global_admin
            and not listed_user.is_current_admin
            and listed_user.active_owned_project_count == 0
        )
        listed_user.can_be_purged = bool(
            not listed_user.is_active
            and listed_user.anonymized_at is not None
            and listed_user.owned_project_count == 0
        )

    admin_filter = (
        models.Q(is_superuser=True)
        | models.Q(telegram_id__in=settings.TELEGRAM_ADMIN_IDS)
    )
    context = {
        'listed_users': page,
        'filter': filter_value,
        'query': query,
        'removed': request.GET.get('removed') == '1',
        'purged': request.GET.get('purged') == '1',
        'user_stats': {
            'total': TelegramUser.objects.count(),
            'active': TelegramUser.objects.filter(is_active=True).count(),
            'deleted': TelegramUser.objects.filter(is_active=False).count(),
            'admins': TelegramUser.objects.filter(
                is_active=True,
            ).filter(admin_filter).count(),
        },
    }
    template_name = (
        'core/users/list_items.html' if request.htmx else 'core/users/list.html'
    )
    return render(request, template_name, context)


@app_admin_required
@require_POST
def user_remove(request, user_id):
    with transaction.atomic():
        target = get_object_or_404(
            TelegramUser.objects.select_for_update(),
            pk=user_id,
            is_active=True,
        )
        if target.pk == request.user.pk or is_app_admin(target):
            raise PermissionDenied
        active_projects = target.owned_projects.filter(is_archived=False)
        if active_projects.exists():
            return HttpResponse(
                'Сначала передайте владение активными проектами '
                'или архивируйте их.',
                status=409,
            )

        target.assigned_tasks.update(assignee=None)
        target.project_memberships.exclude(project__owner=target).delete()
        target.discussions.clear()
        target.outbound_messages.filter(
            status__in=[
                OutboundMessage.Status.PENDING,
                OutboundMessage.Status.SENDING,
                OutboundMessage.Status.FAILED,
            ]
        ).update(
            status=OutboundMessage.Status.CANCELLED,
            next_retry_at=None,
        )
        record_audit(
            request,
            'user.remove',
            target,
            changes={'user_id': target.pk},
        )
        target.anonymize()
    redirect_url = f"{reverse('user_list')}?filter=active&removed=1"
    if request.htmx:
        return HttpResponse(headers={'HX-Redirect': redirect_url})
    return redirect(redirect_url)


@app_admin_required
@require_POST
def user_purge(request, user_id):
    with transaction.atomic():
        target = get_object_or_404(
            TelegramUser.objects.select_for_update(),
            pk=user_id,
            is_active=False,
            anonymized_at__isnull=False,
        )
        if target.owned_projects.exists():
            return HttpResponse(
                'Сначала удалите архивные проекты этого пользователя.',
                status=409,
            )

        record_audit(
            request,
            'user.purge',
            target,
            changes={'user_id': target.pk},
        )
        target.delete()

    redirect_url = f"{reverse('user_list')}?filter=deleted&purged=1"
    if request.htmx:
        return HttpResponse(headers={'HX-Redirect': redirect_url})
    return redirect(redirect_url)


@login_required
def profile_detail(request, user_id):
    profile_user = get_object_or_404(TelegramUser, pk=user_id, is_active=True)
    if not can_view_profile(profile_user, request.user):
        raise PermissionDenied
    return render(
        request,
        'core/users/profile.html',
        {
            'profile_user': profile_user,
            'settings_form': (
                ProfileSettingsForm(instance=request.user)
                if profile_user.pk == request.user.pk
                else None
            ),
            'delete_form': (
                AccountDeleteForm()
                if profile_user.pk == request.user.pk
                else None
            ),
        },
    )


@login_required
@require_POST
def profile_settings(request):
    form = ProfileSettingsForm(request.POST, instance=request.user)
    if form.is_valid():
        form.save()
        record_audit(request, 'account.settings', request.user)
        return redirect('profile_detail', user_id=request.user.pk)
    return render(
        request,
        'core/users/profile.html',
        {
            'profile_user': request.user,
            'settings_form': form,
            'delete_form': AccountDeleteForm(),
        },
        status=422,
    )


@login_required
@require_POST
def account_logout(request):
    record_audit(request, 'auth.logout', request.user)
    logout(request)
    return redirect('index')


@login_required
@require_POST
def account_delete(request):
    form = AccountDeleteForm(request.POST)
    if request.user.owned_projects.filter(is_archived=False).exists():
        form.add_error(
            None,
            'Перед удалением аккаунта передайте или архивируйте активные проекты.',
        )
    if form.is_valid():
        user = request.user
        record_audit(request, 'account.anonymize', user)
        logout(request)
        user.anonymize()
        return redirect('index')
    return render(
        request,
        'core/users/profile.html',
        {
            'profile_user': request.user,
            'settings_form': ProfileSettingsForm(instance=request.user),
            'delete_form': form,
        },
        status=422,
    )


# ---------------------------------------------------------------------------
# Задачи. Шаблоны core/templates/core/tasks/{list,form,card}.html и
# core/forms.py:TaskForm уже существовали — здесь только недостающие view.
# ---------------------------------------------------------------------------


@login_required
def task_list(request):
    """
    GET /tasks/?filter=all|mine
    Обычный запрос -> полная страница списка (core/tasks/list.html целиком).
    HTMX-запрос (клик по фильтру, hx-target=#task-list, hx-swap=outerHTML) ->
    та же разметка, но она сама содержит <div id="task-list">, так что
    outerHTML-подмена находит новый #task-list в ответе и всё продолжает работать.
    """
    filter_value = request.GET.get('filter', 'all')
    tasks = get_accessible_tasks(request.user).select_related(
        'project', 'assignee'
    ).order_by('status', 'deadline')
    if filter_value == 'mine':
        tasks = tasks.filter(assignee=request.user)
    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    quick_filter = request.GET.get('quick', '').strip()
    if quick_filter not in TASK_QUICK_FILTERS:
        quick_filter = ''
    if query:
        tasks = tasks.filter(
            models.Q(title__icontains=query)
            | models.Q(description__icontains=query)
            | models.Q(project__name__icontains=query)
        )
    if status_filter in Task.Status.values:
        tasks = tasks.filter(status=status_filter)
    if quick_filter == 'overdue':
        tasks = tasks.exclude(status=Task.Status.DONE).filter(
            deadline__lt=timezone.now()
        )
    elif quick_filter == 'today':
        day_start, day_end = _user_day_bounds(request.user)
        tasks = tasks.exclude(status=Task.Status.DONE).filter(
            deadline__gte=day_start,
            deadline__lt=day_end,
        )
    elif quick_filter == 'no_deadline':
        tasks = tasks.exclude(status=Task.Status.DONE).filter(
            deadline__isnull=True
        )
    elif quick_filter == 'done':
        tasks = tasks.filter(status=Task.Status.DONE)
    page = Paginator(tasks, 50).get_page(request.GET.get('page'))

    template_name = (
        'core/tasks/list_items.html' if request.htmx else 'core/tasks/list.html'
    )
    return render(
        request,
        template_name,
        {
            'tasks': page,
            'filter': filter_value,
            'query': query,
            'status_filter': status_filter,
            'quick_filter': quick_filter,
        },
    )


@login_required
def task_detail(request, task_id):
    task = get_task_or_403(task_id, request.user)
    return render(request, 'core/tasks/detail.html', {
        'task': task,
        'comments': task.comments.all(),
        'comment_form': CommentForm(),
    })


@login_required
def task_create(request):
    """
    GET  /tasks/create/  -> пустая форма (для модалки, hx-target=#task-modal)
    POST /tasks/create/  -> валидация; успех -> одна новая карточка (append
                            в конец #task-list, как и задаёт form.html);
                            ошибка -> форма с ошибками (перерисовывается в модалке)
    """
    return_to = request.POST.get('return_to') or request.GET.get('return_to')
    initial = {}
    if request.method == 'GET':
        project_id = request.GET.get('project')
        stage_id = request.GET.get('stage')
        if stage_id:
            stage = get_stage_or_403(stage_id, request.user)
            initial.update({'project': stage.project, 'stage': stage})
        elif project_id:
            project = get_project_or_403(project_id, request.user)
            initial['project'] = project

    if request.method == 'POST':
        form = TaskForm(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                task = form.save(commit=False)
                task.creator = request.user
                task.save()
                record_audit(request, 'task.create', task)
                if task.deadline and task.status != Task.Status.DONE:
                    notify(task, Notification.Kind.DEADLINE_SET)
                if task.assignee_id and task.assignee_id != request.user.pk:
                    notify(task, Notification.Kind.TASK_ASSIGNED)
            response = render(
                request,
                'core/tasks/card.html',
                {'task': task, 'task_project_context': return_to == 'project'},
            )
            if request.htmx and return_to == 'project' and task.project_id:
                if task.stage_id:
                    target = f'#stage-{task.stage_id}-task-list'
                else:
                    target = '#project-unassigned-task-list'
                response.headers['HX-Retarget'] = target
                response.headers['HX-Reswap'] = 'beforeend'
            return _close_modal(response, 'task-modal')
        response = render(
            request,
            'core/tasks/form.html',
            {'form': form, 'return_to': return_to},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#task-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(
        request,
        'core/tasks/form.html',
        {
            'form': TaskForm(user=request.user, initial=initial),
            'return_to': return_to,
        },
    )


@login_required
@require_http_methods(['GET'])
def task_form_options(request):
    form = TaskForm(request.GET or None, user=request.user)
    return render(
        request,
        'core/tasks/dependent_fields.html',
        {'form': form},
    )


@login_required
def task_update(request, task_id):
    """
    GET  /tasks/<id>/edit/  -> форма, предзаполненная текущей задачей
    POST /tasks/<id>/edit/  -> валидация; успех -> обновлённая карточка
                                ЭТОЙ ЖЕ задачи (outerHTML-замена по id, а не
                                append в конец списка — иначе получим дубль)
    """
    task = get_task_or_403(task_id, request.user, permission='edit')
    old_deadline = task.deadline
    old_status = task.status
    old_assignee_id = task.assignee_id
    return_to = request.POST.get('return_to') or request.GET.get('return_to')

    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                task = form.save()
                deadline_changed = old_deadline != task.deadline
                assignee_changed = old_assignee_id != task.assignee_id
                completed = (
                    old_status != Task.Status.DONE
                    and task.status == Task.Status.DONE
                )
                notifications_reset = deadline_changed or assignee_changed
                if notifications_reset:
                    task.notifications.filter(
                        kind__in=[
                            Notification.Kind.DEADLINE_SET,
                            Notification.Kind.DEADLINE_APPROACHING,
                            Notification.Kind.DEADLINE_OVERDUE,
                            Notification.Kind.TASK_ASSIGNED,
                        ]
                    ).delete()
                elif completed:
                    task.notifications.filter(
                        kind__in=[
                            Notification.Kind.DEADLINE_APPROACHING,
                            Notification.Kind.DEADLINE_OVERDUE,
                        ]
                    ).delete()
                if (
                    notifications_reset
                    and task.deadline
                    and task.status != Task.Status.DONE
                ):
                    notify(task, Notification.Kind.DEADLINE_SET)
                if (
                    assignee_changed
                    and task.assignee_id
                    and task.assignee_id != request.user.pk
                ):
                    notify(task, Notification.Kind.TASK_ASSIGNED)
                record_audit(
                    request,
                    'task.update',
                    task,
                    changes={
                        'deadline_changed': deadline_changed,
                        'assignee_changed': assignee_changed,
                        'status': task.status,
                    },
                )
            if return_to == 'project':
                redirect_url = (
                    reverse('project_detail', args=[task.project_id])
                    if task.project_id
                    else reverse('task_list')
                )
                return HttpResponse(
                    headers={'HX-Redirect': redirect_url}
                )
            response = render(request, 'core/tasks/card.html', {'task': task})
            return _close_modal(response, 'task-modal')
        response = render(
            request,
            'core/tasks/form.html',
            {'form': form, 'return_to': return_to},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#task-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(
        request,
        'core/tasks/form.html',
        {
            'form': TaskForm(instance=task, user=request.user),
            'return_to': return_to,
        },
    )


@login_required
@require_http_methods(['DELETE'])
def task_delete(request, task_id):
    """
    DELETE /tasks/<id>/delete/ -> пустое тело ответа.
    Важно: именно HttpResponse(status=200) с пустым телом, а не
    JsonResponse({}) — при hx-swap="outerHTML" HTMX подставляет ТЕЛО
    ответа буквально на место карточки; JsonResponse({}) оставил бы
    в DOM текст "{}" вместо того, чтобы карточка просто исчезла.
    """
    task = get_task_or_403(task_id, request.user, permission='delete')
    record_audit(request, 'task.delete', task)
    task.delete()
    return HttpResponse(status=200, headers={'HX-Refresh': 'true'})


@login_required
@require_POST
def task_status(request, task_id):
    """Устанавливает явно выбранный статус и возвращает карточку для HTMX."""
    task = get_task_or_403(task_id, request.user, permission='status')
    new_status = request.POST.get('status')
    if new_status not in Task.Status.values:
        return HttpResponse('Недопустимый статус.', status=400)

    with transaction.atomic():
        task.status = new_status
        task.save()
        record_audit(
            request,
            'task.status',
            task,
            changes={'status': new_status},
        )
        if task.status == Task.Status.DONE:
            task.notifications.filter(
                kind__in=[
                    Notification.Kind.DEADLINE_APPROACHING,
                    Notification.Kind.DEADLINE_OVERDUE,
                ]
            ).delete()
    response = render(
        request,
        'core/tasks/card.html',
        {
            'task': task,
            'task_project_context': request.POST.get('return_to') == 'project',
        },
    )
    response.headers['HX-Refresh'] = 'true'
    return response


@login_required
@require_POST
def comment_create(request, task_id):
    task = get_task_or_403(task_id, request.user)
    form = CommentForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            comment = form.save(commit=False)
            comment.task = task
            comment.author = request.user
            comment.save()
            record_audit(request, 'comment.create', comment)
            recipients = {
                user.pk: user
                for user in [task.creator, task.assignee]
                if user is not None and user.pk != request.user.pk
            }
            for recipient in recipients.values():
                enqueue_task_event(
                    task,
                    recipient=recipient,
                    kind=Notification.Kind.COMMENT_ADDED,
                    event_id=str(comment.pk),
                    text=(
                        f'{request.user.display_name} добавил комментарий '
                        f'к задаче «{task.title}».'
                    ),
                )
        return redirect('task_detail', task_id=task.id)

    return render(request, 'core/tasks/detail.html', {
        'task': task,
        'comments': task.comments.all(),
        'comment_form': form,
    }, status=422)

# ---------------------------------------------------------------------------
# Проекты (ТЗ 3.4: "Создавать проекты (папки)"). Пока без этапов —
# Stage вернём отдельным шагом, когда дойдём именно до него.
# ---------------------------------------------------------------------------


@login_required
def project_list(request):
    """GET /projects/ — список активных (не архивных) проектов."""
    filter_value = request.GET.get('filter', 'all')
    include_archived = filter_value == 'archived'
    projects = _with_project_summary(
        get_accessible_projects(
            request.user,
            include_archived=include_archived,
        )
    ).order_by('-created_at')
    if include_archived:
        projects = projects.filter(is_archived=True)
    if filter_value == 'mine':
        projects = projects.filter(
            models.Q(owner=request.user)
            | models.Q(
                project_memberships__user=request.user,
                project_memberships__role=ProjectMembership.Role.OWNER,
            )
        ).distinct()
    query = request.GET.get('q', '').strip()
    if query:
        projects = projects.filter(
            models.Q(name__icontains=query)
            | models.Q(description__icontains=query)
        )
    page = Paginator(projects, 50).get_page(request.GET.get('page'))
    template_name = (
        'core/projects/list_items.html' if request.htmx else 'core/projects/list.html'
    )
    return render(
        request,
        template_name,
        {'projects': page, 'filter': filter_value, 'query': query},
    )


@login_required
def project_detail(request, project_id):
    project = get_project_or_403(project_id, request.user)
    project_tasks = Task.objects.filter(project=project).select_related(
        'assignee', 'stage'
    ).order_by('status', 'deadline', '-created_at')
    stages = project.stages.filter(is_archived=False).prefetch_related(
        models.Prefetch('tasks', queryset=project_tasks)
    ).order_by('order')
    unassigned_tasks = project_tasks.filter(
        models.Q(stage__isnull=True) | models.Q(stage__is_archived=True)
    )
    memberships = project.project_memberships.select_related('user').order_by(
        'role', 'user__first_name', 'user__username'
    ).filter(user__is_active=True)
    archived_stages = project.stages.filter(is_archived=True).order_by(
        '-updated_at'
    )
    return render(
        request,
        'core/projects/detail.html',
        {
            'project': project,
            'stages': stages,
            'unassigned_tasks': unassigned_tasks,
            'memberships': memberships,
            'archived_stages': archived_stages,
            'ownership_form': (
                ProjectOwnershipTransferForm(project=project)
                if project.is_owner(request.user)
                else None
            ),
        },
    )


@login_required
def project_create(request):
    """
    GET  /projects/create/  -> пустая форма (в модалку #project-modal)
    POST /projects/create/  -> валидация; успех -> создатель автоматически
                                становится owner и первым участником (members);
                                отдаём одну новую карточку (append в #project-list)
    """
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                project = form.save(commit=False)
                project.owner = request.user
                project.save()
                ProjectMembership.objects.create(
                    project=project,
                    user=request.user,
                    role=ProjectMembership.Role.OWNER,
                )
                record_audit(request, 'project.create', project)
            project = _with_project_summary(
                Project.objects.filter(pk=project.pk)
            ).get()
            response = render(request, 'core/projects/card.html', {'project': project})
            return _close_modal(response, 'project-modal')
        response = render(
            request,
            'core/projects/form.html',
            {'form': form},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#project-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(request, 'core/projects/form.html', {'form': ProjectForm()})


@login_required
def project_update(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='admin')
    return_to = request.POST.get('return_to') or request.GET.get('return_to')
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            with transaction.atomic():
                form.save()
                record_audit(request, 'project.update', project)
            if return_to == 'detail':
                return HttpResponse(
                    headers={'HX-Redirect': reverse('project_detail', args=[project.id])}
                )
            project = _with_project_summary(
                Project.objects.filter(pk=project.pk)
            ).get()
            response = render(request, 'core/projects/card.html', {'project': project})
            return _close_modal(response, 'project-modal')
        response = render(
            request,
            'core/projects/form.html',
            {'form': form, 'project': project, 'return_to': return_to},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#project-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(
        request,
        'core/projects/form.html',
        {'form': ProjectForm(instance=project), 'project': project, 'return_to': return_to},
    )


@login_required
@require_http_methods(['DELETE'])
def project_delete(request, project_id):
    project = get_object_or_404(Project, pk=project_id)
    if not can_delete_project(project, request.user):
        raise PermissionDenied
    if not project.is_archived:
        return HttpResponse(
            'Сначала архивируйте проект.',
            status=409,
        )
    record_audit(request, 'project.delete', project)
    project.delete()
    return HttpResponse(status=200, headers={'HX-Refresh': 'true'})


@login_required
@require_POST
def project_archive(request, project_id):
    """
    POST /projects/<id>/archive/
    Мягкое удаление: is_archived=True, запись остаётся в БД (у неё могут
    быть задачи — Task.project стоит на SET_NULL, но лучше не терять
    группировку молча). Пустое тело ответа -> hx-swap=outerHTML убирает
    карточку из списка активных проектов.
    """
    project = get_project_or_403(project_id, request.user, required_role='admin')
    project.is_archived = True
    project.save()
    record_audit(request, 'project.archive', project)
    if request.POST.get('return_to') == 'projects':
        return HttpResponse(headers={'HX-Redirect': reverse('project_list')})
    return HttpResponse(status=200, headers={'HX-Refresh': 'true'})


@login_required
@require_POST
def project_restore(request, project_id):
    project = get_object_or_404(Project, pk=project_id, is_archived=True)
    if not (request.user.is_superuser or project.is_admin(request.user)):
        raise PermissionDenied
    project.is_archived = False
    project.save(update_fields=['is_archived', 'updated_at'])
    record_audit(request, 'project.restore', project)
    return redirect('project_detail', project_id=project.pk)


@login_required
@require_POST
def project_transfer_ownership(request, project_id):
    project = get_project_or_403(
        project_id,
        request.user,
        required_role='owner',
    )
    form = ProjectOwnershipTransferForm(request.POST, project=project)
    if not form.is_valid():
        raise PermissionDenied('Передача владения не подтверждена.')
    new_owner = form.cleaned_data['new_owner']
    old_owner_id = project.owner_id
    with transaction.atomic():
        ProjectMembership.objects.filter(
            project=project,
            user_id=old_owner_id,
        ).update(role=ProjectMembership.Role.ADMIN)
        Project.objects.filter(pk=project.pk).update(owner=new_owner)
        ProjectMembership.objects.filter(
            project=project,
            user=new_owner,
        ).update(role=ProjectMembership.Role.OWNER)
    project.owner = new_owner
    record_audit(
        request,
        'project.transfer_ownership',
        project,
        changes={'from_user_id': old_owner_id, 'to_user_id': new_owner.pk},
    )
    return redirect('project_detail', project_id=project.pk)


@login_required
@require_http_methods(['GET', 'POST'])
def project_member_create(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='owner')
    if request.method == 'POST':
        form = ProjectMembershipCreateForm(
            request.POST,
            project=project,
            actor=request.user,
        )
        if form.is_valid():
            membership = form.save(commit=False)
            membership.project = project
            membership.save()
            record_audit(request, 'membership.create', membership)
            response = render(
                request,
                'core/projects/members/card.html',
                {'project': project, 'membership': membership},
            )
            return _close_modal(response, 'member-modal')
        response = render(
            request,
            'core/projects/members/form.html',
            {'project': project, 'form': form},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#member-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(
        request,
        'core/projects/members/form.html',
        {
            'project': project,
            'form': ProjectMembershipCreateForm(
                project=project,
                actor=request.user,
            ),
        },
    )


@login_required
@require_http_methods(['GET', 'POST'])
def project_member_update(request, membership_id):
    membership = get_object_or_404(
        ProjectMembership.objects.select_related('project', 'user'),
        pk=membership_id,
    )
    project = get_project_or_403(
        membership.project_id,
        request.user,
        required_role='owner',
    )
    if membership.user_id == project.owner_id:
        raise PermissionDenied

    if request.method == 'POST':
        form = ProjectMembershipRoleForm(request.POST, instance=membership)
        if form.is_valid():
            membership = form.save()
            record_audit(request, 'membership.update', membership)
            response = render(
                request,
                'core/projects/members/card.html',
                {'project': project, 'membership': membership},
            )
            return _close_modal(response, 'member-modal')
        response = render(
            request,
            'core/projects/members/form.html',
            {'project': project, 'membership': membership, 'form': form},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#member-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(
        request,
        'core/projects/members/form.html',
        {
            'project': project,
            'membership': membership,
            'form': ProjectMembershipRoleForm(instance=membership),
        },
    )


@login_required
@require_http_methods(['DELETE'])
def project_member_delete(request, membership_id):
    membership = get_object_or_404(
        ProjectMembership.objects.select_related('project', 'user'),
        pk=membership_id,
    )
    project = membership.project
    if not is_app_admin(request.user):
        project = get_project_or_403(
            membership.project_id,
            request.user,
            required_role='owner',
        )
    if membership.user_id == project.owner_id:
        raise PermissionDenied
    record_audit(request, 'membership.delete', membership)
    membership.delete()
    return HttpResponse(status=200, headers={'HX-Refresh': 'true'})


@login_required
def stage_create(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='admin')
    if request.method == 'POST':
        form = StageForm(request.POST)
        if form.is_valid():
            requested_order = form.cleaned_data.get('order')
            stage = form.save(commit=False)
            stage.project = project
            stage.order = None
            stage.save()
            place_stage(stage, requested_order)
            record_audit(request, 'stage.create', stage)
            response = render(
                request,
                'core/stages/card.html',
                {'project': project, 'stage': stage},
            )
            return _close_modal(response, 'stage-modal')
        response = render(
            request,
            'core/stages/form.html',
            {'form': form, 'project': project},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#stage-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(request, 'core/stages/form.html', {'form': StageForm(), 'project': project})


@login_required
def stage_update(request, stage_id):
    stage = get_stage_or_403(stage_id, request.user, required_role='admin')
    project = stage.project
    old_order = stage.order

    if request.method == 'POST':
        form = StageForm(request.POST, instance=stage)
        if form.is_valid():
            new_order = form.cleaned_data.get('order')
            if new_order is None:
                new_order = old_order
            stage = form.save(commit=False)
            stage.order = None
            stage.save()
            place_stage(stage, new_order)
            record_audit(request, 'stage.update', stage)
            response = render(
                request,
                'core/stages/card.html',
                {'project': project, 'stage': stage},
            )
            return _close_modal(response, 'stage-modal')
        response = render(
            request,
            'core/stages/form.html',
            {'form': form},
            status=200 if request.htmx else 422,
        )
        if request.htmx:
            response.headers['HX-Retarget'] = '#stage-modal'
            response.headers['HX-Reswap'] = 'innerHTML'
        return response

    return render(request, 'core/stages/form.html', {'form': StageForm(instance=stage)})


@login_required
@require_POST
def stage_archive(request, stage_id):
    stage = get_stage_or_403(stage_id, request.user, required_role='admin')
    record_audit(request, 'stage.archive', stage)
    archive_stage_service(stage)
    return HttpResponse(status=200, headers={'HX-Refresh': 'true'})


@login_required
@require_POST
def stage_restore(request, stage_id):
    stage = get_object_or_404(
        Stage.objects.select_related('project'),
        pk=stage_id,
        is_archived=True,
    )
    if not (request.user.is_superuser or stage.project.is_admin(request.user)):
        raise PermissionDenied
    restore_stage_service(stage)
    record_audit(request, 'stage.restore', stage)
    return redirect('project_detail', project_id=stage.project_id)


@login_required
@require_http_methods(['DELETE'])
def stage_delete(request, stage_id):
    stage = get_object_or_404(
        Stage.objects.select_related('project'),
        pk=stage_id,
        is_archived=True,
    )
    if not can_delete_project(stage.project, request.user):
        raise PermissionDenied
    record_audit(request, 'stage.delete', stage)
    stage.delete()
    return HttpResponse(status=200, headers={'HX-Refresh': 'true'})


# ---------------------------------------------------------------------------
# Обсуждения (ТЗ 3.3)
# ---------------------------------------------------------------------------


@login_required
def discussion_list(request):
    discussions = Discussion.objects.all()
    if not is_app_admin(request.user):
        discussions = discussions.filter(
            models.Q(created_by=request.user)
            | models.Q(participants=request.user)
        ).distinct()
    discussions = discussions.prefetch_related('messages').order_by('-created_at')
    return render(request, 'core/discussions/list.html', {'discussions': discussions})


@login_required
def discussion_create(request):
    if request.method == 'POST':
        form = DiscussionForm(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                discussion = form.save(commit=False)
                discussion.created_by = request.user
                discussion.save()
                # form.save_m2m() is needed to save the participants from the form
                form.save_m2m()
                # Add the creator to the participants
                discussion.participants.add(request.user)
                record_audit(request, 'discussion.create', discussion)
            return redirect('discussion_detail', discussion_id=discussion.id)
        return render(request, 'core/discussions/form.html', {'form': form}, status=422)

    form = DiscussionForm(user=request.user)
    return render(request, 'core/discussions/form.html', {'form': form})


@login_required
def discussion_detail(request, discussion_id):
    discussion = get_discussion_or_403(discussion_id, request.user)
    messages = discussion.messages.select_related('sender').all()
    form = MessageForm()
    return render(request, 'core/discussions/detail.html', {
        'discussion': discussion,
        'messages': messages,
        'form': form,
    })


@app_admin_required
@require_http_methods(['DELETE'])
def discussion_delete(request, discussion_id):
    discussion = get_object_or_404(Discussion, pk=discussion_id)
    record_audit(request, 'discussion.delete', discussion)
    discussion.delete()
    if request.GET.get('redirect') == '1':
        return HttpResponse(
            headers={'HX-Redirect': reverse('discussion_list')}
        )
    return HttpResponse(status=200)


@login_required
@require_POST
def message_create(request, discussion_id):
    """
    POST /discussions/<id>/messages/create/
    Форма отправки сообщения целится (hx-target/hx-swap на самом <form>)
    в #message-list с hx-swap="beforeend" — это верно для УСПЕШНОЙ отправки
    (новое сообщение добавляется в конец истории). Но раньше при ОШИБКЕ
    валидации view делала redirect() на discussion_detail — а htmx сам
    следует за редиректом и получившийся ответ (целая HTML-страница)
    вставляет через тот же beforeend в #message-list, то есть в чат
    буквально влетала вложенная HTML-страница целиком.

    Исправление: ошибка не идёт в основной таргет вообще. Вместо этого
    отдельным OOB-блоком (hx-swap-oob) обновляется #message-error — блок
    рядом с полем ввода, который для этого и существует в detail.html.
    Основное тело ответа при ошибке оставляем пустым, поэтому beforeend
    в #message-list ничего не добавляет.
    """
    discussion = get_discussion_or_403(discussion_id, request.user)
    if not allow_request(
        request, 'message-create', limit=60, window_seconds=60
    ):
        return HttpResponse('Слишком много сообщений.', status=429)
    form = MessageForm(request.POST)

    if form.is_valid():
        with transaction.atomic():
            message = form.save(commit=False)
            message.discussion = discussion
            message.sender = request.user
            message.save()
            record_audit(request, 'message.create', message)
            for recipient in discussion.participants.exclude(
                pk=request.user.pk
            ).filter(is_active=True):
                enqueue_outbound(
                    recipient=recipient,
                    kind=Notification.Kind.MESSAGE_ADDED,
                    text=(
                        f'{request.user.display_name}: новое сообщение '
                        f'в обсуждении «{discussion.title}».'
                    ),
                    dedupe_key=f'message:{message.pk}:{recipient.pk}',
                )
        html = render_to_string(
            'core/discussions/message.html', {'message': message}, request=request
        )
        # Плейсхолдер удаляем тем же OOB-механизмом, чтобы это одинаково
        # работало при собственной отправке и при получении через polling.
        html += '<div id="no-messages-placeholder" hx-swap-oob="delete"></div>'
        # На успехе очищаем предыдущую ошибку, если она была.
        html += '<div id="message-error" hx-swap-oob="true"></div>'
        return HttpResponse(html)

    error_text = escape(
        ' '.join(form.errors.get('text', ['Не удалось отправить сообщение.']))
    )
    html = (
        '<div id="message-error" hx-swap-oob="true">'
        f'<div class="form-error">{error_text}</div>'
        '</div>'
    )
    return HttpResponse(html)


@login_required
@require_POST
def message_update(request, message_id):
    message = get_object_or_404(
        Message.objects.select_related('discussion', 'sender'),
        pk=message_id,
    )
    get_discussion_or_403(message.discussion_id, request.user)
    if message.sender_id != request.user.pk:
        raise PermissionDenied
    form = MessageForm(request.POST, instance=message)
    if not form.is_valid():
        return HttpResponse('Сообщение не может быть пустым.', status=422)
    message = form.save(commit=False)
    message.edited_at = timezone.now()
    message.save()
    record_audit(request, 'message.update', message)
    return render(
        request,
        'core/discussions/message.html',
        {'message': message},
    )


@login_required
@require_http_methods(['DELETE'])
def message_delete(request, message_id):
    message = get_object_or_404(
        Message.objects.select_related('discussion', 'sender'),
        pk=message_id,
    )
    get_discussion_or_403(message.discussion_id, request.user)
    if message.sender_id != request.user.pk and not is_app_admin(request.user):
        raise PermissionDenied
    record_audit(request, 'message.delete', message)
    message.delete()
    return HttpResponse(status=200)


@login_required
def discussion_messages_poll(request, discussion_id):
    """
    GET /discussions/<id>/messages/poll/?after=<id последнего сообщения в DOM>
    Возвращает только новые сообщения, чтобы не дублировать историю и не
    сбрасывать позицию прокрутки чата.
    """
    discussion = get_discussion_or_403(discussion_id, request.user)
    if not allow_request(
        request, 'message-poll', limit=120, window_seconds=10 * 60
    ):
        return HttpResponse(status=429)

    try:
        after_id = int(request.GET.get('after') or 0)
    except (TypeError, ValueError):
        after_id = 0

    new_messages = (
        discussion.messages.select_related('sender')
        .filter(id__gt=after_id)
        .order_by('id')
    )

    html = ''.join(
        render_to_string(
            'core/discussions/message.html', {'message': message}, request=request
        )
        for message in new_messages
    )
    if new_messages:
        html += '<div id="no-messages-placeholder" hx-swap-oob="delete"></div>'
    return HttpResponse(html)
