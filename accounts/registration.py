import json
import logging
import secrets
from datetime import timedelta
from urllib.request import Request, urlopen

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.http import require_http_methods

from .models import Account, EmailCode, MailLimit
from .usernames import AccountUsernameField, username_exists


VERIFICATION_TTL_MINUTES = 10
logger = logging.getLogger(__name__)
VERIFICATION_EMAILS = {
    'register': ('邮箱注册', '请在 {deadline}返回注册页面输入验证码。', '注册'),
    'reset': ('重置密码', '请在 {deadline}返回页面，输入验证码并设置新密码。', '重置密码'),
    'old-email': ('旧邮箱验证', '请在 {deadline}返回「我的」，输入验证码以继续换绑邮箱。', '换绑邮箱'),
    'new-email': ('新邮箱验证', '请在 {deadline}返回「我的」，输入验证码以确认绑定。', '绑定或换绑邮箱'),
}
PASSWORD_LENGTH_ERRORS = {
    'min_length': '密码长度必须在 6 至 12 位之间。',
    'max_length': '密码长度必须在 6 至 12 位之间。',
}


def registration_ready():
    return bool((settings.RESEND_API_KEY or getattr(settings, 'LOCAL_WEB', False)) and settings.WORD_EMAIL_FROM)


def code_digest(email, code, purpose='register'):
    salt = 'lexizx.reset-password.v1' if purpose == 'reset' else 'lexizx.verify-email.v1'
    return salted_hmac(salt, f'{email}:{code}', algorithm='sha256').hexdigest()


def send_verification(email, code, purpose='register'):
    label, instructions, action = VERIFICATION_EMAILS[purpose]
    action_title = f'{label}验证码'
    subject = f'驻留集｜{action_title}'
    deadline = f'{VERIFICATION_TTL_MINUTES} 分钟内'
    ignore_text = f'如果你没有申请{action}，可以忽略此邮件。'
    text = f'你的{action_title}是 {code}。\n\n{instructions.format(deadline=deadline)}\n{ignore_text}'
    html_ctx = {
        'code': code,
        'expires_minutes': VERIFICATION_TTL_MINUTES,
        'title': subject,
        'action_title': action_title,
        'action_desc': instructions.format(deadline=f'<strong>{deadline}</strong>'),
        'ignore_text': ignore_text,
    }
    send_email(email, subject, text, html=render_to_string('emails/verification.html', html_ctx))
    if getattr(settings, 'LOCAL_WEB', False):
        prefix = f' [{label}]' if purpose != 'register' else ''
        print(f'本地验证码{prefix} {email}：{code}', flush=True)


def send_email(email, subject, text, *, html=None):
    sender = settings.WORD_EMAIL_FROM
    if '<' not in sender:
        sender = f'驻留集 <{sender}>'
    if getattr(settings, 'LOCAL_WEB', False):
        message = EmailMultiAlternatives(subject, text, sender, [email])
        if html:
            message.attach_alternative(html, 'text/html')
        if not message.send():
            raise OSError('Could not write the local email')
        return
    body = {'from': sender, 'to': [email], 'subject': subject, 'text': text}
    if html:
        body['html'] = html
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': f'Lexizx/{settings.APP_VERSION}',
        'Authorization': f'Bearer {settings.RESEND_API_KEY}',
    }
    request = Request('https://api.resend.com/emails', data=json.dumps(body).encode(), method='POST', headers=headers)
    with urlopen(request, timeout=8) as response:
        if response.status not in (200, 201):
            raise OSError('Email API rejected the request')


def notify_account_change(email, change):
    if not email or not registration_ready():
        return
    title = {'email': '绑定邮箱已更换', 'password': '登录密码已修改'}[change]
    text = f'你的驻留集账号{title}。\n\n如果这不是你本人操作，请尽快联系驻留集管理员。'

    def deliver():
        try:
            send_email(email, f'驻留集｜{title}', text)
        except OSError:
            logger.warning('Account change notification could not be delivered (%s)', change)

    transaction.on_commit(deliver, robust=True)


def reserve_mail_capacity(now, ip, extra_limits=()):
    ip_key = salted_hmac('lexizx.mail-ip', ip, algorithm='sha256').hexdigest()
    limits = [(f'ip:{ip_key}', 8, timedelta(hours=1)), ('global', 85, timedelta(days=1)), *extra_limits]
    rows = [(MailLimit.objects.get_or_create(key=key, defaults={'until': now + period})[0], cap)
            for key, cap, period in limits]
    if any(row.count >= cap for row, cap in rows):
        return False
    for row, _ in rows:
        row.count += 1
        row.save(update_fields=['count'])
    return True


