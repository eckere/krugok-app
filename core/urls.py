from django.urls import path

from . import views

urlpatterns = [
    path('healthz/', views.healthcheck, name='healthcheck'),
    path('', views.index, name='index'),
    path('dev-login/', views.dev_login, name='dev_login'),
    path('dev-switch-account/<int:user_id>/', views.dev_switch_account, name='dev_switch_account'),
    path('auth/telegram/', views.auth_telegram, name='auth_telegram'),
    path('auth/telegram/widget/', views.auth_telegram_widget, name='auth_telegram_widget'),
    path('invite/', views.invite_redeem, name='invite_redeem'),
    path('invite/<str:code>/', views.invite_link, name='invite_link'),
    path('invites/', views.invite_list, name='invite_list'),
    path('invites/create/', views.invite_create, name='invite_create'),
    path('users/<int:user_id>/', views.profile_detail, name='profile_detail'),

    path('tasks/', views.task_list, name='task_list'),
    path('tasks/create/', views.task_create, name='task_create'),
    path('tasks/form-options/', views.task_form_options, name='task_form_options'),
    path('tasks/<int:task_id>/edit/', views.task_update, name='task_update'),
    path('tasks/<int:task_id>/delete/', views.task_delete, name='task_delete'),
    path('tasks/<int:task_id>/status/', views.task_status, name='task_status'),

    path('projects/', views.project_list, name='project_list'),
    path('projects/create/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/archive/', views.project_archive, name='project_archive'),
    path('projects/<int:project_id>/edit/', views.project_update, name='project_update'),
    path('projects/<int:project_id>/delete/', views.project_delete, name='project_delete'),
    path('projects/<int:project_id>/members/create/', views.project_member_create, name='project_member_create'),
    path('memberships/<int:membership_id>/edit/', views.project_member_update, name='project_member_update'),
    path('memberships/<int:membership_id>/delete/', views.project_member_delete, name='project_member_delete'),

    path('projects/<int:project_id>/stages/create/', views.stage_create, name='stage_create'),
    path('stages/<int:stage_id>/edit/', views.stage_update, name='stage_update'),
    path('stages/<int:stage_id>/archive/', views.stage_archive, name='stage_archive'),
    path('stages/<int:stage_id>/delete/', views.stage_delete, name='stage_delete'),

    path('tasks/<int:task_id>/', views.task_detail, name='task_detail'),
    path('tasks/<int:task_id>/comments/create/', views.comment_create, name='comment_create'),

    path('discussions/', views.discussion_list, name='discussion_list'),
    path('discussions/create/', views.discussion_create, name='discussion_create'),
    path('discussions/<int:discussion_id>/', views.discussion_detail, name='discussion_detail'),
    path('discussions/<int:discussion_id>/delete/', views.discussion_delete, name='discussion_delete'),
    path('discussions/<int:discussion_id>/messages/create/', views.message_create, name='message_create'),
    path('discussions/<int:discussion_id>/messages/poll/', views.discussion_messages_poll, name='discussion_messages_poll'),
]
