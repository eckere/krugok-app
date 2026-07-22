from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from .models import Project, Stage, Task, Discussion


def get_project_or_403(project_id, user):
    project = get_object_or_404(Project, id=project_id, is_archived=False)
    if not project.is_member(user):
        raise PermissionDenied
    return project


def get_stage_or_403(stage_id, user):
    stage = get_object_or_404(Stage, id=stage_id, is_archived=False, project__is_archived=False)
    if not stage.project.is_member(user):
        raise PermissionDenied
    return stage


def get_task_or_403(task_id, user):
    task = get_object_or_404(Task, id=task_id)
    if task.project_id is not None:
        get_project_or_403(task.project_id, user)
    return task


def get_discussion_or_403(discussion_id, user):
    discussion = get_object_or_404(Discussion, id=discussion_id)
    if not (discussion.created_by == user or user in discussion.participants.all()):
        raise PermissionDenied
    return discussion
