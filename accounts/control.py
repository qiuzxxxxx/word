from urllib.request import ProxyHandler, build_opener

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from words.secret_vault import encrypt

from .ai_gateway import https_endpoint
from .announcement_content import render_announcement
from .auth import is_owner
from .models import Account, Announcement, EmailCode, SharedModel, SiteState, SupportMessage
from .usernames import AccountUsernameField, username_exists


class AccountForm(forms.Form):
    username = AccountUsernameField()
    email = forms.EmailField(label='邮箱', max_length=150, required=False)
    password = forms.CharField(label='新密码', required=False, min_length=6, max_length=12,
                               error_messages={'min_length': '密码长度必须在 6 至 12 位之间。',
                                               'max_length': '密码长度必须在 6 至 12 位之间。'},
                               widget=forms.PasswordInput(attrs={'autocomplete': 'new-password', 'minlength': 6, 'maxlength': 12}),
                               help_text='新建时必填（6-12位）。编辑时留空保留原密码。')

    def __init__(self, *args, target=None, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)
        if not target:
            self.fields['password'].required = True

    def clean_username(self):
        value = self.cleaned_data['username']
        if username_exists(value, exclude_id=self.target.pk if self.target else None):
            raise ValidationError('该用户名不可用。')
        return value

    def clean_email(self):
        value = self.cleaned_data['email'].lower()
        if value and Account.objects.filter(email=value).exclude(pk=self.target.pk if self.target else None).exists():
            raise ValidationError('该邮箱已被使用。')
        return value

    def clean(self):
        values = super().clean()
        if values.get('password'):
            candidate = get_user_model()(username=values.get('username', ''), email=values.get('email', ''))
            try:
                validate_password(values['password'], candidate)
            except ValidationError as error:
                self.add_error('password', error)
        return values

    def save(self):
        data = self.cleaned_data
        user = self.target or get_user_model()()
        user.username, user.email = data['username'], data['email']
        if data['password']:
            user.set_password(data['password'])
        user.save()
        account, _ = Account.objects.get_or_create(user=user)
        if account.email != (user.email or None) or data['password']:
            account.email = user.email or None
            account.login_id = ''
            account.save(update_fields=['email', 'login_id'])
        if user.email:
            EmailCode.objects.filter(email=user.email).delete()


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title', 'body', 'published']
        labels = {'title': '公告标题', 'body': '公告正文', 'published': '立即发布'}
        widgets = {'body': forms.Textarea(attrs={'rows': 12, 'aria-describedby': 'announcement-hint'})}


class SiteForm(forms.ModelForm):
    class Meta:
        model = SiteState
        fields = ['maintenance', 'message']
        labels = {'maintenance': '开启维护', 'message': '维护说明'}


class SharedModelForm(forms.ModelForm):
    secret = forms.CharField(label='API 密钥', required=False, max_length=2000,
                             widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
                             help_text='初次添加必填，编辑留空保留原密钥。')

    class Meta:
        model = SharedModel
        fields = ['name', 'model_id', 'endpoint', 'vision', 'enabled', 'per_user_daily', 'daily_total']
        labels = {'name': '显示名称', 'model_id': '模型 ID', 'endpoint': '完整 HTTPS 接口地址',
                  'vision': '支持图片', 'enabled': '对用户开放', 'per_user_daily': '单人每日次数',
                  'daily_total': '全站每日次数'}

    def clean_endpoint(self):
        endpoint = self.cleaned_data['endpoint']
        try:
            https_endpoint(endpoint)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        return endpoint

    def clean(self):
        values = super().clean()
        if not self.instance.pk and not values.get('secret'):
            self.add_error('secret', '请提供公共模型 API 密钥。')
        per_user, total = values.get('per_user_daily'), values.get('daily_total')
        if per_user is not None and total is not None and not 1 <= per_user <= total <= 100000:
            self.add_error('daily_total', '额度须满足 1 ≤ 单人每日次数 ≤ 全站每日次数 ≤ 100000。')
        return values

    def save(self, commit=True):
        if self.cleaned_data['secret']:
            self.instance.encrypted_key = encrypt(self.cleaned_data['secret'])
        return super().save(commit=commit)


def ordinary_accounts(request):
    return Account.objects.select_related('user').filter(deleted_at__isnull=True).exclude(user_id=request.user.pk)


@require_http_methods(['GET'])
def home(request):
    if not is_owner(request.user):
        raise PermissionDenied
    query = request.GET.get('q', '').strip()[:150]
    accounts = ordinary_accounts(request)
    if query:
        accounts = accounts.filter(Q(user__username__icontains=query) | Q(email__icontains=query))
    default_page_size = 20
    page_size_options = [10, 20, 50, 100]
    try:
        page_size = int(request.GET.get('page_size', default_page_size))
        if page_size not in page_size_options:
            page_size = default_page_size
    except (ValueError, TypeError):
        page_size = default_page_size

    paginator = Paginator(accounts.order_by('-pk'), page_size)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    elided_pages = list(paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1)) if paginator.num_pages > 1 else []

    return render(request, 'control_home.html', {
        'accounts': page_obj,
        'page_size': page_size,
        'page_size_options': page_size_options,
        'elided_pages': elided_pages,
        'query': query,
        'announcements': Announcement.objects.all(),
        'pending_feedback': SupportMessage.objects.filter(resolved_at__isnull=True).count(),
        'shared_models': SharedModel.objects.order_by('name', 'pk'),
        'site': request.site_state,
    })


