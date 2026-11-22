# -*- coding: utf-8 -*-
"""全局模板上下文：隐私声明版本与本机同意状态。

数据只保存在本机 SQLite，上下文处理器不发起任何网络请求。
"""
from django.conf import settings


def privacy(request):
    agreed_version = ''
    agreed_hash = ''
    server_mode = getattr(settings, 'SERVER_MODE', False)
    account = getattr(request, 'login_account', None) if server_mode else None
    if account and account.privacy_agreed_version:
        agreed_version = account.privacy_agreed_version
        agreed_hash = account.privacy_agreed_hash
    elif account or not server_mode:
        try:
            from .models import UserSettings

            obj = UserSettings.objects.first()
            if obj:
                agreed_version = obj.privacy_agreed_version or ''
                agreed_hash = obj.privacy_agreed_hash or ''
        except Exception:
            # Older and desktop installations may render before the database is ready.
            pass

    current = getattr(settings, 'PRIVACY_VERSION', '')
    portal = {}
    if account:
        from accounts.auth import is_owner
        from accounts.models import Announcement

        portal = {'can_manage_site': is_owner(request.user),
                  'site_announcements': list(Announcement.objects.filter(published=True)[:50])}
    return {
        'server_mode': server_mode,
        'privacy_version': current,
        'privacy_agreed_version': agreed_version,
        'privacy_agreed_hash': agreed_hash,
        'privacy_agreed': bool(agreed_version) and agreed_version == current,
        **portal,
    }
