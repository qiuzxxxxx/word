"""Use the production web app with isolated data and a file-based email outbox."""

import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


_default_dir = Path(__file__).resolve().parent.parent / 'dist' / 'local-web'
_data_dir = Path(os.environ.get('LEXIZX_LOCAL_WEB_DIR', str(_default_dir))).resolve()
_data_dir.mkdir(parents=True, exist_ok=True)
_secrets_path = _data_dir / '.web-secrets.json'
if not _secrets_path.exists():
    try:
        _fd = os.open(_secrets_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(_fd, 'w', encoding='utf-8') as _handle:
            json.dump({'DJANGO_SECRET_KEY': secrets.token_urlsafe(64),
                       'WORD_VAULT_KEY': Fernet.generate_key().decode('ascii')}, _handle)

os.environ.update(json.loads(_secrets_path.read_text(encoding='utf-8')))
os.environ.update(WORD_HOST='127.0.0.1', WORD_DATA_DIR=str(_data_dir), WORD_STATIC_ROOT=str(_data_dir / 'static'),
                  RESEND_API_KEY='', WORD_EMAIL_FROM='no-reply@example.test')

from .server_settings import *  # noqa: E402,F403


ALLOWED_HOSTS = ['127.0.0.1', 'localhost', '[::1]']
SECURE_PROXY_SSL_HEADER = None
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
LOCAL_WEB = True
EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
EMAIL_FILE_PATH = str(_data_dir / 'mail')
