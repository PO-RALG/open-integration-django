from django.urls import path

from .views import (
    api_root,
    emit,
    events,
    inbox,
    lookups,
    mark_inbox_read,
    outbox_delivery_logs,
    outbox_list,
    outbox_process,
    rules,
    templates,
    user_preference,
)

app_name = "notification"

urlpatterns = [
    path("", api_root, name="root"),
    path("lookups/", lookups, name="lookups"),
    path("events/", events, name="events"),
    path("rules/", rules, name="rules"),
    path("templates/", templates, name="templates"),
    path(
        "preferences/<str:actor_type_code>/<int:user_id>/",
        user_preference,
        name="user_preference",
    ),
    path("emit/", emit, name="emit"),
    path("outbox/", outbox_list, name="outbox"),
    path("outbox/process/", outbox_process, name="outbox_process"),
    path(
        "outbox/<int:outbox_id>/delivery-logs/",
        outbox_delivery_logs,
        name="outbox_delivery_logs",
    ),
    path("inbox/<int:user_id>/", inbox, name="inbox"),
    path("inbox/<int:message_id>/read/", mark_inbox_read, name="inbox_read"),
]
