#!/usr/bin/env bash
set -euo pipefail

cd /opt/lexizx
set -a
source /etc/lexizx.env
set +a
exec runuser -u lexizx -- /opt/lexizx-venv/bin/python manage.py "$@"
