from django.conf import settings
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('words.urls')),
]

if getattr(settings, 'SERVER_MODE', False):
    from accounts import control
    from accounts.auth import PortalLogin, PortalLogout
    from accounts.registration import register

    from .server_auth import health

    handler403 = 'vocab_project.server_auth.forbidden'
    handler404 = 'vocab_project.server_auth.not_found'
    handler500 = 'vocab_project.server_auth.server_error'
    urlpatterns = [
        path('login/', PortalLogin.as_view(), name='owner_login'),
        path('logout/', PortalLogout.as_view(), name='owner_logout'),
        path('register/', register, name='register'),
        path('healthz/', health, name='health'),
        path('control/', control.home, name='site_control'),
        path('control/announcements/preview/', control.preview_announcement, name='announcement_preview'),
        path('control/<str:section>/', control.edit, name='control_new'),
        path('control/<str:section>/<int:pk>/', control.edit, name='control_edit'),
        path('control/<str:section>/<int:pk>/action/', control.action, name='control_action'),
        path('', include('words.urls')),
    ]
