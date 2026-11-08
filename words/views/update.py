"""自动更新模块 - 检查更新、下载、校验、安装状态管理"""

import json
import re
import threading
import time
import urllib.error
import urllib.request
from html import unescape
from pathlib import Path
import xml.etree.ElementTree as ET

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods


GITHUB_LATEST_RELEASE_API = (
    'https://api.github.com/repos/'
    'qiuzxxxxx/Lexizx/releases/latest'
)
# 主站 Atom 订阅源：走 github.com 而非 api.github.com，不受未授权 API
# 配额（60 次/小时，出口 IP 共享时常被占满 → 403）限制，国内也更稳。
GITHUB_RELEASES_ATOM = (
    'https://github.com/qiuzxxxxx/Lexizx/releases.atom'
)
RELEASE_PAGE = ('https://github.com/qiuzxxxxx/'
                'Lexizx/releases/latest')


def _parse_version(text):
    """'v3.4.0' / '3.4.0-beta' -> (3, 4, 0)；解析失败返回 None"""
    m = re.search(r'(\d+)\.(\d+)\.(\d+)', str(text or ''))
    return tuple(int(x) for x in m.groups()) if m else None


def _http_get_json(url, timeout=6):
    req = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'Postgraduate-Vocabulary-App',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(5 * 1024 * 1024 + 1).decode('utf-8'))


def _http_get_text(url, timeout=8):
    req = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/atom+xml, text/html;q=0.9',
            'User-Agent': 'Mozilla/5.0 (compatible; '
                          'Postgraduate-Vocabulary-App)',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(512 * 1024 + 1).decode('utf-8', 'ignore')


def _release_html_to_text(raw):
    """Release 说明的 HTML 片段 -> 弹窗 <pre> 可读的纯文本"""
    text = re.sub(r'<\s*br\s*/?\s*>', '\n', raw, flags=re.I)
    text = re.sub(r'<\s*li[^>]*>', '· ', text, flags=re.I)
    text = re.sub(r'</\s*(p|li|h[1-6]|div)\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\n{3,}', '\n\n', unescape(text)).strip()


def _latest_release_from_atom(attempts=(3, 4, 5)):
    """从 github.com 的 Releases Atom 订阅源解析最新 Release。
    该通道走 github.com 主站（不受 API 配额限制），但本机到 github.com
    偶发连接被重置（SSL UNEXPECTED_EOF），故做多次快速重试；全失败返回 None"""
    ns = '{http://www.w3.org/2005/Atom}'
    for timeout in attempts:
        try:
            root = ET.fromstring(_http_get_text(GITHUB_RELEASES_ATOM, timeout=timeout))
            entry = root.find(ns + 'entry')
            if entry is None:
                continue
            link = entry.find(ns + 'link')
            html_url = (link.get('href') or '') if link is not None else ''
            title = (entry.findtext(ns + 'title') or '').strip()
            tag = html_url.rsplit('/tag/', 1)[-1] if '/tag/' in html_url else ''
            return {
                'tag_name': tag or title,
                'name': title,
                'body': _release_html_to_text(entry.findtext(ns + 'content') or ''),
                'html_url': html_url or RELEASE_PAGE,
                'published_at': (entry.findtext(ns + 'updated') or '')[:10],
            }
        except Exception:
            continue
    return None


def api_check_update(request):
    """检查是否有新版本：优先读 github.com 的 Releases Atom 订阅源，
    失败时回退官方 Release API。两者都不可达时友好返回，不抛错。"""
    current = settings.APP_VERSION
    cur_tuple = _parse_version(current)

    data = _latest_release_from_atom()
    if not data:
        # 回退官方 Release API（国内偶发被重置，失败时递增超时重试）
        for timeout in (6, 8):
            try:
                data = _http_get_json(GITHUB_LATEST_RELEASE_API, timeout=timeout)
                break
            except urllib.error.HTTPError as e:
                # 403 = 未授权配额耗尽，重试无意义，立即放弃
                if e.code == 403:
                    break
                data = None
            except Exception:
                data = None
    if not data:
        return JsonResponse({
            'success': False,
            'current': 'v' + current,
            'error': '无法连接更新服务器（GitHub），请检查网络后重试',
        })
    try:
        latest_tag = data.get('tag_name') or ''
        if not _parse_version(latest_tag):
            raise ValueError('bad tag')
        payload = {
            'latest': latest_tag,
            'release_title': data.get('name') or '',
            'release_notes': data.get('body') or '',
            'html_url': data.get('html_url') or RELEASE_PAGE,
            'published_at': (data.get('published_at') or '')[:10],
        }
    except Exception:
        return JsonResponse({
            'success': False,
            'current': 'v' + current,
            'error': '更新服务器返回数据异常，请稍后重试',
        })

    new_tuple = _parse_version(payload['latest'])
    payload.update({
        'success': True,
        'current': 'v' + current,
        'has_update': bool(new_tuple and cur_tuple and new_tuple > cur_tuple),
    })
    return JsonResponse(payload)


