import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


def generate_invite_code() -> str:
    """Возвращает достаточно длинный URL-безопасный одноразовый код."""
    return secrets.token_urlsafe(24)


def default_invite_expiry():
    return timezone.now() + timedelta(days=7)


INVITE_START_PARAM_PREFIX = 'invite_'


class TelegramUser(AbstractUser):
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True, db_index=True)
    photo_url = models.URLField(blank=True, null=True)
    last_seen = models.DateTimeField(auto_now=True)
    language_code = models.CharField(max_length=12, blank=True, default='en')
    is_premium = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)

    class Meta:
        ordering = ['first_name']

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def display_name(self):
        return self.get_full_name() or self.username


class InviteCode(models.Model):
    """Персональное одноразовое приглашение в приложение."""

    code = models.CharField(
        max_length=64,
        unique=True,
        default=generate_invite_code,
        editable=False,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_invite_codes',
    )
    used_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='used_invite_codes',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(
        default=default_invite_expiry,
        null=True,
        blank=True,
        help_text='По умолчанию приглашение действует 7 дней. Оставьте пустым для бессрочного.',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.code

    def get_absolute_url(self):
        return reverse('invite_link', args=[self.code])

    def get_telegram_start_param(self) -> str:
        """Параметр запуска Main Mini App для этого приглашения."""
        return f'{INVITE_START_PARAM_PREFIX}{self.code}'

    def get_telegram_url(self) -> str:
        """Deep-link, открывающий приглашение сразу в Telegram Mini App."""
        bot_username = settings.TELEGRAM_BOT_USERNAME
        if not bot_username:
            return ''
        query = urlencode({'startapp': self.get_telegram_start_param()})
        return f'https://t.me/{bot_username}?{query}'

    def is_redeemable(self, *, at=None) -> bool:
        """Проверка состояния без раскрытия причины недействительности кода."""
        at = at or timezone.now()
        return (
            self.is_active
            and self.used_by_id is None
            and (self.expires_at is None or self.expires_at > at)
        )


class Project(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_projects',
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='ProjectMembership',
        related_name='projects',
        blank=True,
    )

    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def get_members(self):
        return TelegramUser.objects.filter(
            models.Q(pk=self.owner_id) | models.Q(project_memberships__project=self)
        ).distinct()

    def is_member(self, user):
        return (
            self.owner_id == user.id
            or self.project_memberships.filter(user=user).exists()
        )

    def is_admin(self, user):
        return (
            self.owner_id == user.id
            or self.project_memberships.filter(user=user, role=ProjectMembership.Role.ADMIN).exists()
        )

    def is_owner(self, user):
        return self.owner_id == user.id or self.project_memberships.filter(user=user, role=ProjectMembership.Role.OWNER).exists()


class ProjectMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'owner', 'Владелец'
        ADMIN = 'admin', 'Админ'
        MEMBER = 'member', 'Участник'

    project = models.ForeignKey(
        'Project',
        on_delete=models.CASCADE,
        related_name='project_memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='project_memberships',
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['project', 'user'], name='unique_project_membership'),
        ]

    def __str__(self):
        return f'{self.user} ({self.get_role_display()}) in {self.project}'

    def clean(self):
        super().clean()
        if not self.project_id or not self.user_id:
            return
        is_project_owner = self.project.owner_id == self.user_id
        if is_project_owner and self.role != self.Role.OWNER:
            raise ValidationError({'role': 'Владелец проекта должен иметь роль владельца.'})
        if self.role == self.Role.OWNER and not is_project_owner:
            raise ValidationError({'role': 'Роль владельца доступна только владельцу проекта.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.project_id and self.user_id == self.project.owner_id:
            raise ValidationError('Нельзя удалить членство владельца проекта.')
        return super().delete(*args, **kwargs)


class Stage(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = 'not_started', 'Не начат'
        IN_PROGRESS = 'in_progress', 'В процессе'
        DONE = 'done', 'Выполнена'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='stages',
    )
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(null=True, blank=True)
    deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['project', 'order'], name='unique_stage_order_per_project'),
        ]

    def __str__(self):
        return self.name


class Task(models.Model):
    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        IN_PROGRESS = 'in_progress', 'В процессе'
        DONE = 'done', 'Выполнена'

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    stage = models.ForeignKey(
        Stage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_tasks',
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks',
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    deadline = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'deadline']),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.stage and self.project_id != self.stage.project_id:
            raise ValidationError({'stage': 'Этап должен принадлежать выбранному проекту.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.status == self.Status.DONE and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != self.Status.DONE:
            self.completed_at = None
        super().save(*args, **kwargs)

    @property
    def is_overdue(self) -> bool:
        return bool(
            self.deadline
            and self.status != self.Status.DONE
            and self.deadline < timezone.now()
        )


class Notification(models.Model):
    class Kind(models.TextChoices):
        DEADLINE_SET = 'deadline_set', 'Дедлайн установлен'
        DEADLINE_APPROACHING = 'deadline_approaching', 'Дедлайн приближается'
        DEADLINE_OVERDUE = 'deadline_overdue', 'Дедлайн просрочен'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Ожидает отправки'
        SENT = 'sent', 'Отправлено'
        FAILED = 'failed', 'Ошибка отправки'

    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('task', 'kind')
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.get_kind_display()}: {self.task}'


class Comment(models.Model):
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    text = models.TextField(validators=[MinLengthValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Comment by {self.author} on {self.task}'


class Discussion(models.Model):
    title = models.CharField(max_length=255)
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='discussions',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_discussions',
    )
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='discussions',
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Message(models.Model):
    discussion = models.ForeignKey(
        Discussion,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    text = models.TextField(validators=[MinLengthValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Message by {self.sender} in {self.discussion}'
