from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.conf import settings
from django.http import HttpRequest
from django.utils.html import format_html

from .models import InviteCode, Notification, Project, ProjectMembership, Task, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(UserAdmin):
    list_display = ('username', 'telegram_id', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_premium', 'is_verified')
    list_filter = ('is_active', 'is_staff', 'is_premium', 'is_verified')
    search_fields = ('username', 'first_name', 'last_name', 'telegram_id')
    fieldsets = UserAdmin.fieldsets + (
        ('Telegram', {'fields': ('telegram_id', 'photo_url', 'last_seen', 'language_code', 'is_premium', 'is_verified')}),
    )
    readonly_fields = ('last_seen',)


@admin.register(InviteCode)
class InviteCodeAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'invite_url',
        'created_by',
        'is_active',
        'expires_at',
        'used_by',
        'used_at',
    )
    list_filter = ('is_active', 'created_at', 'expires_at')
    search_fields = ('code', 'created_by__username', 'used_by__username')
    readonly_fields = ('code', 'invite_url', 'created_at', 'used_at', 'used_by')
    fields = (
        'code',
        'invite_url',
        'created_by',
        'is_active',
        'expires_at',
        'used_by',
        'used_at',
        'created_at',
    )

    @admin.display(description='Ссылка приглашения')
    def invite_url(self, obj: InviteCode | None):
        if obj is None:
            return 'Ссылка появится после сохранения приглашения.'
        url = obj.get_telegram_url()
        if not url:
            path = obj.get_absolute_url()
            url = f'{settings.INVITE_BASE_URL}{path}' if settings.INVITE_BASE_URL else path
        return format_html('<a href="{0}" target="_blank" rel="noopener">{0}</a>', url)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'is_archived', 'created_at')
    list_filter = ('is_archived', 'created_at')
    search_fields = ('name', 'description', 'owner__username')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'project', 'status', 'assignee', 'deadline', 'created_at')
    list_filter = ('status', 'deadline', 'created_at')
    search_fields = ('title', 'description', 'project__name', 'assignee__username')
    readonly_fields = ('created_at', 'updated_at', 'completed_at')


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = ('project', 'user', 'role', 'created_at')
    list_filter = ('role', 'created_at')
    search_fields = ('project__name', 'user__username')
    readonly_fields = ('created_at',)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('task', 'recipient', 'kind', 'status', 'created_at')
    list_filter = ('kind', 'status', 'created_at')
    search_fields = (
        'task__title',
        'recipient__username',
        'recipient__first_name',
        'recipient__last_name',
    )
    readonly_fields = (
        'task',
        'recipient',
        'kind',
        'status',
        'error_message',
        'created_at',
        'sent_at',
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: Notification | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: Notification | None = None,
    ) -> bool:
        return False
