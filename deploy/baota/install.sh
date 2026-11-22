#!/usr/bin/env bash
set -euo pipefail

cd /opt/lexizx
umask 022

if [[ $EUID != 0 ]]; then
    echo '请通过宝塔终端或 SSH 使用 root 执行。' >&2
    exit 1
fi
if systemctl is-active --quiet lexizx; then
    echo '服务正在运行。更新前请按部署说明备份并停止服务。' >&2
    exit 1
fi

domain=${1:-}
owner=${2:-zx}
if [[ $owner != zx ]]; then
    echo '站点最高管理员账号固定为 zx。' >&2
    exit 1
fi
if [[ ! -f /etc/lexizx.env ]]; then
    if [[ ! $domain =~ ^[a-z0-9][a-z0-9.-]*\.[a-z0-9]+$ ]]; then
        echo '首次安装用法：bash deploy/baota/install.sh learn.example.com zx' >&2
        exit 1
    fi
fi

if ! id lexizx >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/lexizx --shell /usr/sbin/nologin lexizx
fi
install -d -m 0750 -o lexizx -g lexizx /var/lib/lexizx
install -d -m 0755 -o lexizx -g lexizx /var/lib/lexizx-static

python3 -m venv /opt/lexizx-venv
/opt/lexizx-venv/bin/python -m pip install --disable-pip-version-check -r deploy/baota/requirements.txt

if [[ ! -f /etc/lexizx.env ]]; then
    env_tmp=$(mktemp /etc/lexizx.env.XXXXXX)
    trap 'rm -f -- "$env_tmp"' EXIT
    /opt/lexizx-venv/bin/python - "$domain" "$owner" > "$env_tmp" <<'PY'
import secrets
import sys

from cryptography.fernet import Fernet

print('DJANGO_SETTINGS_MODULE=vocab_project.server_settings')
print(f'DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}')
print(f'WORD_VAULT_KEY={Fernet.generate_key().decode("ascii")}')
print(f'WORD_HOST={sys.argv[1]}')
print(f'WORD_OWNER={sys.argv[2]}')
print('WORD_DATA_DIR=/var/lib/lexizx')
print('WORD_STATIC_ROOT=/var/lib/lexizx-static')
PY
    mv -- "$env_tmp" /etc/lexizx.env
    trap - EXIT
fi

source /etc/lexizx.env
if [[ -n $domain && $domain != "$WORD_HOST" ]]; then
    echo '传入域名与 /etc/lexizx.env 中的配置不同，请检查现有配置。' >&2
    exit 1
fi

manage=(bash deploy/baota/manage.sh)
"${manage[@]}" check --deploy --fail-level WARNING
"${manage[@]}" migrate --noinput
# Release archives can preserve old timestamps, so regenerate the published assets on upgrade.
"${manage[@]}" collectstatic --noinput --clear
"${manage[@]}" shell -c '
from django.core.management import call_command
from django.db import transaction
from words.models import UserSettings, Word

with transaction.atomic():
    if not UserSettings.objects.exists() and not Word.objects.exists():
        call_command("import_words", "data/hongbaoshu.json", skip_ai=True)
    UserSettings.get_settings()
'

has_owner=$("${manage[@]}" shell -c '
from django.conf import settings
from django.contrib.auth import get_user_model

print(int(get_user_model().objects.filter(username=settings.OWNER_USERNAME).exists()))
' | tail -n 1)
if [[ $has_owner == 0 ]]; then
    "${manage[@]}" createsuperuser --username "$owner" --email ''
fi
"${manage[@]}" prepare_learning

install -m 0644 deploy/baota/lexizx.service /etc/systemd/system/lexizx.service
systemctl daemon-reload
systemctl enable lexizx
systemctl restart lexizx

curl --fail --silent --show-error --retry 8 --retry-connrefused --retry-delay 1 --max-time 5 \
    -H "Host: $WORD_HOST" -H 'X-Forwarded-Proto: https' http://127.0.0.1:8765/healthz/
echo "后端已启动，请在宝塔完成 $WORD_HOST 的 Nginx 和 SSL 配置。"
