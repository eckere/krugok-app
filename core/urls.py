from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('dev-login/', views.dev_login, name='dev_login'),
    path('auth/telegram/', views.auth_telegram, name='auth_telegram'),
]
