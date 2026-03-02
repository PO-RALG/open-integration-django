from django.contrib import admin

from .models import (
    DeliveryLog,
    EventCatalog,
    EventRule,
    EventRuleChannel,
    InAppMessage,
    LkActorType,
    LkChannel,
    LkModule,
    LkPriority,
    LkStatus,
    Outbox,
    Template,
    UserPreference,
)


@admin.register(LkModule)
class LkModuleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(LkChannel)
class LkChannelAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(LkStatus)
class LkStatusAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(LkPriority)
class LkPriorityAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "weight", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(LkActorType)
class LkActorTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    search_fields = ("code", "name")
    list_filter = ("is_active",)


@admin.register(EventCatalog)
class EventCatalogAdmin(admin.ModelAdmin):
    list_display = ("code", "module", "is_active", "created_at")
    search_fields = ("code", "description", "module__code", "module__name")
    list_filter = ("is_active", "module")
    readonly_fields = ("created_at",)


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ("event", "channel", "language", "version", "is_active", "created_at")
    search_fields = ("event__code", "channel__code", "language", "subject", "body")
    list_filter = ("is_active", "channel", "language")
    readonly_fields = ("created_at",)


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user_id",
        "actor_type",
        "allow_sms",
        "allow_email",
        "allow_in_app",
        "preferred_lang",
        "updated_at",
    )
    search_fields = ("user_id", "actor_type__code")
    list_filter = ("actor_type", "preferred_lang")
    readonly_fields = ("updated_at",)


@admin.register(EventRule)
class EventRuleAdmin(admin.ModelAdmin):
    list_display = ("event", "recipient_policy", "priority", "is_active", "created_at")
    search_fields = ("event__code", "recipient_policy", "priority__code")
    list_filter = ("is_active", "priority")
    readonly_fields = ("created_at",)


@admin.register(EventRuleChannel)
class EventRuleChannelAdmin(admin.ModelAdmin):
    list_display = ("event_rule", "channel")
    search_fields = ("event_rule__event__code", "channel__code")
    list_filter = ("channel",)


@admin.register(Outbox)
class OutboxAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "event",
        "channel",
        "status",
        "priority",
        "recipient_user_id",
        "attempt_count",
        "next_attempt_at",
        "created_at",
    )
    search_fields = (
        "idempotency_key",
        "event__code",
        "recipient_user_id",
        "to_phone",
        "to_email",
        "entity_type",
        "entity_id",
    )
    list_filter = ("status", "channel", "priority", "recipient_actor_type")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DeliveryLog)
class DeliveryLogAdmin(admin.ModelAdmin):
    list_display = ("outbox", "attempt_no", "status", "provider", "created_at")
    search_fields = ("outbox__id", "provider", "provider_message_id", "error_message")
    list_filter = ("status", "provider")
    readonly_fields = ("created_at",)


@admin.register(InAppMessage)
class InAppMessageAdmin(admin.ModelAdmin):
    list_display = ("user_id", "title", "is_read", "event_code", "created_at")
    search_fields = ("user_id", "title", "body", "event_code", "entity_type", "entity_id")
    list_filter = ("is_read",)
    readonly_fields = ("created_at",)
