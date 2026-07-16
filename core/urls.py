from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('auth/telegram/', views.auth_telegram, name='auth_telegram'),
]
