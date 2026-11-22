import secrets
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods, require_safe

from .auth import lock_current_account
from .models import Account, AccountEmailCode, EmailCode, MailLimit
from .registration import (
    VERIFICATION_TTL_MINUTES, notify_account_change, registration_ready, reserve_mail_capacity, send_verification,
)


OLD_EMAIL = AccountEmailCode.Purpose.OLD
NEW_EMAIL = AccountEmailCode.Purpose.NEW
CODE_LIFETIME = timedelta(minutes=VERIFICATION_TTL_MINUTES)
RESEND_INTERVAL = timedelta(seconds=60)
CODE_ERROR = '验证码错误、已失效或已达到尝试上限。请检查验证码或重新发送。'


class EmailCodeForm(forms.Form):
    code = forms.RegexField(r'^[0-9]{6}$', label='验证码', widget=forms.TextInput(attrs={
        'autocomplete': 'one-time-code', 'inputmode': 'numeric', 'maxlength': 6,
    }), error_messages={'invalid': '请输入 6 位数字验证码。'})


class NewEmailForm(EmailCodeForm):
    email = forms.EmailField(label='新邮箱', max_length=150, widget=forms.EmailInput(attrs={'autocomplete': 'email'}))

    def __init__(self, *args, sending=False, **kwargs):
        super().__init__(*args, **kwargs)
        if sending:
            self.fields['code'].required = False

    def clean_email(self):
        return self.cleaned_data['email'].lower()


class AccountPasswordForm(PasswordChangeForm):
    error_messages = {
        **PasswordChangeForm.error_messages,
        'password_incorrect': '当前密码不正确。',
        'password_mismatch': '两次输入的新密码不一致。',
    }

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields['old_password'].label = '当前密码'
        self.fields['new_password2'].label = '确认新密码'
        self.fields['old_password'].widget.attrs.pop('autofocus', None)


def email_scope(account):
    # Changes to the login, password or source address invalidate both verification steps.
    return salted_hmac('lexizx.account-email.scope',
                       f'{account.pk}:{account.login_id}:{account.user.password}:{account.email or ""}',
                       algorithm='sha256').hexdigest()


def account_code_digest(scope, purpose, email, code):
    return salted_hmac('lexizx.account-email.code', f'{scope}:{purpose}:{email}:{code}', algorithm='sha256').hexdigest()


def reserve_account_code(request, account, scope, purpose, email, now):
    MailLimit.objects.filter(until__lte=now).delete()
    AccountEmailCode.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
    recent = AccountEmailCode.objects.filter(account=account, purpose=purpose, sent_at__gt=now - RESEND_INTERVAL)
    recipient = salted_hmac('lexizx.account-email.recipient', email.lower(), algorithm='sha256').hexdigest()
    recipient_key = f'account-mail:{recipient}'
    if recent.exists() or MailLimit.objects.filter(pk=recipient_key, count__gte=1).exists():
        return None, '请在 60 秒后重发。'
    ip = request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR', '')
    limits = [(recipient_key, 1, RESEND_INTERVAL), (f'account:{account.pk}', 8, timedelta(hours=1))]
    if not reserve_mail_capacity(now, ip, limits):
        return None, '当前验证码发送次数已达上限，请稍后再试。'

    code = f'{secrets.randbelow(1000000):06d}'
    pending, _ = AccountEmailCode.objects.update_or_create(account=account, purpose=purpose, defaults={
        'email': email, 'scope': scope, 'digest': account_code_digest(scope, purpose, email, code),
        'sent_at': now, 'expires_at': now + CODE_LIFETIME, 'attempts': 0, 'verified': False,
    })
    return (pending, code), ''


def matches_code(pending, email, code):
    if not pending or pending.email != email or not pending.digest or pending.attempts >= 5:
        return False
    if constant_time_compare(pending.digest, account_code_digest(pending.scope, pending.purpose, email, code)):
        return True
    pending.attempts += 1
    pending.save(update_fields=['attempts'])
    return False


def active_codes(account, scope, now):
    return {pending.purpose: pending for pending in AccountEmailCode.objects.filter(
        account=account, scope=scope, expires_at__gt=now,
    )}


def save_verified_email(account, email):
    EmailCode.objects.filter(email__in=[email, account.email]).delete()
    AccountEmailCode.objects.filter(account=account).delete()
    account.email = account.user.email = email
    account.save(update_fields=['email'])
    account.user.save(update_fields=['email'])


@login_required
@require_safe
def profile(request):
    return render(request, 'account_profile.html', {'account': request.login_account})


