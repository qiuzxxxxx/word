"""Keep all learning models inside the owner's original or the current learner's DB."""

import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import DEFAULT_DB_ALIAS, connections, transaction


LEARNING = 'learning'
_database = ContextVar('learning_database', default=DEFAULT_DB_ALIAS)
_learner = ContextVar('learning_account', default=None)


def learning_db():
    return _database.get()


def learner_id():
    return _learner.get()


def workspace_path(user_id):
    return settings.DATA_DIR / 'learners' / f'{int(user_id)}.sqlite3'


def template_path():
    return settings.DATA_DIR / 'learner-template.sqlite3'


@contextmanager
def learning_file(path):
    connection = connections[LEARNING]
    previous = connection.settings_dict
    connection.close()
    connection.settings_dict = {**previous, 'NAME': str(path)}
    token = _database.set(LEARNING)
    try:
        yield
    finally:
        try:
            connection.close()
        finally:
            connection.settings_dict = previous
            _database.reset(token)


def provision(user_id):
    path = workspace_path(user_id)
    if path.exists():
        return path
    # The central SQLite DB's BEGIN IMMEDIATE serializes first-time creation across workers.
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        if not path.exists():
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=path.parent, prefix='.creating-', delete=False) as handle:
                temporary = Path(handle.name)
            try:
                shutil.copyfile(template_path(), temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return path


@contextmanager
def user_workspace(user_id, is_owner):
    token = _learner.set(user_id)
    try:
        if is_owner:
            yield
        else:
            with learning_file(provision(user_id)):
                get_user_model().objects.using(LEARNING).get_or_create(
                    pk=user_id, defaults={'username': f'learner-{user_id}', 'password': '!', 'is_active': False},
                )
                yield
    finally:
        _learner.reset(token)


class LearningRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'words':
            return learning_db()
        return DEFAULT_DB_ALIAS

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, **hints):
        if db == LEARNING and app_label not in {'words', 'auth', 'contenttypes'}:
            return False
        return None
