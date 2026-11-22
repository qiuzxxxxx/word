import secrets
from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.http import require_POST

from .models import Account, Announcement, SiteState
from .workspaces import user_workspace


COOKIE = 'lexizx_access'
AUDIENCE = 'lexizx-web'


def is_owner(user):
    return user.is_authenticated and user.is_superuser and user.username == settings.OWNER_USERNAME


def site_state():
    return SiteState.objects.filter(pk=1).first()


def jwt_key():
    return salted_hmac('lexizx.jwt.v1', AUDIENCE, algorithm='sha256').digest()


def valid_login(request, account):
    if not account or not account.login_id or account.deleted_at:
        return False
    try:
        claims = jwt.decode(
            request.COOKIES.get(COOKIE, ''), jwt_key(), algorithms=['HS256'],
            audience=AUDIENCE, issuer=AUDIENCE,
            options={'require': ['exp', 'iat', 'sub', 'jti', 'iss', 'aud']},
        )
    except jwt.InvalidTokenError:
        return False
    sub_matches = claims.get('sub') in (str(account.pk), str(account.user_id))
    return sub_matches and constant_time_compare(claims.get('jti', ''), account.login_id)


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username and '@' in username:
            username = Account.objects.filter(email=username.strip().lower(), deleted_at__isnull=True).values_list(
                'user__username', flat=True,
            ).first() or ''
        return super().authenticate(request, username=username, password=password, **kwargs)


class PortalLogin(LoginView):
    template_name = 'owner_login.html'

    def get_context_data(self, **kwargs):
        from .registration import registration_ready

        return super().get_context_data(
            signup_available=registration_ready(), site_state=self.request.site_state, **kwargs,
        )

    def form_valid(self, form):
        user = form.get_user()
        if self.request.site_state and self.request.site_state.maintenance and not is_owner(user):
            form.add_error(None, '网站维护中，请稍后再登录。')
            return self.render_to_response(self.get_context_data(form=form), status=503)
        account, _ = Account.objects.get_or_create(user=user)
        login_id = secrets.token_urlsafe(32)
        updated = Account.objects.filter(
            pk=account.pk, deleted_at__isnull=True, user__is_active=True, user__password=user.password,
        ).update(login_id=login_id, announcement_pending=True)
        if not updated:
            form.add_error(None, '该账号无法登录。')
            return self.form_invalid(form)
        now = timezone.now()
        valid_for = getattr(settings, 'SESSION_COOKIE_AGE', 60 * 60 * 24 * 90)
        token = jwt.encode(
            {'sub': str(account.pk), 'user_id': str(user.pk), 'jti': login_id, 'iat': now,
             'exp': now + timedelta(seconds=valid_for),
             'iss': AUDIENCE, 'aud': AUDIENCE},
            jwt_key(), algorithm='HS256',
        )
        if hasattr(self.request, 'session'):
            self.request.session.set_expiry(valid_for)
        response = super().form_valid(form)
        is_secure = settings.SESSION_COOKIE_SECURE and (self.request.is_secure() or not getattr(settings, 'DEBUG', False))
        samesite = getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax')
        response.set_cookie(COOKIE, token, max_age=valid_for, secure=is_secure,
                            httponly=True, samesite=samesite)
        return response


class PortalLogout(LogoutView):
    def post(self, request, *args, **kwargs):
        if request.login_account:
            Account.objects.filter(pk=request.login_account.pk, login_id=request.login_account.login_id).update(login_id='')
        response = super().post(request, *args, **kwargs)
        samesite = getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax')
        response.delete_cookie(COOKIE, samesite=samesite)
        return response


def lock_current_account(request):
    account = Account.objects.select_for_update().select_related('user').filter(
        pk=request.user.pk, login_id=request.login_account.login_id, deleted_at__isnull=True,
        user__is_active=True, user__password=request.user.password,
    ).first()
    if account is None:
        raise PermissionDenied
    return account


@login_required
@require_POST
def next_login_popup(request):
    with transaction.atomic():
        account = lock_current_account(request)
        popup = account.welcome_step
        if popup:
            account.welcome_step = (
                Account.WelcomeStep.GUIDE if popup == Account.WelcomeStep.AGREEMENT else Account.WelcomeStep.DONE
            )
            account.save(update_fields=['welcome_step'])
        elif account.announcement_pending:
            account.announcement_pending = False
            account.save(update_fields=['announcement_pending'])
            if Announcement.objects.filter(published=True).exists():
                popup = 'announcement'
    return JsonResponse({'popup': popup})


class ServerAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.login_account = None
        request.site_state = site_state() if request.path_info != '/healthz/' else None
        had_cookie = COOKIE in request.COOKIES
        if request.user.is_authenticated:
            account = Account.objects.filter(user=request.user).first()
            if valid_login(request, account):
                request.login_account = account
            else:
                logout(request)

        maintenance = request.site_state and request.site_state.maintenance
        access_routes = ('/login/', '/logout/', '/healthz/')
        if maintenance and not is_owner(request.user) and request.path_info not in access_routes:
            if request.path_info.startswith('/api/'):
                response = JsonResponse({'error': '网站维护中，请稍后再试'}, status=503)
            else:
                response = render(request, 'maintenance.html', {'site': request.site_state}, status=503)
            response['Retry-After'] = '300'
        elif request.login_account and not request.path_info.startswith('/control/') and request.path_info not in access_routes:
            with user_workspace(request.user.pk, is_owner(request.user)):
                from .ai_gateway import sync_shared_models

                sync_shared_models()
                response = self.get_response(request)
        else:
            response = self.get_response(request)

        if had_cookie and not request.login_account and COOKIE not in response.cookies:
            samesite = getattr(settings, 'SESSION_COOKIE_SAMESITE', 'Lax')
            response.delete_cookie(COOKIE, samesite=samesite)
        add_never_cache_headers(response)
        return response
