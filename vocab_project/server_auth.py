from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.middleware import LoginRequiredMiddleware
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe

from accounts.models import SiteState
from accounts.workspaces import template_path
from words.models import Word


class PortalLoginRequired(LoginRequiredMiddleware):
    def handle_no_permission(self, request, view_func):
        if request.path_info.startswith('/api/'):
            return JsonResponse({'error': '请先登录'}, status=401)
        return super().handle_no_permission(request, view_func)


def forbidden(request, exception=None):
    return render(None, 'portal_error.html', {
        'title': '无法访问此页面', 'detail': '当前账号没有权限访问这个页面。',
    }, status=403)


def not_found(request, exception=None):
    return render(None, 'portal_error.html', {
        'title': '找不到页面', 'detail': '页面可能已移动或删除。',
    }, status=404)


def server_error(request):
    return render(None, 'portal_error.html', {
        'title': '页面暂时不可用', 'detail': '请稍后刷新页面重试。',
    }, status=500)


def csrf_failure(request, reason=''):
    if request.path_info.startswith('/api/'):
        return JsonResponse({'error': '页面会话已失效，请刷新后再试。'}, status=403)
    return render(None, 'portal_error.html', {
        'title': '页面会话已失效', 'detail': '请重新打开页面后再试一次。',
    }, status=403)


@login_not_required
@require_safe
def health(request):
    try:
        Word.objects.exists()
        ready = SiteState.objects.filter(pk=1).exists() and template_path().is_file()
    except DatabaseError:
        ready = False
    if not ready:
        return HttpResponse('unavailable\n', status=503, content_type='text/plain')
    return HttpResponse('ok\n', content_type='text/plain')
