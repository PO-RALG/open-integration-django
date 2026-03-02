from django.conf import settings
from django.contrib import admin
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic.base import RedirectView

from gateway.swagger import openapi_schema, swagger_ui
from mediator.views import integration_tester

urlpatterns = [
    path(
        "favicon.ico",
        RedirectView.as_view(
            url=static(settings.EMBLEM_FAVICON_STATIC_PATH),
            permanent=False,
        ),
    ),
    path(
        "admin/mediator/tester/",
        admin.site.admin_view(integration_tester),
        name="admin-mediator-tester",
    ),
    path("api/docs/openapi.json", openapi_schema, name="openapi-schema"),
    path("api/docs/swagger/", swagger_ui, name="swagger-ui"),
    path("api/notification/", include("notification.urls")),
    path("admin/", admin.site.urls),
    path("", include("mediator.urls")),
]
