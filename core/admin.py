from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Comment, Discussion, Message, Notification, Project, Stage, Task, TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(UserAdmin):
    list_display = ('username', 'telegram_id', 'first_name', 'last_name', 'is_active', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Telegram', {'fields': ('telegram_id', 'photo_url', 'last_seen')}),
    )
    readonly_fields = ('last_seen',)


admin.site.register(Project)
admin.site.register(Stage)
admin.site.register(Task)
admin.site.register(Comment)
admin.site.register(Discussion)
admin.site.register(Message)
admin.site.register(Notification)
