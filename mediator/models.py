import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Client(models.Model):
    name = models.CharField(max_length=120)
    client_id = models.CharField(max_length=80, unique=True)
    client_secret = models.CharField(max_length=255)
    allowed_ips = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.client_id})"


class Mediator(models.Model):
    name = models.CharField(max_length=120)
    urn = models.CharField(max_length=255, unique=True)
    version = models.CharField(max_length=30, default="1.0.0")
    endpoint_url = models.URLField(max_length=500)
    is_online = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.version})"


class Channel(models.Model):
    class ChannelType(models.TextChoices):
        HTTP = "http", "HTTP"
        HTTPS = "https", "HTTPS"
        TCP = "tcp", "TCP"

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    path_pattern = models.CharField(max_length=255)
    methods = models.JSONField(default=list, blank=True)
    requires_request_body = models.BooleanField(default=False)
    request_content_type = models.CharField(max_length=120, blank=True)
    request_body_example = models.TextField(blank=True)
    request_body_schema = models.JSONField(default=dict, blank=True)
    channel_type = models.CharField(
        max_length=10,
        choices=ChannelType.choices,
        default=ChannelType.HTTP,
    )
    priority = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    is_active = models.BooleanField(default=True)
    mediator = models.ForeignKey(
        Mediator,
        related_name="channels",
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["path_pattern", "mediator"],
                name="uniq_channel_path_per_mediator",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.channel_type.upper()}]"


class ExternalSystemRegistration(models.Model):
    channel = models.ForeignKey(
        Channel,
        related_name="external_system_registrations",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
    )
    organization = models.CharField(max_length=160)
    api_code = models.CharField(max_length=120, null=True, blank=True)
    push_code = models.CharField(max_length=120, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization", "channel__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["channel", "organization"],
                condition=models.Q(channel__isnull=False),
                name="uniq_registration_channel_organization",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(channel__isnull=True),
                name="uniq_registration_org_global",
            ),
        ]

    def __str__(self) -> str:
        if self.channel is None:
            return f"{self.organization} -> Global ESB Route"
        return f"{self.organization} -> {self.channel.name}"


class Transaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCESSFUL = "successful", "Successful"
        FAILED = "failed", "Failed"

    correlation_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    channel = models.ForeignKey(
        Channel,
        related_name="transactions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    client = models.ForeignKey(
        Client,
        related_name="transactions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    request_method = models.CharField(max_length=10)
    request_url = models.URLField(max_length=500)
    request_headers = models.JSONField(default=dict, blank=True)
    request_body = models.TextField(blank=True)
    response_status_code = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(100), MaxValueValidator(599)],
    )
    response_headers = models.JSONField(default=dict, blank=True)
    response_body = models.TextField(blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["status", "started_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.correlation_id} - {self.status}"
