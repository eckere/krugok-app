"""
core/views.py
"""
import json
import logging

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import models
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    CommentForm,
    DiscussionForm,
    MessageForm,
    ProjectForm,
    ProjectMembershipCreateForm,
    ProjectMembershipRoleForm,
    StageForm,
    TaskForm,
)
from .models import Comment, Project, ProjectMembership, Stage, Task, TelegramUser, Discussion, Message
from .permissions import (
    get_accessible_projects,
    get_accessible_tasks,
    get_discussion_or_403,
    get_project_or_403,
    get_stage_or_403,
    get_task_or_403,
)
from .telegram_auth import InitDataValidationError, validate_init_data

logger = logging.getLogger(__name__)


def _close_modal(response, modal_id):
    """Просит клиент закрыть модальное окно после успешной HTMX-операции."""
    response.headers['HX-Trigger'] = json.dumps(
        {'closeModal': {'id': modal_id}}
    )
    return response




@ensure_csrf_cookie
def index(request):
    context = {}
    if request.user.is_authenticated:
        tasks = get_accessible_tasks(request.user).select_related(
            'project', 'assignee'
        ).order_by('status', 'deadline')
        projects = get_accessible_projects(request.user).order_by('-created_at')
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
    return render(request, 'core/index.html', context)


def dev_login(request):
    """Временный вход в обход Telegram — ТОЛЬКО для локальной разработки."""
    if not settings.DEBUG:
        raise Http404
    user, _created = TelegramUser.objects.get_or_create(
        telegram_id=111222333,
        defaults={'username': 'dev_test', 'first_name': 'Тестировщик', 'last_name': ''},
    )
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('index')


@require_POST
def auth_telegram(request):
    """
    POST /auth/telegram/
    Тело запроса (JSON): {"init_data": "<window.Telegram.WebApp.initData>"}
    """
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Некорректное тело запроса'}, status=400)

    init_data = payload.get('init_data', '')

    try:
        tg_user = validate_init_data(init_data)
    except InitDataValidationError as exc:
        logger.warning('Отклонена попытка входа в Mini App: %s', exc)
        return JsonResponse({'ok': False, 'error': str(exc)}, status=403)

    telegram_id = tg_user['id']
    username = tg_user.get('username') or f'tg_{telegram_id}'

    user, _created = TelegramUser.objects.update_or_create(
        telegram_id=telegram_id,
        defaults={
            'username': username,
            'first_name': tg_user.get('first_name', ''),
            'last_name': tg_user.get('last_name', ''),
            'photo_url': tg_user.get('photo_url'),
        },
    )
    login(request, user)
    return JsonResponse({'ok': True, 'user': {'id': user.id, 'display_name': user.display_name}})


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

    template_name = (
        'core/tasks/list_items.html' if request.htmx else 'core/tasks/list.html'
    )
    return render(request, template_name, {'tasks': tasks, 'filter': filter_value})


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
            task = form.save(commit=False)
            task.creator = request.user
            task.save()
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
    return_to = request.POST.get('return_to') or request.GET.get('return_to')

    if request.method == 'POST':
        form = TaskForm(request.POST, instance=task, user=request.user)
        if form.is_valid():
            form.save()
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
    task.delete()
    return HttpResponse(status=200)


@login_required
@require_POST
def task_status(request, task_id):
    """
    POST /tasks/<id>/status/
    Кнопка "Статус" в card.html не передаёт конкретное значение (нет hx-vals),
    поэтому статус просто циклически переключается: новая -> в процессе ->
    выполнена -> снова новая. Возвращает обновлённую карточку для outerHTML.
    """
    task = get_task_or_403(task_id, request.user, permission='status')
    order = [Task.Status.NEW, Task.Status.IN_PROGRESS, Task.Status.DONE]
    task.status = order[(order.index(task.status) + 1) % len(order)]
    task.save()
    return render(
        request,
        'core/tasks/card.html',
        {
            'task': task,
            'task_project_context': request.POST.get('return_to') == 'project',
        },
    )


