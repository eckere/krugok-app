import hashlib

from django.conf import settings

from .models import AuditLog


def client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.rsplit(',', 1)[-1] if forwarded else request.META.get(
        'REMOTE_ADDR', ''
    )).strip()


def hash_ip(value: str) -> str:
    if not value:
        return ''
    salt = settings.SECRET_KEY[-32:]
    return hashlib.sha256(f'{salt}:{value}'.encode()).hexdigest()


def record_audit(request, action: str, entity=None, *, changes=None) -> AuditLog:
    actor = request.user if getattr(request.user, 'is_authenticated', False) else None
    entity_type = entity._meta.label_lower if entity is not None else 'system'
    entity_label = str(entity)[:255] if entity is not None else ''
    if entity_type == 'core.invitecode':
        entity_label = 'Приглашение'
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity.pk) if entity is not None and entity.pk else '',
        entity_label=entity_label,
        changes=changes or {},
        ip_hash=hash_ip(client_ip(request)),
    )
