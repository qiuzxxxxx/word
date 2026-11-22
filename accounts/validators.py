from django.core.exceptions import ValidationError


class PasswordLengthValidator:
    def __init__(self, min_length=6, max_length=12):
        self.min_length = int(min_length)
        self.max_length = int(max_length)

    def validate(self, password, user=None):
        length = len(password) if password else 0
        if length < self.min_length or length > self.max_length:
            raise ValidationError(
                f'密码长度必须在 {self.min_length} 至 {self.max_length} 位之间。',
                code='password_length_invalid',
            )

    def get_help_text(self):
        return f'密码长度必须在 {self.min_length} 至 {self.max_length} 位之间。'
