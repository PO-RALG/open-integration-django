from django.urls import path, re_path

from .views import (
    api_root,
    health,
    integration_tester,
    proxy_request,
    transaction_status,
)

app_name = "mediator"

urlpatterns = [
    path("", api_root, name="root"),
    path("health/", health, name="health"),
    path("tester/", integration_tester, name="tester"),
    path(
        "transactions/<uuid:correlation_id>/",
        transaction_status,
        name="transaction-status",
    ),
    re_path(r"^(?P<resource_path>.+)$", proxy_request, name="proxy"),
]