@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('code')
def email_settings(request):
    account = request.login_account
    old_form = EmailCodeForm()
    new_form = NewEmailForm()
    unavailable = not registration_ready()
    send_error = ''
    now = timezone.now()
    queued = None
    action = request.POST.get('action')

    if request.method == 'POST' and not unavailable:
        with transaction.atomic():
            account = lock_current_account(request)
            now = timezone.now()
            scope = email_scope(account)
            pending = active_codes(account, scope, now)
            old_code = pending.get(OLD_EMAIL)
            needs_old = bool(account.email and not (old_code and old_code.verified))

            if needs_old and action == 'send_old':
                queued, send_error = reserve_account_code(request, account, scope, OLD_EMAIL, account.email, now)
            elif needs_old and action == 'verify_old':
                old_form = EmailCodeForm(request.POST)
                if old_form.is_valid():
                    if matches_code(old_code, account.email, old_form.cleaned_data['code']):
                        old_code.verified, old_code.digest, old_code.expires_at = True, '', now + CODE_LIFETIME
                        old_code.save(update_fields=['verified', 'digest', 'expires_at'])
                        AccountEmailCode.objects.filter(account=account, purpose=NEW_EMAIL).delete()
                        return redirect('account_email')
                    old_form.add_error('code', CODE_ERROR)
            elif needs_old and action in {'send_new', 'verify_new'}:
                send_error = '旧邮箱验证已失效，请重新验证旧邮箱。'
            elif not needs_old and action in {'send_new', 'verify_new'}:
                sending = action == 'send_new'
                new_form = NewEmailForm({'email': request.POST.get('email')} if sending else request.POST, sending=sending)
                if new_form.is_valid():
                    email = new_form.cleaned_data['email']
                    if account.email and email == account.email.lower():
                        new_form.add_error('email', '请输入与当前绑定邮箱不同的地址。')
                    elif Account.objects.filter(email__iexact=email).exists():
                        new_form.add_error('email', '该邮箱已被其他账号使用。')
                    elif sending:
                        queued, send_error = reserve_account_code(request, account, scope, NEW_EMAIL, email, now)
                    elif matches_code(pending.get(NEW_EMAIL), email, new_form.cleaned_data['code']):
                        old_email = account.email
                        save_verified_email(account, email)
                        notify_account_change(old_email, 'email')
                        messages.success(request, '邮箱换绑成功。' if old_email else '邮箱绑定成功。')
                        return redirect('account_profile')
                    else:
                        new_form.add_error('code', CODE_ERROR)

    if queued:
        pending_code, code = queued
        try:
            send_verification(pending_code.email, code, purpose=pending_code.purpose)
        except OSError:
            AccountEmailCode.objects.filter(pk=pending_code.pk, digest=pending_code.digest).update(digest='')
            send_error = '验证码发送失败，请稍后重试。'
        else:
            messages.success(request, '验证码已发送，请查收邮件。')
            return redirect('account_email')

    pending = active_codes(account, email_scope(account), now)
    old_code = pending.get(OLD_EMAIL)
    new_code = pending.get(NEW_EMAIL)
    old_verified = bool(account.email and old_code and old_code.verified)
    if new_code and not new_form.is_bound:
        new_form.initial['email'] = new_code.email
    return render(request, 'account_email.html', {
        'account': account, 'unavailable': unavailable, 'send_error': send_error,
        'old_form': old_form, 'new_form': new_form, 'old_verified': old_verified,
        'show_old_code': bool((old_code and old_code.digest) or old_form.is_bound),
        'show_new_code': bool((new_code and new_code.digest) or action == 'verify_new'),
        'new_code_email': new_code.email if new_code and new_code.digest else '',
        'code_minutes': VERIFICATION_TTL_MINUTES,
    }, status=503 if unavailable else 200)


@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('old_password', 'new_password1', 'new_password2')
def password_settings(request):
    form = AccountPasswordForm(request.user)
    if request.method == 'POST':
        user = None
        with transaction.atomic():
            account = lock_current_account(request)
            form = AccountPasswordForm(account.user, request.POST)
            if form.is_valid():
                user = form.save(commit=False)
                user.save(update_fields=['password'])
                AccountEmailCode.objects.filter(account=account).update(digest='', verified=False)
                if account.email:
                    EmailCode.objects.filter(email=account.email).update(digest='')
                notify_account_change(account.email, 'password')
        if user is not None:
            update_session_auth_hash(request, user)
            messages.success(request, '密码已修改。')
            return redirect('account_profile')
    return render(request, 'account_password.html', {'form': form})
