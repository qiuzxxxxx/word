"""Run production integration checks against temporary SQLite files and mocked email/AI services."""

import io
import json
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
PASSWORD = 'Study-workspace/Test-2026'


def verify():
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import connections
    from django.test import Client

    from accounts.auth import COOKIE
    from accounts.models import Account, EmailCode, SharedModel, SharedUsage
    from accounts.registration import VERIFICATION_TTL_MINUTES, send_verification
    from accounts.workspaces import learning_file, workspace_path
    from words.models import AIModel, Note, Unit, UserSettings, Word

    call_command('migrate', 'auth', verbosity=0)
    call_command('migrate', 'words', '0063', verbosity=0)
    owner = get_user_model().objects.create_user('zx', password=PASSWORD)
    UserSettings.objects.create(privacy_agreed_version=settings.PRIVACY_VERSION, privacy_agreed_hash='a' * 64)
    unit = Unit.objects.create(number=99, name='Legacy owner words')
    Word.objects.create(word='legacy-owner-word', unit=unit)
    call_command('migrate', verbosity=0)
    call_command('check', deploy=True, fail_level='WARNING')
    call_command('prepare_learning', stdout=io.StringIO())
    owner.refresh_from_db()
    assert owner.is_superuser and owner.is_staff

    def browser():
        return Client(enforce_csrf_checks=True, HTTP_HOST='learn.example.com', HTTP_X_FORWARDED_PROTO='https')

    def post(client, path, data=None, json=False):
        kwargs = {'HTTP_ORIGIN': 'https://learn.example.com', 'HTTP_X_CSRFTOKEN': client.cookies['csrftoken'].value}
        if json:
            kwargs['content_type'] = 'application/json'
        return client.post(path, data or {}, **kwargs)

    def login(client, username, password=PASSWORD, status=302):
        assert client.get('/login/').status_code == 200
        assert post(client, '/login/', {'username': username, 'password': password}).status_code == status
        if status == 302:
            assert client.cookies[COOKIE]['secure'] and client.cookies[COOKIE]['httponly']

    supervisor, first, second = browser(), browser(), browser()
    assert supervisor.get('/healthz/').content == b'ok\n'
    login(supervisor, 'zx')
    assert supervisor.get('/control/').status_code == 200
    assert supervisor.get('/api/backup/').status_code == 200
    assert b'privacy-mask hidden' in supervisor.get('/').content
    assert post(supervisor, '/api/privacy/agree/', {'text_hash': 'b' * 64}, json=True).json()['success']
    owner_account = Account.objects.get(pk=owner.pk)
    assert owner_account.privacy_agreed_hash == 'b' * 64 and owner_account.privacy_agreed_at

    with patch('accounts.registration.urlopen') as send_request:
        send_request.return_value.__enter__.return_value.status = 200
        send_verification('alice@example.com', '654321')
        request = send_request.call_args.args[0]
        assert request.full_url == 'https://api.resend.com/emails' and request.get_method() == 'POST'
        assert request.get_header('User-agent') == f'Lexizx/{settings.APP_VERSION}'
        assert request.get_header('Accept') == 'application/json'
        assert request.get_header('Authorization') == 'Bearer mocked-resend-key'
        mail = json.loads(request.data)
        assert mail['to'] == ['alice@example.com']
        assert mail['from'] == f'学习工具 <{settings.WORD_EMAIL_FROM}>'
        assert mail['subject'] == '学习工具｜邮箱注册验证码'
        for content in (mail['text'], mail['html']):
            assert '654321' in content and f'{VERIFICATION_TTL_MINUTES} 分钟内' in content

    with patch('accounts.registration.send_verification') as send:
        register_page = first.get('/register/')
        assert register_page.status_code == 200
        assert b'name="password_confirm"' in register_page.content and b'name="agree_privacy"' in register_page.content
        assert '个人信息与服务器数据'.encode() in register_page.content
        assert post(first, '/register/', {'action': 'send', 'email': 'Alice@example.com'}).status_code == 200
        assert post(first, '/register/', {'action': 'send', 'email': 'Alice@example.com'}).status_code == 200
        send.assert_called_once()
        email, code = send.call_args.args
        assert email == 'alice@example.com' and len(code) == 6
        pending = EmailCode.objects.get(email=email)
        assert (pending.expires_at - pending.sent_at).total_seconds() == VERIFICATION_TTL_MINUTES * 60
        wrong = '011234' if code != '011234' else '011235'
        signup = {'email': email, 'username': 'learner-alice', 'password': PASSWORD,
                  'password_confirm': PASSWORD, 'code': code, 'agree_privacy': 'on',
                  'privacy_version': settings.PRIVACY_VERSION}
        get_user_model().objects.create_user('ExistingReader', password=PASSWORD)
        for invalid, message in (
            ({'username': ''}, '这个字段是必填项'),
            ({'username': 'ＺＸ'}, '该用户名不可用'),
            ({'username': 'existingreader'}, '该用户名已被使用'),
            ({'password_confirm': 'different'}, '两次输入的密码不一致'),
            ({'agree_privacy': ''}, '请阅读并同意用户声明'),
            ({'privacy_version': 'older-version'}, '用户声明已更新'),
        ):
            page = post(first, '/register/', signup | invalid)
            assert page.status_code == 200 and message.encode() in page.content, invalid
            assert not Account.objects.filter(email=email).exists()
        assert EmailCode.objects.get(email=email).attempts == 0
        bad = post(first, '/register/', signup | {'code': wrong})
        assert bad.status_code == 200 and not Account.objects.filter(email=email).exists()
        assert EmailCode.objects.get(email=email).attempts == 1
        assert post(first, '/register/', signup).status_code == 302

    assert post(supervisor, '/control/users/', {'username': 'bob', 'email': 'bob@example.com',
                                                'password': PASSWORD}).status_code == 302
    alice = Account.objects.get(email='alice@example.com')
    assert alice.user.username == 'learner-alice'
    assert alice.privacy_agreed_version == settings.PRIVACY_VERSION and alice.privacy_agreed_at
    bob = Account.objects.get(email='bob@example.com')
    login(first, 'learner-alice')
    login(second, 'bob')
    assert first.get('/control/').status_code == 403
    assert first.get('/admin/').status_code == 404
    assert first.get('/').status_code == 200 and second.get('/settings/').status_code == 200
    assert b'privacy-mask hidden' in first.get('/').content
    assert second.get('/api/achievements/').json()['total_count'] == 14
    assert b'legacy-owner-word' not in first.get('/api/backup/').content
    assert b'legacy-owner-word' in supervisor.get('/api/backup/').content
    assert f'lexizx:{alice.pk}:'.encode() in first.get('/').content
    assert f'lexizx:{bob.pk}:'.encode() in second.get('/').content

    new_word = post(first, '/api/word/create/', {'word': 'first-private-word', 'meanings': ['private']}, json=True)
    assert new_word.json()['success'], new_word.content
    word_id = new_word.json()['word_id']
    assert post(first, f'/api/note/{word_id}/', {'content': 'first-private-note'}, json=True).json()['success']
    original = first.get('/api/backup/').json()
    assert second.get(f'/api/note/{word_id}/').status_code == 404
    assert b'first-private-word' not in second.get('/api/backup/').content
    assert post(first, '/api/restore/', original, json=True).json()['success']
    assert b'first-private-note' in first.get('/api/backup/').content

    def read_backup(client):
        try:
            return client.get('/api/backup/').content
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        alice_backup = pool.submit(read_backup, first)
        bob_backup = pool.submit(read_backup, second)
        assert b'first-private-note' in alice_backup.result()
        assert b'first-private-note' not in bob_backup.result()
    with learning_file(workspace_path(alice.pk)):
        assert Note.objects.filter(content='first-private-note').exists()
    with learning_file(workspace_path(bob.pk)):
        assert not Note.objects.filter(content='first-private-note').exists()

    markdown = '**Update**\n\n- Item one\n\n<script>test</script>\n\n[bad](javascript:alert%281%29)'
    assert post(first, '/control/announcements/preview/', {'body': markdown}).status_code == 403
    preview = post(supervisor, '/control/announcements/preview/', {'body': markdown}).json()['html']
    assert '<strong>Update</strong>' in preview and '<li>Item one</li>' in preview
    assert '<script>' not in preview and 'javascript:' not in preview
    assert post(supervisor, '/control/announcement/', {'title': 'Update notice', 'body': markdown,
                                                       'published': 'on'}).status_code == 302
    announcement_page = second.get('/').content
    assert b'Update notice' in announcement_page and b'&lt;script&gt;test&lt;/script&gt;' in announcement_page
    assert b'<strong>Update</strong>' in announcement_page and '豫ICP备2026020102号'.encode() in announcement_page
    assert '豫ICP备2026020102号'.encode() in browser().get('/register/').content

    private_key = 'private-test-key'
    secret = 'public-test-secret'
    endpoint = 'https://api.example.net/v1/chat/completions'
    assert post(supervisor, '/control/model/', {'name': 'Shared test', 'model_id': 'shared', 'endpoint': endpoint,
                                               'secret': secret, 'enabled': 'on', 'per_user_daily': 1,
                                               'daily_total': 2}).status_code == 302
    assert secret not in SharedModel.objects.get().encrypted_key
    assert secret.encode() not in supervisor.get('/control/model/1/').content
    personal = post(first, '/api/ai-models/', {'model_id': 'personal', 'api_key': private_key}, json=True).json()['model']
    models = first.get('/api/ai-models/').json()['models']
    public = next(model for model in models if model['is_public'])
    assert personal['id'] != public['id'] and not public['endpoint']
    assert post(first, '/api/ai-models/', {'id': public['id'], 'model_id': 'modified'}, json=True).status_code == 403
    assert post(first, '/api/settings/', {'quick_memory_model': public['id']}, json=True).json()['success']
    assert private_key.encode() not in first.get('/api/backup/').content
    assert b'personal' not in second.get('/api/ai-models/').content
    assert [m['model_id'] for m in first.get('/api/backup/').json()['ai_models']] == ['personal']

    with patch('accounts.ai_gateway.build_opener') as opener:
        upstream = opener.return_value.open.return_value.__enter__.return_value
        upstream.read.return_value = b'{"choices":[{"message":{"content":"ok"}}]}'
        assert post(first, '/api/ai/test/', {'model_id': public['id']}, json=True).json()['success']
        request = opener.return_value.open.call_args.args[0]
        assert request.full_url == endpoint and request.get_header('Authorization') == f'Bearer {secret}'
        assert post(first, '/api/ai/test/', {'model_id': public['id']}, json=True).status_code == 502
        opener.return_value.open.assert_called_once()
    assert SharedUsage.objects.get().count == 1
    with patch('accounts.ai_gateway.build_opener') as opener:
        assert post(first, '/api/ai/test/', {'model': 'private', 'endpoint': 'http://localhost:8080'}, json=True).status_code == 502
        opener.assert_not_called()

    alternate = browser()
    login(alternate, 'alice@example.com')
    assert first.get('/api/settings/').status_code == 401
    assert alternate.get('/api/settings/').status_code == 200
    assert post(supervisor, f'/control/users/{bob.pk}/action/', {'operation': 'ban'}).status_code == 302
    assert second.get('/api/settings/').status_code == 401
    login(second, 'bob', status=200)
    assert post(supervisor, f'/control/users/{bob.pk}/action/', {'operation': 'unban'}).status_code == 302
    login(second, 'bob')
    new_password = 'Reset-workspace/Test-2026'
    assert post(supervisor, f'/control/users/{bob.pk}/', {'username': 'bob', 'email': 'bob@example.com',
                                                        'password': new_password}).status_code == 302
    assert second.get('/api/settings/').status_code == 401
    login(second, 'bob', status=200)
    login(second, 'bob', password=new_password)
    assert post(supervisor, '/control/site/', {'maintenance': 'on', 'message': 'Service work'}).status_code == 302
    assert second.get('/api/settings/').status_code == 503
    assert browser().get('/register/').status_code == 503
    login(browser(), 'bob', password=new_password, status=503)
    assert supervisor.get('/control/').status_code == 200
    assert post(supervisor, '/control/site/', {'message': ''}).status_code == 302
    assert post(supervisor, f'/control/users/{bob.pk}/action/', {'operation': 'delete'}).status_code == 302
    assert second.get('/api/settings/').status_code == 401
    assert Account.objects.get(pk=bob.pk).deleted_at and workspace_path(bob.pk).is_file()
    assert post(alternate, '/logout/').status_code == 302
    assert alternate.get('/api/settings/').status_code == 401
    call_command('prepare_learning', stdout=io.StringIO())
    with learning_file(workspace_path(alice.pk)):
        assert Word.objects.filter(word='first-private-word').exists()
        assert AIModel.objects.filter(shared_model_id__isnull=False).exists()
    print('Verified production migration, workspaces, JWT rotation, OTP, administration, AI isolation and quotas.')


if __name__ == '__main__':
    import django

    with TemporaryDirectory(prefix='lexizx-verification-') as directory:
        os.environ.update(
            DJANGO_SETTINGS_MODULE='vocab_project.server_settings', DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
            WORD_VAULT_KEY=Fernet.generate_key().decode(), WORD_HOST='learn.example.com',
            WORD_DATA_DIR=directory, WORD_STATIC_ROOT=str(Path(directory) / 'static'),
            RESEND_API_KEY='mocked-resend-key', WORD_EMAIL_FROM='no-reply@auth.example.com',
        )
        django.setup()
        from django.db import connections

        try:
            verify()
        finally:
            connections.close_all()
