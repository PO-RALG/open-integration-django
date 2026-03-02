import json
import urllib.error
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management import call_command
from django.db import IntegrityError
from django.templatetags.static import static
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import Channel, Client, ExternalSystemRegistration, Mediator, Transaction


class DummyUpstreamResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self._status = status
        self._body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def getcode(self):
        return self._status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class MediatorEndpointTests(TestCase):
    def test_favicon_redirect(self):
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], static(settings.EMBLEM_FAVICON_STATIC_PATH))

    def test_root_endpoint(self):
        response = self.client.get(reverse("mediator:root"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["service"],
            "Tanzania Teachers' Professional Board Mediator",
        )

    def test_health_endpoint(self):
        response = self.client.get(reverse("mediator:health"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("timestamp", payload)
        self.assertIn("services", payload)
        self.assertIn("database", payload["services"])
        self.assertIn("rabbitmq", payload["services"])

    def test_swagger_documentation_endpoints(self):
        schema_response = self.client.get("/api/docs/openapi.json")
        self.assertEqual(schema_response.status_code, 200)
        schema_payload = schema_response.json()
        self.assertEqual(schema_payload["openapi"], "3.0.3")
        self.assertIn("/health/", schema_payload["paths"])
        self.assertIn("/api/notification/emit/", schema_payload["paths"])
        self.assertIn("post", schema_payload["paths"]["/api/notification/emit/"])
        emit_doc = schema_payload["paths"]["/api/notification/emit/"]["post"]
        emit_json_body = emit_doc["requestBody"]["content"]["application/json"]
        self.assertEqual(emit_json_body["schema"]["type"], "object")
        self.assertEqual(
            emit_json_body["example"]["event_code"],
            "ACCOUNT_ROLE_CHANGED",
        )
        self.assertIn("recipients", emit_json_body["example"])
        self.assertIn("/transactions/{correlation_id}/", schema_payload["paths"])

        ui_response = self.client.get("/api/docs/swagger/")
        self.assertEqual(ui_response.status_code, 200)
        self.assertContains(ui_response, "swagger-ui")


class IntegrationTesterViewTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()

    @staticmethod
    def _flatten_response_context(response):
        if response.context is None:
            return {}
        if hasattr(response.context, "flatten"):
            return response.context.flatten()

        flattened = {}
        try:
            for item in response.context:
                if hasattr(item, "flatten"):
                    flattened.update(item.flatten())
                elif isinstance(item, dict):
                    flattened.update(item)
        except TypeError:
            if isinstance(response.context, dict):
                flattened.update(response.context)
        return flattened

    def test_tester_requires_staff_authentication(self):
        response = self.client.get(reverse("mediator:tester"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_admin_tester_requires_staff_authentication(self):
        response = self.client.get(reverse("admin-mediator-tester"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_tester_renders_for_staff_user(self):
        user = self.user_model.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("mediator:tester"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Integration Tester")

        admin_response = self.client.get(reverse("admin-mediator-tester"))
        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, "Integration Tester")

    def test_tester_includes_admin_context_for_jazzmin_layout(self):
        user = self.user_model.objects.create_user(
            username="staff-layout",
            email="staff-layout@example.com",
            password="password",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(user)

        for route_name in ("mediator:tester", "admin-mediator-tester"):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            context = self._flatten_response_context(response)
            self.assertIn("available_apps", context)
            self.assertIn("is_nav_sidebar_enabled", context)
            self.assertIn("site_header", context)


class MediatorModelTests(TestCase):
    def setUp(self):
        self.mediator = Mediator.objects.create(
            name="Core Mediator",
            urn="urn:mediator:core",
            version="1.0.0",
            endpoint_url="https://mediator.example.org",
        )
        self.client_app = Client.objects.create(
            name="Facility A",
            client_id="facility-a",
            client_secret="secret",
        )

    def test_channel_unique_path_per_mediator(self):
        Channel.objects.create(
            name="Patients",
            path_pattern="/patients",
            methods=["get", "post"],
            mediator=self.mediator,
        )

        with self.assertRaises(IntegrityError):
            Channel.objects.create(
                name="Patients Duplicate",
                path_pattern="/patients",
                methods=["get"],
                mediator=self.mediator,
            )

    def test_transaction_creation(self):
        channel = Channel.objects.create(
            name="Encounters",
            path_pattern="/encounters",
            methods=["post"],
            mediator=self.mediator,
        )
        transaction = Transaction.objects.create(
            channel=channel,
            client=self.client_app,
            request_method="POST",
            request_url="https://api.example.org/encounters",
        )

        self.assertEqual(transaction.status, Transaction.Status.PENDING)


class SeederCommandTests(TestCase):
    def test_seed_demo_data_command(self):
        out = StringIO()
        call_command("seed_demo_data", stdout=out)

        self.assertGreaterEqual(Client.objects.filter(client_id__startswith="demo-").count(), 1)
        self.assertGreaterEqual(Mediator.objects.filter(urn__startswith="urn:ttpb:demo:").count(), 1)
        self.assertGreaterEqual(Channel.objects.filter(path_pattern__startswith="/demo/").count(), 1)
        self.assertGreaterEqual(
            Transaction.objects.filter(request_url__contains="/demo/").count(),
            1,
        )

    def test_seed_demo_data_uses_env_overrides(self):
        out = StringIO()
        with patch.dict(
            "os.environ",
            {
                "DEMO_SEED_CLIENTS_JSON": json.dumps(
                    [
                        {
                            "client_id": "demo-env-client",
                            "name": "Demo Env Client",
                            "client_secret": "env-secret",
                            "allowed_ips": ["127.0.0.1"],
                            "is_active": True,
                        }
                    ]
                ),
                "DEMO_SEED_MEDIATORS_JSON": json.dumps(
                    [
                        {
                            "urn": "urn:ttpb:demo:env",
                            "name": "Demo Env Mediator",
                            "version": "2.0.0",
                            "endpoint_url": "https://env-mediator.example.org/api",
                            "is_online": True,
                        }
                    ]
                ),
                "DEMO_SEED_CHANNELS_JSON": json.dumps(
                    [
                        {
                            "name": "Demo Env Channel",
                            "description": "Channel from env configuration",
                            "path_pattern": "/demo/env/ping",
                            "methods": ["get"],
                            "requires_request_body": False,
                            "request_content_type": "",
                            "request_body_example": "",
                            "channel_type": "https",
                            "priority": 1,
                            "is_active": True,
                            "mediator_urn": "urn:ttpb:demo:env",
                        }
                    ]
                ),
                "DEMO_SEED_REQUEST_BASE_URL": "http://openhim.local:8087",
            },
            clear=False,
        ):
            call_command(
                "seed_demo_data",
                "--reset",
                "--transactions-per-channel",
                "1",
                stdout=out,
            )

        self.assertTrue(Client.objects.filter(client_id="demo-env-client").exists())
        self.assertTrue(Mediator.objects.filter(urn="urn:ttpb:demo:env").exists())
        self.assertTrue(Channel.objects.filter(name="Demo Env Channel").exists())
        tx = Transaction.objects.get(request_url="http://openhim.local:8087/demo/env/ping")
        self.assertEqual(tx.request_method, "GET")


@override_settings(MEDIATOR_MODE="hybrid")
class ProxyLayerTests(TestCase):
    def setUp(self):
        self.client_record = Client.objects.create(
            name="Demo Integrator",
            client_id="demo-integrator",
            client_secret="demo-secret",
            allowed_ips=["127.0.0.1"],
            is_active=True,
        )
        self.mediator = Mediator.objects.create(
            name="Forwarding Mediator",
            urn="urn:mediator:forwarding",
            version="1.0.0",
            endpoint_url="https://upstream.example.org/api",
            is_online=True,
        )
        self.channel = Channel.objects.create(
            name="Teacher Submit",
            path_pattern="/integrations/teachers",
            methods=["post"],
            requires_request_body=True,
            request_content_type="application/json",
            mediator=self.mediator,
            is_active=True,
            priority=1,
        )

    def _auth_headers(self):
        return {
            "HTTP_X_CLIENT_ID": self.client_record.client_id,
            "HTTP_X_CLIENT_SECRET": self.client_record.client_secret,
        }

    def _auth_headers_with_org(self, organization):
        headers = self._auth_headers()
        headers["HTTP_X_ORGANIZATION"] = organization
        return headers

    def test_proxy_requires_authentication(self):
        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_proxy_rejects_invalid_credentials(self):
        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
            HTTP_X_CLIENT_ID="demo-integrator",
            HTTP_X_CLIENT_SECRET="wrong-secret",
        )
        self.assertEqual(response.status_code, 403)

    def test_proxy_rejects_missing_required_body(self):
        response = self.client.post(
            "/integrations/teachers",
            data="",
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 400)
        tx = Transaction.objects.latest("started_at")
        self.assertEqual(tx.status, Transaction.Status.FAILED)
        self.assertIn("requires request body", tx.error_message.lower())

    def test_proxy_rejects_wrong_content_type(self):
        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="text/plain",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 415)
        tx = Transaction.objects.latest("started_at")
        self.assertEqual(tx.status, Transaction.Status.FAILED)
        self.assertIn("unsupported content type", tx.error_message.lower())

    @patch("mediator.views.urllib.request.urlopen")
    def test_proxy_forwards_and_logs_transaction(self, mock_urlopen):
        mock_urlopen.return_value = DummyUpstreamResponse(
            status=201,
            body=b"{\"ok\": true}",
            headers={"Content-Type": "application/json", "X-Upstream": "demo"},
        )

        response = self.client.post(
            "/integrations/teachers?sync=true",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["X-Upstream"], "demo")
        self.assertIn("X-Correlation-Id", response)

        tx = Transaction.objects.latest("started_at")
        self.assertEqual(tx.status, Transaction.Status.SUCCESSFUL)
        self.assertEqual(tx.channel, self.channel)
        self.assertEqual(tx.client, self.client_record)
        self.assertIn("/integrations/teachers?sync=true", tx.request_url)

        call_args = mock_urlopen.call_args
        self.assertEqual(call_args.kwargs["timeout"], settings.MEDIATOR_FORWARD_TIMEOUT)
        self.assertIn(
            "/integrations/teachers?sync=true",
            call_args.args[0].full_url,
        )

    @patch("mediator.views.urllib.request.urlopen")
    def test_proxy_handles_upstream_http_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://upstream.example.org/api/integrations/teachers",
            code=500,
            msg="Internal Error",
            hdrs={"Content-Type": "application/json"},
            fp=BytesIO(b"{\"error\":\"upstream\"}"),
        )

        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, 500)
        tx = Transaction.objects.latest("started_at")
        self.assertEqual(tx.status, Transaction.Status.FAILED)
        self.assertEqual(tx.response_status_code, 500)

    @patch("mediator.views.urllib.request.urlopen")
    def test_proxy_handles_upstream_connection_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")

        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, 502)
        tx = Transaction.objects.latest("started_at")
        self.assertEqual(tx.status, Transaction.Status.FAILED)

    def test_proxy_requires_organization_when_channel_has_external_registration(self):
        ExternalSystemRegistration.objects.create(
            channel=self.channel,
            organization="MOEST",
            api_code="MOEST-API-001",
            push_code=None,
            is_active=True,
        )

        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Organization is required", response.json()["error"])

    def test_proxy_rejects_unknown_organization_registration(self):
        ExternalSystemRegistration.objects.create(
            channel=self.channel,
            organization="MOEST",
            api_code="MOEST-API-001",
            push_code=None,
            is_active=True,
        )

        response = self.client.post(
            "/integrations/teachers",
            data="{\"teacher_id\": \"T-1\"}",
            content_type="application/json",
            **self._auth_headers_with_org("NIDA"),
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("No external system registration", response.json()["error"])

    @override_settings(
        GOVESB_TOKEN_URL="https://esb.example.org/oauth/token",
        GOVESB_ENGINE_URL="https://esb.example.org/engine",
        GOVESB_GRANT_TYPE="client_credentials",
        ESB_CLIENT_ID="demo-esb-client",
        ESB_CLIENT_SECRET="demo-esb-secret",
        ESB_REQUEST_DATA_FORMAT="json",
    )
    @patch("mediator.views.DataFormat")
    @patch("mediator.views.ESB")
    def test_proxy_routes_via_esb_when_registration_exists(
        self,
        mock_esb_class,
        mock_data_format,
    ):
        ExternalSystemRegistration.objects.create(
            channel=self.channel,
            organization="NIDA",
            api_code="NIDA-VERIFY-001",
            push_code=None,
            is_active=True,
        )

        mock_data_format.JSON = "json"
        mock_data_format.XML = "xml"

        mock_esb = mock_esb_class.return_value
        mock_esb.request_data.return_value = (
            {"data": {"success": True, "esbBody": {"status": "ok"}}},
            True,
        )

        response = self.client.post(
            "/integrations/teachers",
            data='{"requestdata":{"teacher_id":"T-1"}}',
            content_type="application/json",
            **self._auth_headers_with_org("NIDA"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Correlation-Id", response)

        tx = Transaction.objects.latest("started_at")
        self.assertEqual(tx.status, Transaction.Status.SUCCESSFUL)
        self.assertEqual(tx.response_status_code, 200)
        self.assertIn("esb.example.org/engine", tx.request_url)

        mock_esb.request_data.assert_called_once()
        self.assertFalse(mock_esb.push_data.called)

    @override_settings(
        MEDIATOR_MODE="pure_esb",
        GOVESB_TOKEN_URL="https://esb.example.org/oauth/token",
        GOVESB_ENGINE_URL="https://esb.example.org/engine",
        GOVESB_GRANT_TYPE="client_credentials",
        ESB_CLIENT_ID="demo-esb-client",
        ESB_CLIENT_SECRET="demo-esb-secret",
        ESB_REQUEST_DATA_FORMAT="json",
    )
    @patch("mediator.views.DataFormat")
    @patch("mediator.views.ESB")
    def test_pure_esb_mode_bypasses_channel_matching(
        self,
        mock_esb_class,
        mock_data_format,
    ):
        ExternalSystemRegistration.objects.create(
            channel=None,
            organization="NIDA",
            api_code="NIDA-VERIFY-001",
            push_code=None,
            is_active=True,
        )

        mock_data_format.JSON = "json"
        mock_data_format.XML = "xml"
        mock_esb = mock_esb_class.return_value
        mock_esb.request_data.return_value = ({"data": {"success": True}}, True)

        response = self.client.post(
            "/any/path/not-configured/in-channel",
            data='{"requestdata":{"nin":"123"}}',
            content_type="application/json",
            **self._auth_headers_with_org("NIDA"),
        )

        self.assertEqual(response.status_code, 200)
        tx = Transaction.objects.latest("started_at")
        self.assertIsNone(tx.channel)
        self.assertEqual(tx.status, Transaction.Status.SUCCESSFUL)

    @override_settings(MEDIATOR_MODE="pure_esb")
    def test_pure_esb_mode_requires_registration(self):
        response = self.client.post(
            "/any/path/not-configured/in-channel",
            data='{"requestdata":{"nin":"123"}}',
            content_type="application/json",
            **self._auth_headers_with_org("NIDA"),
        )
        self.assertEqual(response.status_code, 404)

    @patch("mediator.views._submit_async_transaction")
    @patch("mediator.views.urllib.request.urlopen")
    def test_proxy_accepts_async_request_and_exposes_status_endpoint(
        self,
        mock_urlopen,
        mock_submit_async,
    ):
        mock_urlopen.return_value = DummyUpstreamResponse(
            status=202,
            body=b'{"queued":true}',
            headers={"Content-Type": "application/json"},
        )

        mock_submit_async.return_value = None

        response = self.client.post(
            "/integrations/teachers?async=true",
            data='{"teacher_id":"T-1"}',
            content_type="application/json",
            **self._auth_headers(),
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response["Preference-Applied"], "respond-async")
        self.assertIn("X-Correlation-Id", response)

        payload = response.json()
        self.assertTrue(payload["accepted"])
        self.assertIn("status_url", payload)
        self.assertTrue(payload["status_url"].startswith("/transactions/"))

        tx = Transaction.objects.get(correlation_id=payload["correlation_id"])
        self.assertEqual(tx.status, Transaction.Status.PENDING)

        status_response = self.client.get(payload["status_url"], **self._auth_headers())
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.json()
        self.assertEqual(status_payload["correlation_id"], payload["correlation_id"])
        self.assertIn(
            status_payload["status"],
            {Transaction.Status.PENDING, Transaction.Status.PROCESSING},
        )

    def test_transaction_status_requires_client_authentication(self):
        transaction = Transaction.objects.create(
            channel=self.channel,
            client=self.client_record,
            status=Transaction.Status.PENDING,
            request_method="POST",
            request_url="https://upstream.example.org/api/integrations/teachers",
        )

        response = self.client.get(
            reverse(
                "mediator:transaction-status",
                kwargs={"correlation_id": str(transaction.correlation_id)},
            )
        )
        self.assertEqual(response.status_code, 401)

    def test_transaction_status_is_scoped_to_requesting_client(self):
        other_client = Client.objects.create(
            name="Other Integrator",
            client_id="other-integrator",
            client_secret="other-secret",
            allowed_ips=["127.0.0.1"],
            is_active=True,
        )
        transaction = Transaction.objects.create(
            channel=self.channel,
            client=other_client,
            status=Transaction.Status.PENDING,
            request_method="POST",
            request_url="https://upstream.example.org/api/integrations/teachers",
        )

        response = self.client.get(
            reverse(
                "mediator:transaction-status",
                kwargs={"correlation_id": str(transaction.correlation_id)},
            ),
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
