import io
import re
from contextlib import nullcontext, redirect_stdout
from datetime import timedelta
from unittest import SkipTest
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

if not getattr(settings, 'SERVER_MODE', False):
    raise SkipTest('Web account tests require the web settings.')

from .models import Account, AccountEmailCode, Announcement, EmailCode, SupportMessage
from .registration import code_digest


@override_settings(LOCAL_WEB=True, WORD_EMAIL_FROM='no-reply@example.test',
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SECURE_SSL_REDIRECT=False)
class ProfileTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(settings.OWNER_USERNAME, password='initial123')
        self.account = Account.objects.create(user=self.user)
        self.profile_url = reverse('account_profile')
        self.email_url = reverse('account_email')
        self.password_url = reverse('account_password')
        self.login(self.client, self.user.username)

    def login(self, client, identifier, password='initial123'):
        return client.post(reverse('owner_login'), {'username': identifier, 'password': password})

    def set_email(self, email):
        self.account.email = self.user.email = email
        self.account.save(update_fields=['email'])
        self.user.save(update_fields=['email'])

    def send_code(self, action, recipient, **data):
        with redirect_stdout(io.StringIO()):
            response = self.client.post(self.email_url, {'action': action, **data})
        self.assertRedirects(response, self.email_url, fetch_redirect_response=False)
        self.assertEqual(mail.outbox[-1].to, [recipient])
        return re.search(r'\b[0-9]{6}\b', mail.outbox[-1].body).group()

    def test_bind_email_and_use_it_to_log_in(self):
        self.assertContains(self.client.get(self.profile_url), '未绑定')
        self.assertContains(self.client.get(self.email_url), '向新邮箱发送验证码')
        email = 'bound@example.test'
        code = self.send_code('send_new', email, email=email.upper())
        wrong_address = self.client.post(self.email_url, {'action': 'verify_new', 'email': 'other@example.test', 'code': code})
        self.assertContains(wrong_address, '验证码错误')
        response = self.client.post(self.email_url, {'action': 'verify_new', 'email': email, 'code': code}, follow=True)
        self.assertContains(response, '邮箱绑定成功')
        self.account.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.account.email, email)
        self.assertEqual(self.user.email, email)
        self.assertFalse(AccountEmailCode.objects.exists())
        self.assertRedirects(self.login(Client(), email), '/', fetch_redirect_response=False)
        with redirect_stdout(io.StringIO()):
            response = Client().post(reverse('password_reset'), {'action': 'send', 'email': email})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[-1].to, [email])
        self.assertIn('重置密码', mail.outbox[-1].subject)

    def test_change_requires_old_then_new_and_replaces_email_login(self):
        old_email = 'old@example.test'
        new_email = 'new@example.test'
        self.set_email(old_email)
        bypass = self.client.post(self.email_url, {'action': 'send_new', 'email': new_email})
        self.assertContains(bypass, '请重新验证旧邮箱')
        self.assertFalse(AccountEmailCode.objects.exists())
        old_code = self.send_code('send_old', old_email)
        other_user = get_user_model().objects.create_user(username='another')
        Account.objects.create(user=other_user, email='used@example.test')
        self.assertRedirects(self.client.post(self.email_url, {'action': 'verify_old', 'code': old_code}),
                             self.email_url, fetch_redirect_response=False)
        duplicate = self.client.post(self.email_url, {'action': 'send_new', 'email': 'USED@example.test'})
        self.assertContains(duplicate, '该邮箱已被其他账号使用')
        new_code = self.send_code('send_new', new_email, email=new_email)
        self.account.refresh_from_db()
        self.assertEqual(self.account.email, old_email)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.email_url, {'action': 'verify_new', 'email': new_email, 'code': new_code}, follow=True)
        self.assertContains(response, '邮箱换绑成功')
        self.assertEqual(mail.outbox[-1].to, [old_email])
        self.assertIn('绑定邮箱已更换', mail.outbox[-1].subject)
        self.assertNotIn(new_email, mail.outbox[-1].body)
        self.assertEqual(self.login(Client(), old_email).status_code, 200)
        self.assertRedirects(self.login(Client(), new_email), '/', fetch_redirect_response=False)

    def test_expired_old_verification_blocks_valid_new_code(self):
        old_email = 'old@example.test'
        new_email = 'new@example.test'
        self.set_email(old_email)
        old_code = self.send_code('send_old', old_email)
        self.client.post(self.email_url, {'action': 'verify_old', 'code': old_code})
        new_code = self.send_code('send_new', new_email, email=new_email)
        AccountEmailCode.objects.filter(purpose=AccountEmailCode.Purpose.OLD).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = self.client.post(self.email_url, {'action': 'verify_new', 'email': new_email, 'code': new_code})
        self.assertContains(response, '请重新验证旧邮箱')
        self.account.refresh_from_db()
        self.assertEqual(self.account.email, old_email)

    def test_new_login_cannot_continue_a_previous_logins_verification(self):
        self.set_email('old@example.test')
        old_code = self.send_code('send_old', self.account.email)
        self.client.post(self.email_url, {'action': 'verify_old', 'code': old_code})
        next_client = Client()
        self.assertRedirects(self.login(next_client, self.user.username), '/', fetch_redirect_response=False)
        self.assertContains(next_client.get(self.email_url), '向旧邮箱发送验证码')
        self.assertEqual(self.client.get(self.email_url).status_code, 302)

    def test_attempt_limit_and_resend_cooldown(self):
        email = 'bound@example.test'
        code = self.send_code('send_new', email, email=email)
        resend = self.client.post(self.email_url, {'action': 'send_new', 'email': email})
        self.assertContains(resend, '请在 60 秒后重发')
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(5):
            self.client.post(self.email_url, {'action': 'verify_new', 'email': email, 'code': wrong})
        response = self.client.post(self.email_url, {'action': 'verify_new', 'email': email, 'code': code})
        self.assertContains(response, '达到尝试上限')
        self.account.refresh_from_db()
        self.assertIsNone(self.account.email)

    def test_change_password_without_email_and_keep_current_session(self):
        data = {'old_password': 'wrong', 'new_password1': 'changed456', 'new_password2': 'changed456'}
        self.assertContains(self.client.post(self.password_url, data), '当前密码不正确')
        data['old_password'] = 'initial123'
        data['new_password2'] = 'different'
        self.assertContains(self.client.post(self.password_url, data), '两次输入的新密码不一致')
        data['new_password1'] = data['new_password2'] = 'short'
        self.assertContains(self.client.post(self.password_url, data), '密码长度必须在 6 至 12 位之间')
        data['new_password1'] = data['new_password2'] = 'changed456'
        response = self.client.post(self.password_url, data, follow=True)
        self.assertContains(response, '密码已修改')
        self.assertContains(self.client.get(self.profile_url), self.user.username)
        self.assertEqual(self.login(Client(), self.user.username).status_code, 200)
        self.assertRedirects(self.login(Client(), self.user.username, 'changed456'), '/', fetch_redirect_response=False)

    def test_account_pages_require_login_and_protect_post(self):
        for url in (self.profile_url, self.email_url, self.password_url, reverse('account_feedback')):
            self.assertRedirects(Client().get(url), f'{reverse("owner_login")}?next={url}',
                                 fetch_redirect_response=False)
        self.client.handler.enforce_csrf_checks = True
        self.assertEqual(self.client.post(self.email_url, {'action': 'send_new', 'email': 'new@example.test'}).status_code, 403)

    def test_password_change_notifies_bound_email_and_survives_delivery_failure(self):
        self.set_email('bound@example.test')
        change = {'old_password': 'initial123', 'new_password1': 'changed456', 'new_password2': 'changed456'}
        with self.captureOnCommitCallbacks(execute=True):
            self.assertRedirects(self.client.post(self.password_url, change), self.profile_url, fetch_redirect_response=False)
        self.assertEqual(mail.outbox[-1].to, [self.account.email])
        self.assertIn('登录密码已修改', mail.outbox[-1].subject)
        self.assertNotIn('changed456', mail.outbox[-1].body)

        change = {'old_password': 'changed456', 'new_password1': 'another789', 'new_password2': 'another789'}
        with patch('accounts.registration.send_email', side_effect=OSError), self.assertLogs('accounts.registration'):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertRedirects(self.client.post(self.password_url, change), self.profile_url, fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('another789'))

    def test_feedback_from_email_help_is_visible_only_to_administrator(self):
        self.set_email('unreachable@example.test')
        feedback_url = reverse('account_feedback')
        inbox_url = reverse('control_feedback')
        self.assertContains(self.client.get(self.email_url), '无法访问？跟管理员留言')
        self.assertContains(self.client.get(self.profile_url), '联系管理员')
        self.assertContains(self.client.get(self.profile_url), 'id="account-privacy"')

        learner = get_user_model().objects.create_user('support-learner', password='initial123')
        learner_account = Account.objects.create(user=learner)
        learner_client = Client()
        with patch('accounts.auth.user_workspace', side_effect=lambda *_: nullcontext()):
            self.login(learner_client, learner.username)
            form = learner_client.get(f'{feedback_url}?category=email-access').context['form']
            self.assertEqual(form['category'].value(), SupportMessage.Category.EMAIL_ACCESS)
            data = {'category': 'email-access', 'body': '旧邮箱无法使用 <标签>', 'contact': 'reachable@example.test'}
            self.assertContains(learner_client.post(feedback_url, {**data, 'contact': ''}), '这个字段是必填项')
            self.assertRedirects(learner_client.post(feedback_url, data), self.profile_url, fetch_redirect_response=False)
            self.assertContains(learner_client.get(self.profile_url), '留言已提交')
            learner_client.post(feedback_url, data)
            entry = SupportMessage.objects.get(account=learner_account)
            status_url = reverse('control_feedback_status', args=[entry.pk])
            self.assertEqual(learner_client.get(inbox_url).status_code, 403)
            self.assertEqual(learner_client.post(status_url, {'operation': 'resolve'}).status_code, 403)

        inbox = self.client.get(inbox_url)
        self.assertContains(inbox, learner.username)
        self.assertContains(inbox, data['contact'])
        self.assertContains(inbox, '旧邮箱无法使用 &lt;标签&gt;')
        self.assertNotContains(inbox, data['body'])
        self.assertEqual(self.client.get(status_url).status_code, 405)
        self.assertRedirects(self.client.post(status_url, {'operation': 'resolve'}), inbox_url, fetch_redirect_response=False)
        self.assertNotContains(self.client.get(inbox_url), data['contact'])
        self.assertContains(self.client.get(f'{inbox_url}?status=resolved'), data['contact'])
        self.client.post(status_url, {'operation': 'reopen'})
        self.assertContains(self.client.get(inbox_url), data['contact'])

    def test_existing_accounts_show_announcements_once_per_successful_login(self):
        popup_url = reverse('next_login_popup')
        Announcement.objects.create(title='本站公告', body='测试公告。')
        first = self.client.get(self.profile_url)
        self.assertContains(first, 'id="webLoginPopups"')
        self.assertContains(first, 'privacy-mask hidden')
        self.assertNotContains(first, 'id="webUsageGuide"')
        self.assertEqual(self.client.post(popup_url).json()['popup'], 'announcement')
        self.assertEqual(self.client.post(popup_url).json()['popup'], '')
        self.assertNotContains(self.client.get('/'), 'id="webLoginPopups"')
        self.assertContains(self.client.get('/'), 'id="topAnnouncementBtn"')

        self.assertEqual(self.login(self.client, self.user.username, 'incorrect').status_code, 200)
        self.assertEqual(self.client.post(popup_url).json()['popup'], '')
        self.assertEqual(self.login(self.client, self.user.username).status_code, 302)
        self.assertContains(self.client.get(reverse('site_control')), 'id="webLoginPopups"')
        self.assertEqual(self.client.post(popup_url).json()['popup'], 'announcement')

    def test_login_without_published_announcement_does_not_create_a_popup(self):
        popup_url = reverse('next_login_popup')
        announcement = Announcement.objects.create(title='草稿', body='仅草稿。', published=False)
        self.assertNotContains(self.client.get('/'), 'id="announcementModal"')
        self.assertEqual(self.client.post(popup_url).json()['popup'], '')
        Announcement.objects.filter(pk=announcement.pk).update(published=True)
        self.assertEqual(self.client.post(popup_url).json()['popup'], '')
        self.assertEqual(self.login(self.client, self.user.username).status_code, 302)
        self.assertEqual(self.client.post(popup_url).json()['popup'], 'announcement')

    def test_registered_account_receives_agreement_and_guide_only_once(self):
        email = 'welcome@example.test'
        code = '123456'
        now = timezone.now()
        EmailCode.objects.create(email=email, digest=code_digest(email, code), sent_at=now,
                                 expires_at=now + timedelta(minutes=10))
        Announcement.objects.create(title='本站公告', body='测试公告。')
        new_client = Client()
        signup = new_client.post(reverse('register'), {
            'email': email, 'code': code, 'username': 'new-learner', 'password': 'initial123',
            'password_confirm': 'initial123', 'agree_privacy': 'on', 'privacy_version': settings.PRIVACY_VERSION,
        })
        self.assertRedirects(signup, reverse('owner_login'), fetch_redirect_response=False)
        popup_url = reverse('next_login_popup')
        self.assertEqual(new_client.post(popup_url).status_code, 401)
        with patch('accounts.auth.user_workspace', side_effect=lambda *_: nullcontext()):
            self.assertEqual(self.login(new_client, email).status_code, 302)
            self.assertContains(new_client.get(self.email_url), 'id="webUsageGuide"')
            self.assertEqual(new_client.get(popup_url).status_code, 405)
            self.assertEqual(new_client.post(popup_url).json()['popup'], 'agreement')
            self.assertContains(new_client.get(self.profile_url), 'id="webUsageGuide"')
            self.assertEqual(new_client.post(popup_url).json()['popup'], 'guide')
            self.assertNotContains(new_client.get(self.profile_url), 'id="webUsageGuide"')
            self.assertEqual(new_client.post(popup_url).json()['popup'], 'announcement')
            self.assertEqual(new_client.post(popup_url).json()['popup'], '')
            self.assertEqual(new_client.post(reverse('owner_logout')).status_code, 302)
            self.assertEqual(self.login(new_client, email).status_code, 302)
            self.assertEqual(new_client.post(popup_url).json()['popup'], 'announcement')
