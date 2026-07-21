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
]
