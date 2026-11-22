from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .auth import lock_current_account
from .models import SupportMessage


class SupportMessageForm(forms.ModelForm):
    category = forms.ChoiceField(label='留言类型', choices=SupportMessage.Category.choices, widget=forms.RadioSelect)

    class Meta:
        model = SupportMessage
        fields = ['category', 'body', 'contact']
        labels = {'body': '问题或建议', 'contact': '联系方式'}
        widgets = {'body': forms.Textarea(attrs={'rows': 5})}
        help_texts = {
            'body': '请描述你遇到的问题或想要的改进。请勿填写密码或验证码。',
            'contact': '请填写能收到回复的邮箱、QQ 或其他联系方式。',
        }


@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('contact', 'body')
def submit(request):
    from_email = request.GET.get('category') == SupportMessage.Category.EMAIL_ACCESS
    initial_category = SupportMessage.Category.EMAIL_ACCESS if from_email else SupportMessage.Category.QUESTION
    form = SupportMessageForm(
        request.POST if request.method == 'POST' else None, initial={'category': initial_category}, label_suffix='',
    )
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            account = lock_current_account(request)
            cutoff = timezone.now() - timedelta(minutes=1)
            duplicate = SupportMessage.objects.filter(account=account, created_at__gte=cutoff, **form.cleaned_data)
            if not duplicate.exists():
                form.instance.account = account
                form.save()
        messages.success(request, '留言已提交，管理员会通过你留下的联系方式回复。')
        return redirect('account_profile')
    return render(request, 'account_feedback.html', {'form': form, 'from_email': from_email})