# ===== 自动更新：下载 / 校验 / 安装状态 =====
# 只用模块级 dict + 锁管理单个下载任务（本机单用户，无需数据库或额外服务）。
_UPDATE_LOCK = threading.Lock()
_UPDATE_CANCEL = threading.Event()
_UPDATE_STATE = {
    'state': 'idle',          # idle/starting/downloading/verifying/ready/cancelled/error
    'tag': '',
    'downloaded': 0,
    'total': 0,
    'percent': 0,
    'message': '',
    'error': '',
    'path': '',
    'sha_expected': None,
    'sha_ok': None,
}


def _update_dir():
    d = Path(settings.DATA_DIR) / 'update'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _update_status_path():
    return Path(settings.DATA_DIR) / 'update' / 'last_update.json'


def _set_update_state(**kwargs):
    with _UPDATE_LOCK:
        _UPDATE_STATE.update(kwargs)


def _update_snapshot():
    with _UPDATE_LOCK:
        data = dict(_UPDATE_STATE)
    data['desktop'] = bool(getattr(settings, 'FROZEN', False))
    return data


def _download_update_worker(tag):
    """后台下载 + 校验；状态只写内存，下载完成后写一次最终状态"""
    import updater as updater_core

    dest = _update_dir() / f'word-{tag}.exe'
    last_tick = [0.0]

    def progress(done, total):
        now = time.time()
        if total and done < total and now - last_tick[0] < 0.3:
            return
        last_tick[0] = now
        _set_update_state(downloaded=done, total=total,
                          percent=int(done * 100 / total) if total else 0)

    try:
        exe_url, _ = updater_core.asset_urls(tag)
        _set_update_state(state='downloading', message='正在下载新版本…',
                          downloaded=0, total=0, percent=0, error='', path='',
                          sha_expected=None, sha_ok=None)
        size = updater_core.download_file(
            exe_url, dest, progress=progress, cancel=_UPDATE_CANCEL)
        _set_update_state(state='verifying', message='正在校验文件…',
                          downloaded=size, total=size, percent=100)

        try:
            expected = updater_core.fetch_expected_sha(tag)
        except Exception as e:
            raise updater_core.UpdateError('无法取得 SHA-256 校验值，已拒绝安装') from e
        if not updater_core.verify_sha256(dest, expected):
            raise updater_core.UpdateError('SHA-256 校验未通过，已拒绝安装')
        _set_update_state(state='ready', message='校验通过，可以安装',
                          path=str(dest), sha_expected=expected, sha_ok=True)
    except updater_core.UpdateCancelled:
        try:
            dest.unlink()
        except OSError:
            pass
        _set_update_state(state='cancelled', message='已取消下载', path='')
    except urllib.error.HTTPError as e:
        try:
            dest.unlink()
        except OSError:
            pass
        if e.code == 404:
            hint = '该版本尚未发布安装包（Release 缺少 word.exe），请前往 Release 页面确认'
        elif e.code == 403:
            hint = '下载被 GitHub 限流（HTTP 403），请稍后再试'
        else:
            hint = f'下载失败（HTTP {e.code}），请稍后重试'
        _set_update_state(state='error', message='下载失败', error=hint, path='')
    except Exception as e:
        try:
            dest.unlink()
        except OSError:
            pass
        _set_update_state(state='error', message='下载失败', error=str(e), path='')


@require_http_methods(['POST'])
def api_update_download(request):
    """开始下载指定版本的新 EXE（POST-only，下载在后台线程）"""
    import updater as updater_core
    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        payload = {}
    try:
        tag = updater_core.normalize_tag(payload.get('tag'))
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    with _UPDATE_LOCK:
        if _UPDATE_STATE['state'] in ('starting', 'downloading', 'verifying'):
            return JsonResponse({'success': False, 'error': '已有下载任务进行中',
                                 'state': _UPDATE_STATE['state']}, status=409)
        _UPDATE_STATE.update(state='starting', tag=tag, downloaded=0, total=0,
                             percent=0, message='准备下载…', error='', path='',
                             sha_expected=None, sha_ok=None)
    _UPDATE_CANCEL.clear()
    threading.Thread(target=_download_update_worker, args=(tag,), daemon=True).start()
    return JsonResponse({'success': True, 'state': 'starting', 'tag': tag})


@require_http_methods(['GET'])
def api_update_download_status(request):
    return JsonResponse(_update_snapshot())


@require_http_methods(['POST'])
def api_update_download_cancel(request):
    _UPDATE_CANCEL.set()
    return JsonResponse({'success': True})


@require_http_methods(['GET'])
def api_update_result(request):
    """上次更新的结果（安装成功 / 已回滚），确认过就不再重复提示"""
    import updater as updater_core
    data = updater_core.read_status(_update_status_path())
    if not data or data.get('state') not in ('done', 'rolled_back', 'failed'):
        return JsonResponse({'has_result': False})
    if request.session.get('update_result_seen') == data.get('at'):
        return JsonResponse({'has_result': False})
    return JsonResponse({
        'has_result': True,
        'state': data.get('state'),
        'message': data.get('message') or '',
        'at': data.get('at') or '',
    })


@require_http_methods(['POST'])
def api_update_result_ack(request):
    import updater as updater_core
    data = updater_core.read_status(_update_status_path())
    if data:
        request.session['update_result_seen'] = data.get('at')
    return JsonResponse({'success': True})
