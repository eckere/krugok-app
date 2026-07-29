from django.core.exceptions import PermissionDenied
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404

from .access import is_app_admin
from .models import Discussion, Project, Stage, Task, TelegramUser


def get_accessible_projects(user, *, include_archived: bool = False) -> QuerySet[Project]:
    if is_app_admin(user):
        projects = Project.objects.all()
    else:
        projects = Project.objects.filter(
            Q(owner=user)
            | Q(project_memberships__user=user)
        ).distinct()
    if not include_archived:
        projects = projects.filter(is_archived=False)
    return projects


def get_accessible_tasks(user) -> QuerySet[Task]:
    if is_app_admin(user):
        return Task.objects.all()
    return Task.objects.filter(
        Q(project__in=get_accessible_projects(user))
        | Q(project__isnull=True, creator=user)
        | Q(project__isnull=True, assignee=user)
        | Q(project__isnull=True, discussions__created_by=user)
        | Q(project__isnull=True, discussions__participants=user)
    ).distinct()


def get_collaborators(user) -> QuerySet[TelegramUser]:
    """Пользователи, с которыми уже существует рабочий контекст."""
    if is_app_admin(user):
        return TelegramUser.objects.filter(is_active=True)
    projects = get_accessible_projects(user, include_archived=True)
    discussion_ids = Discussion.objects.filter(
        Q(created_by=user) | Q(participants=user)
    ).values('pk')
    return TelegramUser.objects.filter(
        Q(pk=user.pk)
        | Q(owned_projects__in=projects)
        | Q(project_memberships__project__in=projects)
        | Q(created_tasks__in=get_accessible_tasks(user))
        | Q(assigned_tasks__in=get_accessible_tasks(user))
        | Q(created_discussions__in=discussion_ids)
        | Q(discussions__in=discussion_ids),
        is_active=True,
    ).distinct()


def can_view_profile(target: TelegramUser, user) -> bool:
    return bool(
        user.is_authenticated
        and (
            user.pk == target.pk
            or is_app_admin(user)
            or get_collaborators(user).filter(pk=target.pk).exists()
        )
    )


def can_manage_project(project: Project, user) -> bool:
    return user.is_superuser or project.is_admin(user)


def can_delete_project(project: Project, user) -> bool:
    return is_app_admin(user) or project.is_owner(user)


def can_edit_task(task: Task, user) -> bool:
    if user.is_superuser:
        return True
    if task.project_id:
        return task.project.is_member(user) and (
            task.creator_id == user.id or task.project.is_admin(user)
        )
    return task.creator_id == user.id


def can_change_task_status(task: Task, user) -> bool:
    if can_edit_task(task, user):
        return True
    if task.project_id:
        return task.assignee_id == user.id and task.project.is_member(user)
    return task.assignee_id == user.id


def get_project_or_403(project_id, user, *, required_role: str = 'member'):
    project = get_object_or_404(Project, id=project_id, is_archived=False)
    checks = {
        'member': project.is_member,
        'admin': project.is_admin,
        'owner': project.is_owner,
    }
    try:
        has_access = (
            (required_role == 'member' and is_app_admin(user))
            or user.is_superuser
            or checks[required_role](user)
        )
    except KeyError as exc:
        raise ValueError(f'Неизвестная роль доступа: {required_role}') from exc
    if not has_access:
        raise PermissionDenied
    return project


def get_stage_or_403(stage_id, user, *, required_role: str = 'member'):
    stage = get_object_or_404(Stage, id=stage_id, is_archived=False, project__is_archived=False)
    if required_role == 'admin':
        has_access = can_manage_project(stage.project, user)
    elif required_role == 'owner':
        has_access = can_delete_project(stage.project, user)
    elif required_role == 'member':
        has_access = is_app_admin(user) or stage.project.is_member(user)
    else:
        raise ValueError(f'Неизвестная роль доступа: {required_role}')
    if not has_access:
        raise PermissionDenied
    return stage


def get_task_or_403(task_id, user, *, permission: str = 'view'):
    task = get_object_or_404(Task.objects.select_related('project'), id=task_id)
    checks = {
        'view': lambda: get_accessible_tasks(user).filter(pk=task.pk).exists(),
        'edit': lambda: can_edit_task(task, user),
        'status': lambda: can_change_task_status(task, user),
        'delete': lambda: can_edit_task(task, user),
    }
    try:
        has_access = checks[permission]()
    except KeyError as exc:
        raise ValueError(f'Неизвестное право доступа к задаче: {permission}') from exc
    if not has_access:
        raise PermissionDenied
    return task


def get_discussion_or_403(discussion_id, user):
    discussion = get_object_or_404(Discussion, id=discussion_id)
    if not (
        is_app_admin(user)
        or discussion.created_by_id == user.id
        or discussion.participants.filter(pk=user.pk).exists()
    ):
        raise PermissionDenied
    return discussion
