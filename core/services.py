"""Транзакционные прикладные операции, общие для HTTP и admin."""

from django.db import transaction

from .models import Stage


@transaction.atomic
def place_stage(stage: Stage, requested_order: int | None = None) -> Stage:
    """Без конфликтов перестраивает плотный порядок активных этапов проекта."""
    active = list(
        Stage.objects.select_for_update()
        .filter(project_id=stage.project_id, is_archived=False)
        .order_by('order', 'created_at', 'pk')
    )
    active = [item for item in active if item.pk != stage.pk]

    if requested_order is None:
        index = len(active)
    else:
        index = max(0, min(requested_order - 1, len(active)))
    active.insert(index, stage)

    stage_ids = [item.pk for item in active if item.pk]
    if stage_ids:
        Stage.objects.filter(pk__in=stage_ids).update(order=None)
    for position, item in enumerate(active, start=1):
        item.order = position
    Stage.objects.bulk_update(active, ['order'])
    stage.refresh_from_db()
    return stage


@transaction.atomic
def archive_stage(stage: Stage) -> None:
    stage.is_archived = True
    stage.order = None
    stage.save(update_fields=['is_archived', 'order', 'updated_at'])
    normalize_stage_order(stage.project_id)


@transaction.atomic
def restore_stage(stage: Stage, requested_order: int | None = None) -> Stage:
    stage.is_archived = False
    stage.order = None
    stage.save(update_fields=['is_archived', 'order', 'updated_at'])
    return place_stage(stage, requested_order)


@transaction.atomic
def normalize_stage_order(project_id: int) -> None:
    stages = list(
        Stage.objects.select_for_update()
        .filter(project_id=project_id, is_archived=False)
        .order_by('order', 'created_at', 'pk')
    )
    if not stages:
        return
    Stage.objects.filter(pk__in=[stage.pk for stage in stages]).update(order=None)
    for position, stage in enumerate(stages, start=1):
        stage.order = position
    Stage.objects.bulk_update(stages, ['order'])
