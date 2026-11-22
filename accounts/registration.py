import json
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
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.http import require_http_methods

from .models import Account, EmailCode, MailLimit
from .usernames import AccountUsernameField, username_exists


VERIFICATION_TTL_MINUTES = 10


def registration_ready():
    return bool((settings.RESEND_API_KEY or getattr(settings, 'LOCAL_WEB', False)) and settings.WORD_EMAIL_FROM)


def code_digest(email, code):
    return salted_hmac('lexizx.verify-email.v1', f'{email}:{code}', algorithm='sha256').hexdigest()


def send_verification(email, code):
    sender = settings.WORD_EMAIL_FROM
    if '<' not in sender:
        sender = f'学习工具 <{sender}>'
    body = {
        'from': sender,
        'to': [email],
        'subject': '学习工具｜邮箱注册验证码',
        'text': (f'你的邮箱注册验证码是 {code}。\n\n'
                 f'请在 {VERIFICATION_TTL_MINUTES} 分钟内返回注册页面输入验证码。\n'
                 '如果你没有申请注册，可以忽略此邮件。'),
        'html': render_to_string('emails/verification.html', {
            'code': code, 'expires_minutes': VERIFICATION_TTL_MINUTES,
        }),
    }
    if getattr(settings, 'LOCAL_WEB', False):
        message = EmailMultiAlternatives(body['subject'], body['text'], sender, [email])
        message.attach_alternative(body['html'], 'text/html')
        if not message.send():
            raise OSError('Could not write the local verification email')
        print(f'本地验证码 {email}：{code}', flush=True)
        return
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
        ip_key = salted_hmac('lexizx.mail-ip', ip, algorithm='sha256').hexdigest()
        limits = [(f'ip:{ip_key}', 8, timedelta(hours=1)),
                  ('global', 85, timedelta(days=1))]
        rows = [(MailLimit.objects.get_or_create(key=key, defaults={'until': now + period})[0], cap)
                for key, cap, period in limits]
        if any(row.count >= cap for row, cap in rows):
            return None, '当前验证码发送次数已达上限，请稍后再试。'
        for row, _ in rows:
            row.count += 1
            row.save(update_fields=['count'])
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
    password = forms.CharField(label='密码', max_length=128, strip=False, widget=forms.PasswordInput(attrs={
        'autocomplete': 'new-password',
    }))
    password_confirm = forms.CharField(label='确认密码', max_length=128, strip=False, widget=forms.PasswordInput(attrs={
        'autocomplete': 'new-password',
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
                    Account.objects.create(user=user, email=email, privacy_agreed_at=now,
                                           privacy_agreed_version=data['privacy_version'])
                    pending.delete()
                    return redirect('owner_login')
    return render(request, 'register.html', {'form': form, 'notice': notice})