@require_http_methods(['GET'])
def feedback_inbox(request):
    if not is_owner(request.user):
        raise PermissionDenied
    show_resolved = request.GET.get('status') == 'resolved'
    feedback = SupportMessage.objects.filter(resolved_at__isnull=not show_resolved).select_related('account__user')
    page = Paginator(feedback, 20).get_page(request.GET.get('page'))
    return render(request, 'control_feedback.html', {'feedback': page, 'show_resolved': show_resolved})


@require_http_methods(['POST'])
def feedback_status(request, pk):
    if not is_owner(request.user):
        raise PermissionDenied
    operation = request.POST.get('operation')
    if operation not in {'resolve', 'reopen'}:
        raise Http404
    with transaction.atomic():
        entry = get_object_or_404(SupportMessage.objects.select_for_update(), pk=pk)
        if operation == 'resolve' and entry.resolved_at is None:
            entry.resolved_at = timezone.now()
            entry.save(update_fields=['resolved_at'])
        elif operation == 'reopen' and entry.resolved_at is not None:
            entry.resolved_at = None
            entry.save(update_fields=['resolved_at'])
    inbox = reverse('control_feedback')
    return redirect(f'{inbox}?status=resolved' if operation == 'reopen' else inbox)


@require_http_methods(['POST'])
def preview_announcement(request):
    if not is_owner(request.user):
        raise PermissionDenied
    body = request.POST.get('body', '')
    if len(body) > Announcement._meta.get_field('body').max_length:
        return JsonResponse({'error': '公告正文最多 3000 个字符。'}, status=400)
    return JsonResponse({'html': render_announcement(body)})


@require_http_methods(['GET', 'POST'])
def edit(request, section, pk=None):
    if not is_owner(request.user):
        raise PermissionDenied
    with transaction.atomic():
        submitted = request.POST if request.method == 'POST' else None
        if section == 'users':
            account = get_object_or_404(ordinary_accounts(request), pk=pk) if pk is not None else None
            target = account.user if account else None
            form = AccountForm(submitted, target=target, initial={
                'username': target.username if target else '', 'email': account.email if account else '',
            })
            title = '编辑用户' if target else '创建用户'
        elif section in ('announcement', 'model'):
            model, form_type, title = (Announcement, AnnouncementForm, '发布或修改公告') if section == 'announcement' else (
                SharedModel, SharedModelForm, '配置公共 AI 模型',
            )
            instance = get_object_or_404(model, pk=pk) if pk is not None else None
            form = form_type(submitted, instance=instance)
        elif section == 'site' and pk is None:
            form = SiteForm(submitted, instance=SiteState.objects.get(pk=1))
            title = '网站维护'
        else:
            raise Http404
        if submitted is not None and form.is_valid():
            form.save()
            messages.success(request, '已保存。')
            return redirect('site_control')
    return render(request, 'control_edit.html', {'form': form, 'title': title, 'section': section})


@require_http_methods(['POST'])
def action(request, section, pk):
    if not is_owner(request.user):
        raise PermissionDenied
    operation = request.POST.get('operation')
    with transaction.atomic():
        if section == 'users' and operation in {'ban', 'unban', 'delete'}:
            account = get_object_or_404(ordinary_accounts(request), pk=pk)
            account.login_id = ''
            if operation == 'delete':
                account.deleted_at = timezone.now()
            account.save(update_fields=['login_id', 'deleted_at'])
            account.user.is_active = operation == 'unban'
            account.user.save(update_fields=['is_active'])
        elif section in {'announcement', 'model'} and operation == 'delete':
            model = Announcement if section == 'announcement' else SharedModel
            get_object_or_404(model, pk=pk).delete()
        else:
            raise Http404
    messages.success(request, {'ban': '账号已封禁。', 'unban': '账号已解封。', 'delete': '已删除。'}[operation])
    return redirect('site_control')


@require_http_methods(['POST'])
def test_shared_model(request, pk):
    if not is_owner(request.user):
        raise PermissionDenied
    import json
    import urllib.request
    from words.secret_vault import decrypt
    from words.views import build_ai_headers, _ai_first_choice, _ai_parse_json_body, _AIRequestError
    from .ai_gateway import PublicHTTPSHandler, NoRedirects

    shared = get_object_or_404(SharedModel, pk=pk)
    try:
        api_key = decrypt(shared.encrypted_key)
    except Exception as e:
        return JsonResponse({'error': f'密钥解密失败: {e}'}, status=400)

    try:
        https_endpoint(shared.endpoint)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    payload = {
        'model': shared.model_id,
        'max_tokens': 1,
        'messages': [{'role': 'user', 'content': 'ping'}],
    }
    req = urllib.request.Request(
        shared.endpoint,
        data=json.dumps(payload).encode('utf-8'),
        headers=build_ai_headers(api_key),
        method='POST',
    )
    try:
        opener = build_opener(ProxyHandler({}), PublicHTTPSHandler(), NoRedirects())
        with opener.open(req, timeout=15) as resp:
            raw = resp.read(5 * 1024 * 1024 + 1)
        _ai_first_choice(_ai_parse_json_body(raw))
        return JsonResponse({'success': True, 'message': '连接成功，密钥有效'})
    except urllib.error.HTTPError as e:
        body = e.read(4096).decode('utf-8', 'ignore')
        return JsonResponse({'error': f'连接失败 (HTTP {e.code}): {body[:200]}'}, status=502)
    except Exception as e:
        return JsonResponse({'error': f'连接失败: {e}'}, status=502)

