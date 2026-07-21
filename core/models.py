from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class TelegramUser(AbstractUser):
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True, db_index=True)
    photo_url = models.URLField(blank=True, null=True)
    last_seen = models.DateTimeField(auto_now=True)
    language_code = models.CharField(max_length=12, blank=True, default='en')
    is_premium = models.BooleanField(default=False)

    class Meta:
        ordering = ['first_name']

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def display_name(self):
        return self.get_full_name() or self.username


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