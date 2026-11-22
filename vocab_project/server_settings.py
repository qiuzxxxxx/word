"""Production settings for the multi-user deployment behind Nginx."""

import os
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured


def required_env(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'请设置 {name}')
    return value


# Require a server key before the desktop settings can fall back to a local file.
if len(required_env('DJANGO_SECRET_KEY')) < 50:
    raise ImproperlyConfigured('DJANGO_SECRET_KEY 至少需要 50 个字符')

from .settings import *  # noqa: E402,F403


SERVER_MODE = True
DEBUG = False
ALLOWED_HOSTS = [required_env('WORD_HOST')]
OWNER_USERNAME = 'zx'
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '').strip()
WORD_EMAIL_FROM = os.environ.get('WORD_EMAIL_FROM', '').strip()

try:
    SERVER_VAULT = Fernet(required_env('WORD_VAULT_KEY').encode('ascii'))
except ValueError as exc:
    raise ImproperlyConfigured('WORD_VAULT_KEY 必须是有效的 Fernet 密钥') from exc

DATA_DIR = Path(os.environ.get('WORD_DATA_DIR', '/var/lib/lexizx'))
BACKUP_DIR = DATA_DIR / 'backups'
STATIC_ROOT = Path(os.environ.get('WORD_STATIC_ROOT', '/var/lib/lexizx-static'))
STATICFILES_DIRS = []  # words/static is already discovered through INSTALLED_APPS.
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': DATA_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 15,
            'transaction_mode': 'IMMEDIATE',
            'init_command': 'PRAGMA journal_mode=WAL',
        },
    },
}

DATABASES['learning'] = {**DATABASES['default'], 'NAME': DATA_DIR / 'learner-template.sqlite3'}
DATABASE_ROUTERS = ['accounts.workspaces.LearningRouter']
INSTALLED_APPS = [*INSTALLED_APPS, 'accounts']
AUTHENTICATION_BACKENDS = ['accounts.auth.EmailBackend']
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
_auth_index = MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware')
MIDDLEWARE = [*MIDDLEWARE[:_auth_index + 1],
              'accounts.auth.ServerAccessMiddleware',
              'vocab_project.server_auth.PortalLoginRequired',
              *MIDDLEWARE[_auth_index + 1:]]
LOGIN_URL = 'owner_login'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = 'owner_login'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 86400
# HSTS applies to this hostname. Its child domains are outside this deployment.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_FAILURE_VIEW = 'vocab_project.server_auth.csrf_failure'
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
PRIVACY_VERSION = f'{PRIVACY_VERSION}-multi'
