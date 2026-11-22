import unicodedata

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UsernameField
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError


class AccountUsernameField(UsernameField):
    def __init__(self):
        super().__init__(label='用户名', max_length=150, validators=[UnicodeUsernameValidator()])

    def clean(self, value):
        username = super().clean(value)
        normalized = unicodedata.normalize('NFKC', username).casefold()
        if '@' in username or normalized == settings.OWNER_USERNAME.casefold():
            raise ValidationError('该用户名不可用。')
        return username


def username_exists(username, *, exclude_id=None):
    matches = get_user_model().objects.filter(username__iexact=username)
    if exclude_id is not None:
        matches = matches.exclude(pk=exclude_id)
    return matches.exists()
