from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('dev-login/', views.dev_login, name='dev_login'),
    path('auth/telegram/', views.auth_telegram, name='auth_telegram'),

    path('tasks/', views.task_list, name='task_list'),
    path('tasks/create/', views.task_create, name='task_create'),
    path('tasks/<int:task_id>/edit/', views.task_update, name='task_update'),
    path('tasks/<int:task_id>/delete/', views.task_delete, name='task_delete'),
    path('tasks/<int:task_id>/status/', views.task_status, name='task_status'),

    path('projects/', views.project_list, name='project_list'),
    path('projects/create/', views.project_create, name='project_create'),
    path('projects/<int:project_id>/', views.project_detail, name='project_detail'),
    path('projects/<int:project_id>/archive/', views.project_archive, name='project_archive'),

    path('projects/<int:project_id>/stages/create/', views.stage_create, name='stage_create'),
    path('stages/<int:stage_id>/edit/', views.stage_update, name='stage_update'),
    path('stages/<int:stage_id>/archive/', views.stage_archive, name='stage_archive'),
    path('stages/<int:stage_id>/delete/', views.stage_delete, name='stage_delete'),

    path('tasks/<int:task_id>/', views.task_detail, name='task_detail'),
    path('tasks/<int:task_id>/comments/create/', views.comment_create, name='comment_create'),

    path('discussions/', views.discussion_list, name='discussion_list'),
    path('discussions/create/', views.discussion_create, name='discussion_create'),
    path('discussions/<int:discussion_id>/', views.discussion_detail, name='discussion_detail'),
    path('discussions/<int:discussion_id>/messages/create/', views.message_create, name='message_create'),
]
