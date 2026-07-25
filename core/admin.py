from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.http import HttpRequest

from .models import Notification, Project, ProjectMembership, Task, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(UserAdmin):
    list_display = ('username', 'telegram_id', 'first_name', 'last_name', 'is_active', 'is_staff', 'is_premium')
    list_filter = ('is_active', 'is_staff', 'is_premium')
    search_fields = ('username', 'first_name', 'last_name', 'telegram_id')
    fieldsets = UserAdmin.fieldsets + (
        ('Telegram', {'fields': ('telegram_id', 'photo_url', 'last_seen', 'language_code', 'is_premium')}),
    )
    readonly_fields = ('last_seen',)


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