@login_required
@require_POST
def comment_create(request, task_id):
    task = get_task_or_403(task_id, request.user)
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = form.save(commit=False)
        comment.task = task
        comment.author = request.user
        comment.save()
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
    projects = get_accessible_projects(request.user).order_by('-created_at')
    if filter_value == 'mine':
        projects = projects.filter(
            models.Q(owner=request.user)
            | models.Q(
                project_memberships__user=request.user,
                project_memberships__role=ProjectMembership.Role.OWNER,
            )
        ).distinct()
    template_name = (
        'core/projects/list_items.html' if request.htmx else 'core/projects/list.html'
    )
    return render(
        request,
        template_name,
        {'projects': projects, 'filter': filter_value},
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
    )
    return render(
        request,
        'core/projects/detail.html',
        {
            'project': project,
            'stages': stages,
            'unassigned_tasks': unassigned_tasks,
            'memberships': memberships,
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
            project = form.save(commit=False)
            project.owner = request.user
            project.save()
            ProjectMembership.objects.create(
                project=project,
                user=request.user,
                role=ProjectMembership.Role.OWNER,
            )
            response = render(request, 'core/projects/card.html', {'project': project})
            return _close_modal(response, 'project-modal')
        return render(
            request,
            'core/projects/form.html',
            {'form': form},
            status=200 if request.htmx else 422,
        )

    return render(request, 'core/projects/form.html', {'form': ProjectForm()})


@login_required
def project_update(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='admin')
    return_to = request.POST.get('return_to') or request.GET.get('return_to')
    if request.method == 'POST':
        form = ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            if return_to == 'detail':
                return HttpResponse(
                    headers={'HX-Redirect': reverse('project_detail', args=[project.id])}
                )
            response = render(request, 'core/projects/card.html', {'project': project})
            return _close_modal(response, 'project-modal')
        return render(
            request,
            'core/projects/form.html',
            {'form': form, 'project': project, 'return_to': return_to},
            status=200 if request.htmx else 422,
        )

    return render(
        request,
        'core/projects/form.html',
        {'form': ProjectForm(instance=project), 'project': project, 'return_to': return_to},
    )


@login_required
@require_http_methods(['DELETE'])
def project_delete(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='owner')
    project.delete()
    return HttpResponse(status=200)


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
    if request.POST.get('return_to') == 'projects':
        return HttpResponse(headers={'HX-Redirect': reverse('project_list')})
    return HttpResponse(status=200)


@login_required
@require_http_methods(['GET', 'POST'])
def project_member_create(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='owner')
    if request.method == 'POST':
        form = ProjectMembershipCreateForm(request.POST, project=project)
        if form.is_valid():
            membership = form.save(commit=False)
            membership.project = project
            membership.save()
            response = render(
                request,
                'core/projects/members/card.html',
                {'project': project, 'membership': membership},
            )
            return _close_modal(response, 'member-modal')
        return render(
            request,
            'core/projects/members/form.html',
            {'project': project, 'form': form},
            status=200 if request.htmx else 422,
        )

    return render(
        request,
        'core/projects/members/form.html',
        {'project': project, 'form': ProjectMembershipCreateForm(project=project)},
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
            response = render(
                request,
                'core/projects/members/card.html',
                {'project': project, 'membership': membership},
            )
            return _close_modal(response, 'member-modal')
        return render(
            request,
            'core/projects/members/form.html',
            {'project': project, 'membership': membership, 'form': form},
            status=200 if request.htmx else 422,
        )

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
        ProjectMembership.objects.select_related('project'),
        pk=membership_id,
    )
    project = get_project_or_403(
        membership.project_id,
        request.user,
        required_role='owner',
    )
    if membership.user_id == project.owner_id:
        raise PermissionDenied
    membership.delete()
    return HttpResponse(status=200)


def _normalize_stage_order(project):
    stages = list(project.stages.filter(is_archived=False).order_by('order', 'created_at'))
    for index, stage in enumerate(stages, start=1):
        if stage.order != index:
            stage.order = index
            stage.save(update_fields=['order'])


@login_required
def stage_create(request, project_id):
    project = get_project_or_403(project_id, request.user, required_role='admin')
    if request.method == 'POST':
        form = StageForm(request.POST)
        if form.is_valid():
            stage = form.save(commit=False)
            stage.project = project
            if stage.order is None:
                max_order = project.stages.filter(is_archived=False).aggregate(models.Max('order'))['order__max']
                stage.order = (max_order or 0) + 1
            else:
                project.stages.filter(is_archived=False, order__gte=stage.order).update(order=models.F('order') + 1)
            stage.save()
            _normalize_stage_order(project)
            response = render(
                request,
                'core/stages/card.html',
                {'project': project, 'stage': stage},
            )
            return _close_modal(response, 'stage-modal')
        return render(
            request,
            'core/stages/form.html',
            {'form': form, 'project': project},
            status=200 if request.htmx else 422,
        )

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
            if new_order is None:
                max_order = project.stages.filter(is_archived=False).aggregate(models.Max('order'))['order__max'] or 0
                new_order = max_order + 1
            if old_order != new_order:
                stage.order = None
                stage.save(update_fields=['order'])
                if old_order is not None and old_order < new_order:
                    project.stages.filter(is_archived=False, order__gt=old_order, order__lte=new_order).exclude(pk=stage.pk).update(order=models.F('order') - 1)
                else:
                    project.stages.filter(is_archived=False, order__gte=new_order, order__lt=old_order if old_order is not None else new_order).exclude(pk=stage.pk).update(order=models.F('order') + 1)
                stage.order = new_order
            stage.save()
            _normalize_stage_order(project)
            response = render(
                request,
                'core/stages/card.html',
                {'project': project, 'stage': stage},
            )
            return _close_modal(response, 'stage-modal')
        return render(
            request,
            'core/stages/form.html',
            {'form': form},
            status=200 if request.htmx else 422,
        )

    return render(request, 'core/stages/form.html', {'form': StageForm(instance=stage)})


@login_required
@require_POST
def stage_archive(request, stage_id):
    stage = get_stage_or_403(stage_id, request.user, required_role='admin')
    stage.is_archived = True
    stage.save()
    _normalize_stage_order(stage.project)
    return HttpResponse(status=200)


@login_required
@require_http_methods(['DELETE'])
def stage_delete(request, stage_id):
    stage = get_stage_or_403(stage_id, request.user, required_role='admin')
    project = stage.project
    stage.delete()
    _normalize_stage_order(project)
    return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# Обсуждения (ТЗ 3.3)
# ---------------------------------------------------------------------------


@login_required
def discussion_list(request):
    discussions = Discussion.objects.filter(
        models.Q(created_by=request.user) | models.Q(participants=request.user)
    ).distinct().prefetch_related('messages').order_by('-created_at')
    return render(request, 'core/discussions/list.html', {'discussions': discussions})


@login_required
def discussion_create(request):
    if request.method == 'POST':
        form = DiscussionForm(request.POST, user=request.user)
        if form.is_valid():
            discussion = form.save(commit=False)
            discussion.created_by = request.user
            discussion.save()
            # form.save_m2m() is needed to save the participants from the form
            form.save_m2m()
            # Add the creator to the participants
            discussion.participants.add(request.user)
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


@login_required
@require_POST
def message_create(request, discussion_id):
    discussion = get_discussion_or_403(discussion_id, request.user)
    form = MessageForm(request.POST)
    if form.is_valid():
        message = form.save(commit=False)
        message.discussion = discussion
        message.sender = request.user
        message.save()
        return render(request, 'core/discussions/message.html', {'message': message})

    # In case of an error, we can't just render the form, because the detail view needs more context
    # A full page reload or a more complex HTMX error handling would be better.
    # For now, let's redirect, though it's not ideal for HTMX.
    # A better solution for real-world scenarios might be to return a 422 with an error message in the header.
    return redirect('discussion_detail', discussion_id=discussion_id)
