import io

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Account
from words.models import UserSettings, Word


class Command(BaseCommand):
    help = '初始化本地网页、测试账号和本地邮件目录。'

    def handle(self, *args, **options):
        if not getattr(settings, 'LOCAL_WEB', False):
            raise CommandError('此命令仅用于本地网页版。')
        call_command('migrate', interactive=False, verbosity=0)
        with transaction.atomic():
            if not UserSettings.objects.exists() and not Word.objects.exists():
                call_command('import_words', str(settings.BASE_DIR / 'data/hongbaoshu.json'), skip_ai=True,
                             stdout=io.StringIO())
            UserSettings.get_settings()
            users = get_user_model().objects
            owner = users.filter(username=settings.OWNER_USERNAME).first()
            if not owner:
                owner = users.create_superuser(settings.OWNER_USERNAME, '', '123456')
            else:
                owner.is_staff = owner.is_superuser = owner.is_active = True
                owner.save(update_fields=['is_staff', 'is_superuser', 'is_active'])
            Account.objects.get_or_create(user=owner)

            tester = users.filter(username='cs').first()
            if not tester:
                tester = users.create_user('cs', email='cs@example.test', password='123456')
                Account.objects.create(user=tester)
            else:
                tester.is_active = True
                tester.save(update_fields=['is_active'])
                Account.objects.get_or_create(user=tester)
        call_command('prepare_learning', stdout=io.StringIO())
        self.stdout.write(f'本地邮件目录：{settings.EMAIL_FILE_PATH}')
