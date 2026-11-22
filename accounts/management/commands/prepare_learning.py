from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from accounts.models import Account, SiteState
from accounts.workspaces import LEARNING, learning_file, template_path
from words.models import AICallLog, UserSettings, Word


class Command(BaseCommand):
    help = '准备最高管理员、独立词库模板，并升级已有用户的词库数据库。'

    def handle(self, *args, **options):
        with transaction.atomic():
            user = get_user_model().objects.filter(username=settings.OWNER_USERNAME).first()
            if not user:
                raise CommandError('请先创建最高管理员账号 zx。')
            user.is_staff = user.is_superuser = user.is_active = True
            user.save(update_fields=['is_staff', 'is_superuser', 'is_active'])
            Account.objects.get_or_create(user=user)
            SiteState.objects.get_or_create(pk=1)
            # Former desktop-style AI log writers did not attach the active user.
            AICallLog.objects.filter(user__isnull=True).update(user_id=user.pk)

        for path in sorted((settings.DATA_DIR / 'learners').glob('*.sqlite3')):
            if path.stem.isdecimal():
                with learning_file(path):
                    call_command('migrate', database=LEARNING, interactive=False, verbosity=0)

        with NamedTemporaryFile(dir=settings.DATA_DIR, prefix='.learner-template-', delete=False) as handle:
            temporary = Path(handle.name)
        try:
            with learning_file(temporary):
                call_command('migrate', database=LEARNING, interactive=False, verbosity=0)
                with transaction.atomic(using=LEARNING):
                    call_command('import_words', str(settings.BASE_DIR / 'data/hongbaoshu.json'),
                                 skip_ai=True, stdout=self.stdout)
                    if not Word.objects.exists():
                        raise CommandError('基础词库为空。')
                    UserSettings.get_settings()
                with connections[LEARNING].cursor() as cursor:
                    cursor.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    cursor.execute('PRAGMA journal_mode=DELETE')
                    cursor.fetchone()
            temporary.replace(template_path())
        finally:
            temporary.unlink(missing_ok=True)
        self.stdout.write(self.style.SUCCESS('最高管理员和独立词库已就绪。'))
