from django.conf import settings
from django.db import models


class Account(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True)
    email = models.EmailField(unique=True, null=True, blank=True)
    login_id = models.CharField(max_length=64, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    privacy_agreed_version = models.CharField(max_length=20, blank=True)
    privacy_agreed_hash = models.CharField(max_length=64, blank=True)
    privacy_agreed_at = models.DateTimeField(null=True, blank=True)


class SiteState(models.Model):
    maintenance = models.BooleanField(default=False)
    message = models.CharField(max_length=400, blank=True)


class Announcement(models.Model):
    title = models.CharField(max_length=100)
    body = models.TextField(max_length=3000)
    published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']


class SharedModel(models.Model):
    name = models.CharField(max_length=100)
    model_id = models.CharField(max_length=100)
    endpoint = models.URLField(max_length=500)
    encrypted_key = models.TextField()
    enabled = models.BooleanField(default=True)
    vision = models.BooleanField(default=False)
    per_user_daily = models.PositiveIntegerField(default=20)
    daily_total = models.PositiveIntegerField(default=300)


class SharedUsage(models.Model):
    model = models.ForeignKey(SharedModel, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    day = models.DateField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['model', 'day', 'user'], name='shared_model_daily_user')]


class EmailCode(models.Model):
    email = models.EmailField(primary_key=True)
    digest = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    sent_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)


class MailLimit(models.Model):
    key = models.CharField(max_length=90, primary_key=True)
    until = models.DateTimeField(db_index=True)
    count = models.PositiveSmallIntegerField(default=0)
