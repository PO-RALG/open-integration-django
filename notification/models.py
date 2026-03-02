from django.db import models
from django.db.models import Q
from django.utils import timezone


class LkModule(models.Model):
    id = models.SmallAutoField(primary_key=True)
    code = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_lk_module"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class LkChannel(models.Model):
    id = models.SmallAutoField(primary_key=True)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_lk_channel"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class LkStatus(models.Model):
    id = models.SmallAutoField(primary_key=True)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_lk_status"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class LkPriority(models.Model):
    id = models.SmallAutoField(primary_key=True)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    weight = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_lk_priority"
        ordering = ["-weight", "code"]

    def __str__(self) -> str:
        return f"{self.code} ({self.weight})"


class LkActorType(models.Model):
    id = models.SmallAutoField(primary_key=True)
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_lk_actor_type"
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class EventCatalog(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    module = models.ForeignKey(
        LkModule,
        on_delete=models.PROTECT,
        related_name="events",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notification_event_catalog"
        ordering = ["code"]
        indexes = [
            models.Index(fields=["module", "is_active"], name="notif_event_mod_active_idx"),
        ]

    def __str__(self) -> str:
        return self.code


class Template(models.Model):
    id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(
        EventCatalog,
        to_field="code",
        db_column="event_code",
        on_delete=models.PROTECT,
        related_name="templates",
    )
    channel = models.ForeignKey(
        LkChannel,
        on_delete=models.PROTECT,
        related_name="templates",
    )
    language = models.CharField(max_length=8, default="en")
    subject = models.TextField(blank=True)
    body = models.TextField()
    version = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notification_template"
        ordering = ["event_id", "channel_id", "language", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "channel", "language", "version"],
                name="notif_template_unique_version",
            )
        ]
        indexes = [
            models.Index(
                fields=["event", "channel"],
                condition=Q(is_active=True),
                name="notif_template_active_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.event_id} - {self.channel.code} - {self.language} v{self.version}"


class UserPreference(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.BigIntegerField()
    actor_type = models.ForeignKey(
        LkActorType,
        on_delete=models.PROTECT,
        related_name="user_preferences",
    )
    allow_sms = models.BooleanField(default=True)
    allow_email = models.BooleanField(default=True)
    allow_in_app = models.BooleanField(default=True)
    preferred_lang = models.CharField(max_length=8, default="en")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_user_preference"
        ordering = ["user_id", "actor_type_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_id", "actor_type"],
                name="notif_user_pref_unique_actor",
            )
        ]

    def __str__(self) -> str:
        return f"User {self.user_id} ({self.actor_type.code})"


class EventRule(models.Model):
    id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(
        EventCatalog,
        to_field="code",
        db_column="event_code",
        on_delete=models.PROTECT,
        related_name="rules",
    )
    recipient_policy = models.CharField(max_length=64, default="TEACHER")
    priority = models.ForeignKey(
        LkPriority,
        on_delete=models.PROTECT,
        related_name="event_rules",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notification_event_rule"
        ordering = ["event_id", "-created_at"]
        indexes = [
            models.Index(
                fields=["event"],
                condition=Q(is_active=True),
                name="notif_event_rule_active_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.event_id} ({self.recipient_policy})"


class EventRuleChannel(models.Model):
    id = models.BigAutoField(primary_key=True)
    event_rule = models.ForeignKey(
        EventRule,
        on_delete=models.CASCADE,
        related_name="rule_channels",
    )
    channel = models.ForeignKey(
        LkChannel,
        on_delete=models.PROTECT,
        related_name="event_rule_channels",
    )

    class Meta:
        db_table = "notification_event_rule_channel"
        ordering = ["event_rule_id", "channel_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event_rule", "channel"],
                name="notif_event_rule_channel_unique",
            )
        ]

    def __str__(self) -> str:
        return f"Rule {self.event_rule_id} -> {self.channel.code}"


class Outbox(models.Model):
    id = models.BigAutoField(primary_key=True)
    event = models.ForeignKey(
        EventCatalog,
        to_field="code",
        db_column="event_code",
        on_delete=models.PROTECT,
        related_name="outbox_items",
    )
    channel = models.ForeignKey(
        LkChannel,
        on_delete=models.PROTECT,
        related_name="outbox_items",
    )
    priority = models.ForeignKey(
        LkPriority,
        on_delete=models.PROTECT,
        related_name="outbox_items",
    )
    status = models.ForeignKey(
        LkStatus,
        on_delete=models.PROTECT,
        related_name="outbox_items",
    )
    recipient_user_id = models.BigIntegerField()
    recipient_actor_type = models.ForeignKey(
        LkActorType,
        on_delete=models.PROTECT,
        related_name="outbox_items",
    )
    subject = models.TextField(blank=True)
    body = models.TextField()
    to_phone = models.TextField(blank=True)
    to_email = models.TextField(blank=True)
    entity_type = models.TextField(blank=True)
    entity_id = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    attempt_count = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_outbox"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "next_attempt_at"],
                name="notif_outbox_due_idx",
            ),
            models.Index(
                fields=["recipient_user_id", "-created_at"],
                name="notif_outbox_recipient_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Outbox {self.id} - {self.event_id} - {self.status.code}"


class DeliveryLog(models.Model):
    id = models.BigAutoField(primary_key=True)
    outbox = models.ForeignKey(
        Outbox,
        on_delete=models.CASCADE,
        related_name="delivery_logs",
    )
    attempt_no = models.IntegerField()
    status = models.ForeignKey(
        LkStatus,
        on_delete=models.PROTECT,
        related_name="delivery_logs",
    )
    provider = models.TextField(blank=True)
    provider_message_id = models.TextField(blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notification_delivery_log"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["outbox", "attempt_no"],
                name="notif_delivery_unique_attempt",
            )
        ]
        indexes = [
            models.Index(
                fields=["outbox", "-created_at"],
                name="notif_delivery_outbox_idx",
            )
        ]

    def __str__(self) -> str:
        return f"Outbox {self.outbox_id} attempt {self.attempt_no}"


class InAppMessage(models.Model):
    id = models.BigAutoField(primary_key=True)
    user_id = models.BigIntegerField()
    title = models.TextField(blank=True)
    body = models.TextField()
    event_code = models.CharField(max_length=100, blank=True)
    entity_type = models.TextField(blank=True)
    entity_id = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "notification_in_app_message"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user_id", "is_read", "-created_at"],
                name="notif_inapp_user_unread_idx",
            )
        ]

    def __str__(self) -> str:
        return f"User {self.user_id} in-app message {self.id}"
