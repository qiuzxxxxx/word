from django.conf import settings
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('words.urls')),
]

if getattr(settings, 'SERVER_MODE', False):
    from accounts import control, feedback, profile
    from accounts.auth import PortalLogin, PortalLogout, next_login_popup
    from accounts.registration import password_reset, register

    from .server_auth import health

    handler403 = 'vocab_project.server_auth.forbidden'
    handler404 = 'vocab_project.server_auth.not_found'
    handler500 = 'vocab_project.server_auth.server_error'
    urlpatterns = [
        path('login/', PortalLogin.as_view(), name='owner_login'),
        path('logout/', PortalLogout.as_view(), name='owner_logout'),
        path('register/', register, name='register'),
        path('password-reset/', password_reset, name='password_reset'),
        path('my/', profile.profile, name='account_profile'),
        path('my/email/', profile.email_settings, name='account_email'),
        path('my/password/', profile.password_settings, name='account_password'),
        path('my/feedback/', feedback.submit, name='account_feedback'),
        path('api/login-popups/next/', next_login_popup, name='next_login_popup'),
        path('healthz/', health, name='health'),
        path('control/', control.home, name='site_control'),
        path('control/feedback/', control.feedback_inbox, name='control_feedback'),
        path('control/feedback/<int:pk>/status/', control.feedback_status, name='control_feedback_status'),
        path('control/announcements/preview/', control.preview_announcement, name='announcement_preview'),
        path('control/<str:section>/', control.edit, name='control_new'),
        path('control/<str:section>/<int:pk>/', control.edit, name='control_edit'),
        path('control/model/<int:pk>/test/', control.test_shared_model, name='control_model_test'),
        path('control/<str:section>/<int:pk>/action/', control.action, name='control_action'),
        path('', include('words.urls')),
    ]
