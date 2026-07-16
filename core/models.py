"""
core/models.py

Шаг 1: модели БД для КружокAPP.
На этом этапе все модели лежат в одном файле условного приложения `core`.
При настройке проекта (Шаг 2) решим, нужно ли дробить их на отдельные
Django-приложения (users / projects / tasks / chat) — структура models.py
от этого принципиально не изменится, поменяются только импорты.
"""

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MinLengthValidator
from django.db import models
from django.utils import timezone


class TelegramUser(AbstractUser):
    """
    Кастомная модель пользователя — наследуется от AbstractUser, а не
    создаётся с нуля. Это даёт бесплатно:
      - request.user и стандартную сессионную аутентификацию Django
        (после валидации initData мы просто логиним пользователя через
        обычный django.contrib.auth.login(), дальше HTMX-запросы едут
        по обычной cookie-сессии — никакого JWT городить не нужно);
      - доступ к /admin/, что при 4 пользователях крайне удобно для
        ручных правок и отладки на VPS;
      - совместимость с @login_required и системой прав Django.

    Поле username (унаследовано от AbstractUser) будет заполняться так:
      - если в Telegram у юзера есть @username — берём его;
      - если нет — генерируем 'tg_<telegram_id>', т.к. username в Django
        обязателен и уникален, а в Telegram он опционален.

    ВАЖНО для Шага 2: AUTH_USER_MODEL = 'core.TelegramUser' нужно прописать
    в settings.py ДО первой миграции — Django не даёт подменить модель
    пользователя постфактum.
    """
    # null=True — нужен, чтобы createsuperuser (аккаунт для входа в /admin/
    # напрямую, без Telegram) не падал на NOT NULL constraint. SQLite,
    # как и Postgres, допускает несколько NULL в UNIQUE-колонке.
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True, db_index=True)
    photo_url = models.URLField(blank=True, null=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['first_name']

    def __str__(self):
        return self.get_full_name() or self.username

    @property
    def display_name(self):
        return self.get_full_name() or self.username


class Project(models.Model):
    """Проект — верхнеуровневая папка, например «Курс по истории — Древний мир»."""

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='owned_projects',
    )
    # Раз команда всего 4 человека, но не факт, что каждый должен видеть
    # каждый проект — участников выбираем вручную, как и в чатах.
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
    """Этап (ступень) внутри проекта: «Подготовка материалов», «Запись уроков» и т.д."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='stages')
    name = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)  # ручная сортировка этапов

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']
        unique_together = ('project', 'order')

    def __str__(self):
        return f'{self.project.name} / {self.name}'


class Task(models.Model):
    """Задача — центральная сущность приложения."""

    class Status(models.TextChoices):
        NEW = 'new', 'Новая'
        IN_PROGRESS = 'in_progress', 'В процессе'
        DONE = 'done', 'Выполнена'

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Задача не обязана принадлежать проекту (нужен общий список "мои/все"
    # задачи вне всякой структуры). Если указан stage — project подтягивается
    # автоматически в save(), чтобы project и stage.project не разъезжались.
    project = models.ForeignKey(
        Project, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks'
    )
    stage = models.ForeignKey(
        Stage, on_delete=models.SET_NULL, null=True, blank=True, related_name='tasks'
    )

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_tasks'
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
            models.Index(fields=['status', 'deadline']),  # под задачи cron/celery-beat
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        # Если задачу привязали к этапу — проект берём из этапа,
        # чтобы не хранить противоречивые данные.
        if self.stage_id:
            self.project_id = self.stage.project_id
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


class Comment(models.Model):
    """Комментарий к задаче (вкладка «Комментарии» на экране задачи)."""

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='comments'
    )
    text = models.TextField(validators=[MinLengthValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'Комментарий {self.author} к «{self.task}»'


class Discussion(models.Model):
    """
    Обсуждение (чат). Может быть привязано к конкретной задаче
    или быть свободной темой (task=None) — по ТЗ п.3.3.
    Участники выбираются вручную, поэтому M2M, а не «все участники проекта».
    """

    title = models.CharField(max_length=255)
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE, null=True, blank=True, related_name='discussions'
    )
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='discussions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_discussions'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Message(models.Model):
    """Сообщение внутри Discussion — история переписки."""

    discussion = models.ForeignKey(Discussion, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='messages'
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.sender}: {self.text[:30]}'


class Notification(models.Model):
    """
    Лог отправленных уведомлений по дедлайнам (постановка / приближение /
    просрочка — ТЗ п.3.2). Нужен, чтобы периодическая задача (cron или
    celery-beat, обсудим на этапе деплоя) не слала одно и то же уведомление
    повторно при каждом запуске.
    """

    class Type(models.TextChoices):
        DEADLINE_SET = 'deadline_set', 'Дедлайн установлен'
        DEADLINE_SOON = 'deadline_soon', 'Дедлайн приближается'
        DEADLINE_OVERDUE = 'deadline_overdue', 'Дедлайн просрочен'

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name='notifications')
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications'
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('task', 'recipient', 'type')  # защита от дублей-уведомлений