def reserve_email_code(email, ip):
    now = timezone.now()
    expiry = now + timedelta(minutes=VERIFICATION_TTL_MINUTES)
    with transaction.atomic():
        MailLimit.objects.filter(until__lte=now).delete()
        EmailCode.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        if Account.objects.filter(email=email).exists():
            return None, ''
        previous = EmailCode.objects.filter(email=email).first()
        if previous and previous.sent_at > now - timedelta(seconds=60):
            return None, '请在 60 秒后重发。'
        if not reserve_mail_capacity(now, ip):
            return None, '当前验证码发送次数已达上限，请稍后再试。'
        code = f'{secrets.randbelow(1000000):06d}'
        EmailCode.objects.update_or_create(email=email, defaults={
            'digest': code_digest(email, code), 'expires_at': expiry, 'sent_at': now, 'attempts': 0,
        })
        return code, ''


class RegisterForm(forms.Form):
    email = forms.EmailField(label='邮箱', max_length=150, widget=forms.EmailInput(attrs={'autocomplete': 'email'}))
    code = forms.RegexField(r'^[0-9]{6}$', label='验证码', widget=forms.TextInput(attrs={
        'autocomplete': 'one-time-code', 'inputmode': 'numeric', 'maxlength': 6,
    }))
    username = AccountUsernameField()
    password = forms.CharField(label='密码', min_length=6, max_length=12, strip=False,
                               error_messages=PASSWORD_LENGTH_ERRORS,
                               widget=forms.PasswordInput(attrs={
                                   'autocomplete': 'new-password', 'minlength': 6, 'maxlength': 12,
                               }))
    password_confirm = forms.CharField(label='确认密码', min_length=6, max_length=12, strip=False,
                                       error_messages=PASSWORD_LENGTH_ERRORS,
                                       widget=forms.PasswordInput(attrs={
                                           'autocomplete': 'new-password', 'minlength': 6, 'maxlength': 12,
                                       }))
    agree_privacy = forms.BooleanField(label='我已阅读并同意《用户许可与使用协议》', error_messages={
        'required': '请阅读并同意用户声明后再注册。',
    })
    privacy_version = forms.CharField(max_length=20, widget=forms.HiddenInput, error_messages={
        'required': '请刷新页面，阅读当前版本的用户声明。',
    })

    def clean_email(self):
        return self.cleaned_data['email'].lower()

    def clean_privacy_version(self):
        value = self.cleaned_data['privacy_version']
        if value != settings.PRIVACY_VERSION:
            raise ValidationError('用户声明已更新，请刷新页面并重新阅读。')
        return value

    def clean(self):
        values = super().clean()
        if values.get('password') and values.get('password_confirm') and values['password'] != values['password_confirm']:
            self.add_error('password_confirm', '两次输入的密码不一致。')
        if values.get('password') and values.get('email') and values.get('username'):
            try:
                validate_password(values['password'], get_user_model()(username=values['username'], email=values['email']))
            except ValidationError as error:
                self.add_error('password', error)
        return values


@login_not_required
@require_http_methods(['GET', 'POST'])
def register(request):
    if not registration_ready():
        return render(request, 'register.html', {'unavailable': True}, status=503)
    if request.user.is_authenticated:
        return redirect('/')
    form = RegisterForm(initial={'privacy_version': settings.PRIVACY_VERSION})
    notice = ''
    if request.method == 'POST' and request.POST.get('action') == 'send':
        form.initial['email'] = request.POST.get('email', '')[:150]
        form.initial['username'] = request.POST.get('username', '')[:150]
        form.initial['agree_privacy'] = (
            request.POST.get('agree_privacy') == 'on' and request.POST.get('privacy_version') == settings.PRIVACY_VERSION
        )
        try:
            email = forms.EmailField(max_length=150).clean(request.POST.get('email')).lower()
        except ValidationError as error:
            notice = error.messages[0]
        else:
            form.initial['email'] = email
            # Nginx overwrites X-Forwarded-For from the connection address.
            ip = request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR', '')
            code, notice = reserve_email_code(email, ip)
            if code:
                try:
                    send_verification(email, code)
                except OSError:
                    EmailCode.objects.filter(email=email, digest=code_digest(email, code)).update(digest='')
                    notice = '验证码发送失败，请稍后重试。'
            notice = notice or '如果此地址可以注册，验证码将发送到该邮箱。'
    elif request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            email = data['email']
            with transaction.atomic():
                now = timezone.now()
                pending = EmailCode.objects.filter(email=email, expires_at__gt=now, attempts__lt=5).first()
                if not pending or not constant_time_compare(pending.digest, code_digest(email, data['code'])):
                    if pending:
                        pending.attempts += 1
                        pending.save(update_fields=['attempts'])
                    form.add_error('code', '验证码错误、已失效或已达到尝试上限。')
                elif Account.objects.filter(email=email).exists():
                    form.add_error('email', '该邮箱已注册。')
                elif username_exists(data['username']):
                    form.add_error('username', '该用户名已被使用，请换一个。')
                else:
                    user = get_user_model().objects.create_user(
                        username=data['username'], email=email, password=data['password'],
                    )
                    Account.objects.create(
                        user=user, email=email, privacy_agreed_at=now, privacy_agreed_version=data['privacy_version'],
                        welcome_step=Account.WelcomeStep.AGREEMENT,
                    )
                    pending.delete()
                    return redirect('owner_login')
    return render(request, 'register.html', {'form': form, 'notice': notice})


