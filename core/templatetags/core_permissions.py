from django import template

from core.access import is_app_admin
from core.permissions import (
    can_change_task_status,
    can_delete_project,
    can_edit_task,
    can_manage_project,
)

register = template.Library()


@register.filter
def can_admin_project(project, user) -> bool:
    return can_manage_project(project, user)


@register.filter
def can_own_project(project, user) -> bool:
    return can_delete_project(project, user)


@register.filter
def can_manage_task(task, user) -> bool:
    return can_edit_task(task, user)


@register.filter
def can_update_task_status(task, user) -> bool:
    return can_change_task_status(task, user)


@register.filter
def can_remove_project_member(project, user) -> bool:
    return is_app_admin(user) or can_delete_project(project, user)
