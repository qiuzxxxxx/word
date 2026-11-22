import http.client
import io
import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, build_opener

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from words.secret_vault import decrypt

from .models import SharedModel, SharedUsage
from .workspaces import learner_id, learning_db


def https_endpoint(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme == 'https' and parsed.hostname and parsed.port in (None, 443) and not (
            parsed.username is not None or parsed.password is not None or parsed.fragment or '\\' in url
            or any(ord(c) < 33 for c in url)
        ):
            return parsed
    except (TypeError, ValueError):
        pass
    raise ValueError('AI 接口需要使用公开的 HTTPS 地址和 443 端口。')


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = self.connect_public

    @staticmethod
    def connect_public(address, timeout, source_address=None):
        host, port = address
        last_error = None
        for family, socktype, proto, _, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.version == 6:
                ip = ip.ipv4_mapped or ip
                if ip.version == 6 and (ip.sixtofour or ip.teredo or ip in ipaddress.ip_network('64:ff9b::/96')):
                    continue
            if not ip.is_global or ip.is_reserved or ip.is_multicast or ip.is_loopback:
                continue
            connection = socket.socket(family, socktype, proto)
            try:
                connection.settimeout(timeout)
                connection.connect(sockaddr)
                return connection
            except OSError as error:
                connection.close()
                last_error = error
        raise last_error or OSError('AI 接口未解析到允许访问的公网地址。')


class PublicHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PublicHTTPSConnection, request, context=self._context)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def sync_shared_models():
    from words.models import AIModel

    shared = {model.pk: model for model in SharedModel.objects.defer('encrypted_key')}
    local = {model.shared_model_id: model for model in AIModel.objects.exclude(shared_model_id=None)}
    removed = set(local) - set(shared)
    updates = {}
    for pk, model in shared.items():
        values = {'display_name': f'公共 · {model.name}'[:100], 'model_id': model.model_id,
                  'enabled': model.enabled, 'vision': model.vision}
        if pk not in local or any(getattr(local[pk], key) != value for key, value in values.items()):
            updates[pk] = values
    if removed or updates:
        with transaction.atomic(using=learning_db()):
            if removed:
                AIModel.objects.filter(shared_model_id__in=removed).delete()
            for pk, values in updates.items():
                AIModel.objects.update_or_create(shared_model_id=pk, defaults={**values, 'provider': 'custom'})


def public_endpoint(model):
    endpoint = SharedModel.objects.filter(pk=model.shared_model_id, enabled=True).values_list('endpoint', flat=True).first()
    if not endpoint:
        raise ValueError('该公共模型已停用，请重新选择。')
    return endpoint


def reserve_shared_use(shared_id, url):
    user_id = learner_id()
    day = timezone.localdate()
    with transaction.atomic():
        shared = SharedModel.objects.filter(pk=shared_id, endpoint=url, enabled=True).first()
        if not shared or not get_user_model().objects.filter(pk=user_id, is_active=True).exists():
            raise URLError('公共模型不可用，请刷新后重试。')
        usage, _ = SharedUsage.objects.get_or_create(model=shared, user_id=user_id, day=day)
        total = SharedUsage.objects.filter(model=shared, day=day).aggregate(total=Sum('count'))['total'] or 0
        if usage.count >= shared.per_user_daily or total >= shared.daily_total:
            raise HTTPError(url, 429, 'rate limit', {}, io.BytesIO('公共模型的今日额度已用完。'.encode()))
        usage.count += 1
        usage.save(update_fields=['count'])
        return decrypt(shared.encrypted_key)


def open_ai_request(request, timeout, cfg):
    try:
        https_endpoint(request.full_url)
    except ValueError as error:
        raise URLError(str(error)) from error
    shared_id = cfg.get('shared_model_id')
    if shared_id:
        request.add_header('Authorization', f'Bearer {reserve_shared_use(shared_id, request.full_url)}')
    try:
        return build_opener(ProxyHandler({}), PublicHTTPSHandler(), NoRedirects()).open(request, timeout=timeout)
    except HTTPError as error:
        if not shared_id:
            raise
        error.close()
        raise HTTPError(request.full_url, error.code, 'upstream error', {}, io.BytesIO()) from None