def reserve_password_reset_code(email, ip):
    now = timezone.now()
    expiry = now + timedelta(minutes=VERIFICATION_TTL_MINUTES)
    with transaction.atomic():
        MailLimit.objects.filter(until__lte=now).delete()
        EmailCode.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()
        if not Account.objects.filter(email=email, deleted_at__isnull=True).exists():
            return None, ''
        previous = EmailCode.objects.filter(email=email).first()
        if previous and previous.sent_at > now - timedelta(seconds=60):
            return None, '请在 60 秒后重发。'
        if not reserve_mail_capacity(now, ip):
            return None, '当前验证码发送次数已达上限，请稍后再试。'
        code = f'{secrets.randbelow(1000000):06d}'
        EmailCode.objects.update_or_create(email=email, defaults={
            'digest': code_digest(email, code, purpose='reset'), 'expires_at': expiry, 'sent_at': now, 'attempts': 0,
        })
        return code, ''


class PasswordResetForm(forms.Form):
    email = forms.EmailField(label='邮箱', max_length=150, widget=forms.EmailInput(attrs={'autocomplete': 'email'}))
    code = forms.RegexField(r'^[0-9]{6}$', label='验证码', widget=forms.TextInput(attrs={
        'autocomplete': 'one-time-code', 'inputmode': 'numeric', 'maxlength': 6,
    }))
    new_password = forms.CharField(label='新密码', min_length=6, max_length=12, strip=False,
                                   error_messages=PASSWORD_LENGTH_ERRORS,
                                   widget=forms.PasswordInput(attrs={
                                       'autocomplete': 'new-password', 'minlength': 6, 'maxlength': 12,
                                   }))
    password_confirm = forms.CharField(label='确认新密码', min_length=6, max_length=12, strip=False,
                                       error_messages=PASSWORD_LENGTH_ERRORS,
                                       widget=forms.PasswordInput(attrs={
                                           'autocomplete': 'new-password', 'minlength': 6, 'maxlength': 12,
                                       }))

    def clean_email(self):
        return self.cleaned_data['email'].lower()

    def clean(self):
        values = super().clean()
        if values.get('new_password') and values.get('password_confirm') and values['new_password'] != values['password_confirm']:
            self.add_error('password_confirm', '两次输入的新密码不一致。')
        if values.get('new_password') and values.get('email'):
            account = Account.objects.filter(email=values['email'], deleted_at__isnull=True).first()
            user = account.user if account else get_user_model()(email=values['email'])
            try:
                validate_password(values['new_password'], user)
            except ValidationError as error:
                self.add_error('new_password', error)
        return values


@login_not_required
@require_http_methods(['GET', 'POST'])
def password_reset(request):
    if not registration_ready():
        return render(request, 'password_reset.html', {'unavailable': True}, status=503)
    initial = {}
    if request.user.is_authenticated and getattr(request.user, 'email', None):
        initial['email'] = request.user.email
    form = PasswordResetForm(initial=initial)
    notice = ''
    if request.method == 'POST' and request.POST.get('action') == 'send':
        form.initial['email'] = request.POST.get('email', '')[:150]
        try:
            email = forms.EmailField(max_length=150).clean(request.POST.get('email')).lower()
        except ValidationError as error:
            notice = error.messages[0]
        else:
            form.initial['email'] = email
            ip = request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR', '')
            code, notice = reserve_password_reset_code(email, ip)
            if code:
                try:
                    send_verification(email, code, purpose='reset')
                except OSError:
                    EmailCode.objects.filter(email=email, digest=code_digest(email, code, purpose='reset')).update(digest='')
                    notice = '验证码发送失败，请稍后重试。'
            notice = notice or '如果此邮箱已注册，验证码将发送到该邮箱。'
    elif request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            email = data['email']
            with transaction.atomic():
                now = timezone.now()
                pending = EmailCode.objects.filter(email=email, expires_at__gt=now, attempts__lt=5).first()
                if not pending or not constant_time_compare(pending.digest, code_digest(email, data['code'], purpose='reset')):
                    if pending:
                        pending.attempts += 1
                        pending.save(update_fields=['attempts'])
                    form.add_error('code', '验证码错误、已失效或已达到尝试上限。')
                else:
                    account = Account.objects.filter(email=email, deleted_at__isnull=True).select_related('user').first()
                    if not account:
                        form.add_error('email', '该邮箱尚未注册或账号已注销。')
                    else:
                        user = account.user
                        user.set_password(data['new_password'])
                        user.save(update_fields=['password'])
                        account.login_id = ''
                        account.save(update_fields=['login_id'])
                        pending.delete()
                        notify_account_change(account.email, 'password')
                        return redirect(f"{reverse('owner_login')}?reset_success=1")
    return render(request, 'password_reset.html', {'form': form, 'notice': notice})

