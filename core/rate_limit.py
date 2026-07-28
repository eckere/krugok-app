import hashlib
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .audit import client_ip
from .models import RateLimitBucket


def _key(request, scope: str) -> str:
    user = getattr(request, 'user', None)
    user_marker = (
        f'user:{user.pk}'
        if getattr(user, 'is_authenticated', False)
        else 'anonymous'
    )
    raw = f'{scope}:{user_marker}:{client_ip(request)}'
    return hashlib.sha256(raw.encode()).hexdigest()


def allow_request(
    request,
    scope: str,
    *,
    limit: int,
    window_seconds: int,
) -> bool:
    now = timezone.now()
    key_hash = _key(request, scope)
    try:
        with transaction.atomic():
            bucket, created = RateLimitBucket.objects.select_for_update().get_or_create(
                key_hash=key_hash,
                defaults={'window_started_at': now, 'request_count': 0},
            )
            if not created and now - bucket.window_started_at >= timedelta(
                seconds=window_seconds
            ):
                bucket.window_started_at = now
                bucket.request_count = 0
            bucket.request_count += 1
            bucket.save(
                update_fields=['window_started_at', 'request_count', 'updated_at']
            )
            return bucket.request_count <= limit
    except IntegrityError:
        return False


def cleanup_rate_limits(*, older_than_hours: int = 24) -> int:
    cutoff = timezone.now() - timedelta(hours=older_than_hours)
    deleted, _details = RateLimitBucket.objects.filter(
        updated_at__lt=cutoff
    ).delete()
    return deleted
