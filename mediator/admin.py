from django.contrib import admin
from django.utils.html import format_html

from .models import Channel, Client, ExternalSystemRegistration, Mediator, Transaction


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "client_id", "is_active", "updated_at")
    search_fields = ("name", "client_id")
    list_filter = ("is_active",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Mediator)
class MediatorAdmin(admin.ModelAdmin):
    list_display = ("name", "urn", "version", "is_online", "last_heartbeat")
    search_fields = ("name", "urn", "endpoint_url")
    list_filter = ("is_online", "version")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "channel_type",
        "mediator",
        "priority",
        "is_active",
        "requires_request_body",
        "request_content_type",
        "display_methods",
    )
    search_fields = ("name", "path_pattern", "mediator__name")
    list_filter = ("channel_type", "is_active", "mediator", "requires_request_body")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "Routing",
            {
                "fields": (
                    "name",
                    "mediator",
                    "channel_type",
                    "priority",
                    "is_active",
                    "path_pattern",
                    "methods",
                    "description",
                ),
                "classes": ("tab",),
            },
        ),
        (
            "Request Body",
            {
                "fields": (
                    "requires_request_body",
                    "request_content_type",
                    "request_body_example",
                    "request_body_schema",
                ),
                "classes": ("tab",),
            },
        ),
        (
            "Audit",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("tab",),
            },
        ),
    )

    @admin.display(description="Methods")
    def display_methods(self, obj: Channel) -> str:
        if not obj.methods:
            return "*"
        return ", ".join(m.upper() for m in obj.methods)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "correlation_id",
        "status_badge",
        "request_method",
        "response_status_code",
        "channel",
        "client",
        "started_at",
    )
    search_fields = (
        "correlation_id",
        "request_url",
        "channel__name",
        "client__name",
    )
    list_filter = ("status", "request_method", "started_at")
    readonly_fields = ("correlation_id", "started_at")

    @admin.display(description="Status")
    def status_badge(self, obj: Transaction) -> str:
        colors = {
            Transaction.Status.PENDING: "#6c757d",
            Transaction.Status.PROCESSING: "#0d6efd",
            Transaction.Status.SUCCESSFUL: "#198754",
            Transaction.Status.FAILED: "#dc3545",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="font-weight:600;color:{}">{}</span>',
            color,
            obj.get_status_display(),
        )


@admin.register(ExternalSystemRegistration)
class ExternalSystemAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "scope",
        "api_code",
        "push_code",
        "is_active",
        "updated_at",
    )
    search_fields = ("organization", "channel__name", "api_code", "push_code")
    list_filter = ("is_active", "channel")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "Registration",
            {
                "fields": (
                    "organization",
                    "channel",
                    "api_code",
                    "push_code",
                    "is_active",
                )
            },
        ),
        (
            "Audit",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description="Scope")
    def scope(self, obj: ExternalSystemRegistration) -> str:
        if obj.channel is None:
            return "Global (Pure ESB)"
        return obj.channel.name
