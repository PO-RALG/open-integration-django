import base64
import json
import socket
import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, urlencode, urlparse

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.conf import settings
from django.db import close_old_connections, connection
from django.http import HttpResponse, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from gateway.swagger_annotations import swagger_doc

from .models import Channel, Client, ExternalSystemRegistration, Transaction

try:
    from esb_utils.esb import DataFormat, ESB
except Exception:  # pragma: no cover - dependency/runtime import variability
    DataFormat = None
    ESB = None

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
SENSITIVE_HEADERS = {"authorization", "x-client-id", "x-client-secret"}
BOOLEAN_TRUE_VALUES = {"1", "true", "yes", "on"}
TERMINAL_TRANSACTION_STATUSES = {
    Transaction.Status.SUCCESSFUL,
    Transaction.Status.FAILED,
}
UPSTREAM_NETWORK_EXCEPTIONS = (
    urllib.error.URLError,
    TimeoutError,
    socket.timeout,
    ConnectionError,
)


@swagger_doc(
    methods=["get"],
    summary="Mediator API root",
    tags=["Mediator"],
    responses={"200": {"description": "Mediator service metadata"}},
)
def api_root(_request):
    return JsonResponse(
        {
            "service": "Tanzania Teachers' Professional Board Mediator",
            "status": "running",
            "endpoints": {
                "admin": "/admin/",
                "health": "/health/",
                "tester": "/tester/",
                "integration_call": "/integration/call",
                "transaction_status": "/transactions/<correlation_id>/",
            },
        }
    )


def _check_database():
    try:
        connection.ensure_connection()
        return {"ok": True}
    except Exception as exc:  # pragma: no cover - depends on runtime DB state
        return {"ok": False, "error": str(exc)}


def _check_rabbitmq():
    parsed_url = urlparse(settings.RABBITMQ_URL)
    host = parsed_url.hostname or "localhost"
    port = parsed_url.port or 5672

    try:
        with socket.create_connection((host, port), timeout=2):
            return {"ok": True, "host": host, "port": port}
    except OSError as exc:  # pragma: no cover - depends on runtime broker state
        return {"ok": False, "host": host, "port": port, "error": str(exc)}


@swagger_doc(
    methods=["get"],
    summary="Health check",
    tags=["Mediator"],
    responses={"200": {"description": "Health probe payload"}},
)
def health(_request):
    if settings.ENABLE_HEALTH_DEPENDENCY_CHECKS:
        database = _check_database()
        rabbitmq = _check_rabbitmq()
        overall_ok = database["ok"] and rabbitmq["ok"]
    else:
        database = {"ok": True, "checked": False}
        rabbitmq = {"ok": True, "checked": False}
        overall_ok = True

    return JsonResponse(
        {
            "ok": overall_ok,
            "timestamp": timezone.now().isoformat(),
            "services": {
                "database": database,
                "rabbitmq": rabbitmq,
            },
        }
    )


@staff_member_required
def integration_tester(request):
    channels = (
        Channel.objects.select_related("mediator")
        .filter(is_active=True)
        .order_by("priority", "name")
    )
    clients = Client.objects.filter(is_active=True).order_by("name")

    channel_payload = [
        {
            "name": channel.name,
            "path_pattern": channel.path_pattern,
            "methods": channel.methods or [],
            "mediator_name": channel.mediator.name,
            "priority": channel.priority,
            "requires_request_body": channel.requires_request_body,
            "request_content_type": channel.request_content_type,
            "request_body_example": channel.request_body_example,
        }
        for channel in channels
    ]

    client_payload = [
        {
            "name": client.name,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
        }
        for client in clients
    ]

    context = {
        **admin.site.each_context(request),
        "title": "Integration Tester",
        "channels_payload": channel_payload,
        "clients_payload": client_payload,
    }
    request.current_app = admin.site.name
    return TemplateResponse(request, "mediator/tester.html", context)


def _extract_client_ip(request):
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _extract_credentials(request):
    client_id = request.headers.get("X-Client-Id")
    client_secret = request.headers.get("X-Client-Secret")
    if client_id and client_secret:
        return client_id, client_secret

    authorization = request.headers.get("Authorization", "")
    if authorization.lower().startswith("basic "):
        encoded = authorization.split(" ", 1)[1].strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)
            return username, password
        except Exception:
            return None, None

    return None, None


def _authenticate_client(request):
    client_id, client_secret = _extract_credentials(request)
    if not client_id or not client_secret:
        response = JsonResponse(
            {"ok": False, "error": "Authentication required"},
            status=401,
        )
        response["WWW-Authenticate"] = 'Basic realm="TTPB Mediator"'
        return None, response

    client = Client.objects.filter(client_id=client_id, is_active=True).first()
    if not client or client.client_secret != client_secret:
        return None, JsonResponse(
            {"ok": False, "error": "Invalid client credentials"},
            status=403,
        )

    source_ip = _extract_client_ip(request)
    if client.allowed_ips and source_ip not in client.allowed_ips:
        return None, JsonResponse(
            {"ok": False, "error": "Client IP is not allowed"},
            status=403,
        )

    return client, None


def _path_matches(pattern, path):
    normalized_pattern = pattern if pattern.startswith("/") else f"/{pattern}"
    if normalized_pattern.endswith("*"):
        return path.startswith(normalized_pattern[:-1])
    return path == normalized_pattern


def _method_allowed(channel, method):
    if not channel.methods:
        return True
    allowed = {m.lower() for m in channel.methods}
    return method.lower() in allowed


def _match_channel(request):
    path = request.path
    method = request.method

    channels = list(Channel.objects.select_related("mediator").filter(is_active=True))
    path_matches = [channel for channel in channels if _path_matches(channel.path_pattern, path)]
    if not path_matches:
        return None, JsonResponse(
            {"ok": False, "error": "No active channel for this path"},
            status=404,
        )

    method_matches = [channel for channel in path_matches if _method_allowed(channel, method)]
    if not method_matches:
        return None, JsonResponse(
            {"ok": False, "error": "Method not allowed for this channel"},
            status=405,
        )

    online_matches = [channel for channel in method_matches if channel.mediator.is_online]
    if not online_matches:
        return None, JsonResponse(
            {"ok": False, "error": "Matched mediator is offline"},
            status=503,
        )

    channel = sorted(online_matches, key=lambda c: c.priority)[0]
    return channel, None


def _truncate_text(value):
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = value

    max_len = settings.TRANSACTION_BODY_MAX_LENGTH
    if len(text) > max_len:
        return text[:max_len] + "...<truncated>"
    return text


def _normalize_content_type(value):
    return value.split(";", 1)[0].strip().lower()


def _build_target_url(channel, request):
    base_url = channel.mediator.endpoint_url.rstrip("/")
    url = f"{base_url}{request.path}"
    raw_query = request.META.get("QUERY_STRING", "")
    if raw_query:
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(raw_query, keep_blank_values=True)
            if key.lower() != "async"
        ]
        if filtered_query:
            url = f"{url}?{urlencode(filtered_query, doseq=True)}"
    return url


def _build_target_url_from_parts(channel, path, query_params):
    base_url = channel.mediator.endpoint_url.rstrip("/")
    normalized_path = str(path or "").strip()
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    url = f"{base_url}{normalized_path}"

    filtered_query = []
    if isinstance(query_params, dict):
        for key, value in query_params.items():
            if str(key).lower() == "async":
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    filtered_query.append((str(key), str(item)))
            else:
                filtered_query.append((str(key), str(value)))

    if filtered_query:
        url = f"{url}?{urlencode(filtered_query, doseq=True)}"
    return url


def _proxy_headers_from_mapping(source_headers, correlation_id):
    headers = {}
    for key, value in source_headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered in SENSITIVE_HEADERS:
            continue
        if lowered in {"host", "content-length"}:
            continue
        headers[key] = value

    headers["X-Correlation-Id"] = str(correlation_id)
    return headers


def _proxy_headers_from_request(request, correlation_id):
    return _proxy_headers_from_mapping(request.headers, correlation_id)


def _loggable_request_headers(request):
    cleaned = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in {"x-client-secret", "authorization"}:
            cleaned[key] = "***"
        else:
            cleaned[key] = value
    return cleaned


def _safe_response_headers(headers):
    payload = {}
    for key, value in headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        payload[key] = value
    return payload


def _looks_true(value):
    return str(value).strip().lower() in BOOLEAN_TRUE_VALUES


def _is_async_request(request):
    query_value = request.GET.get("async")
    if query_value is not None and query_value != "":
        return _looks_true(query_value)

    prefer = request.headers.get("Prefer", "")
    if "respond-async" in prefer.lower():
        return True

    return _looks_true(request.headers.get("X-Async", ""))


def _transaction_status_payload(transaction):
    return {
        "ok": True,
        "correlation_id": str(transaction.correlation_id),
        "status": transaction.status,
        "is_terminal": transaction.status in TERMINAL_TRANSACTION_STATUSES,
        "request_method": transaction.request_method,
        "request_url": transaction.request_url,
        "response_status_code": transaction.response_status_code,
        "response_headers": transaction.response_headers or {},
        "response_body": transaction.response_body,
        "error_message": transaction.error_message,
        "started_at": transaction.started_at.isoformat() if transaction.started_at else None,
        "completed_at": (
            transaction.completed_at.isoformat() if transaction.completed_at else None
        ),
    }


def _process_transaction_in_background(
    transaction_id,
    target_url,
    request_method,
    request_body,
    proxy_headers,
    esb_mode=None,
    esb_code=None,
    esb_payload=None,
):
    close_old_connections()
    try:
        transaction = Transaction.objects.filter(id=transaction_id).first()
        if transaction is None:
            return

        transaction.status = Transaction.Status.PROCESSING
        transaction.error_message = ""
        transaction.save(update_fields=["status", "error_message"])

        if esb_mode is not None and esb_code is not None and esb_payload is not None:
            try:
                esb_client = _build_esb_client()
                data_format = _resolve_esb_data_format()
                if esb_mode == "push":
                    esb_result = esb_client.push_data(
                        push_code=esb_code,
                        req_body=esb_payload,
                        data_format=data_format,
                    )
                    if isinstance(esb_result, tuple):
                        response_data, success = esb_result
                    else:
                        response_data, success = esb_result, True
                else:
                    response_data, success = esb_client.request_data(
                        api_code=esb_code,
                        req_body=esb_payload,
                        data_format=data_format,
                    )
            except ValueError as exc:
                transaction.status = Transaction.Status.FAILED
                transaction.response_status_code = 400
                transaction.error_message = str(exc)
                transaction.completed_at = timezone.now()
                transaction.save(
                    update_fields=[
                        "status",
                        "response_status_code",
                        "error_message",
                        "completed_at",
                    ]
                )
                return
            except Exception as exc:  # pragma: no cover - depends on runtime networking
                transaction.status = Transaction.Status.FAILED
                transaction.response_status_code = 502
                transaction.error_message = str(exc)
                transaction.completed_at = timezone.now()
                transaction.save(
                    update_fields=[
                        "status",
                        "response_status_code",
                        "error_message",
                        "completed_at",
                    ]
                )
                return

            _, response_text = _normalize_esb_response(response_data)
            response_status_code = 200 if success else 502
            transaction.response_status_code = response_status_code
            transaction.response_headers = {"Content-Type": "application/json"}
            transaction.response_body = _truncate_text(response_text)
            transaction.completed_at = timezone.now()
            if success:
                transaction.status = Transaction.Status.SUCCESSFUL
                transaction.error_message = ""
            else:
                transaction.status = Transaction.Status.FAILED
                transaction.error_message = "National ESB returned an unsuccessful response"
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "response_headers",
                    "response_body",
                    "error_message",
                    "completed_at",
                ]
            )
            return

        upstream_request = urllib.request.Request(
            target_url,
            data=(request_body if request_body else None),
            headers=proxy_headers,
            method=request_method,
        )

        response_status_code = None
        response_headers = {}
        response_body = b""

        try:
            with urllib.request.urlopen(
                upstream_request,
                timeout=settings.MEDIATOR_FORWARD_TIMEOUT,
            ) as upstream_response:
                response_status_code = upstream_response.getcode()
                response_headers = dict(upstream_response.headers.items())
                response_body = upstream_response.read()
        except urllib.error.HTTPError as exc:
            response_status_code = exc.code
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            response_body = exc.read()
        except UPSTREAM_NETWORK_EXCEPTIONS as exc:
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 502
            transaction.error_message = str(exc)
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return

        transaction.response_status_code = response_status_code
        transaction.response_headers = _safe_response_headers(response_headers)
        transaction.response_body = _truncate_text(response_body)
        transaction.completed_at = timezone.now()

        if 200 <= response_status_code < 400:
            transaction.status = Transaction.Status.SUCCESSFUL
            transaction.error_message = ""
        else:
            transaction.status = Transaction.Status.FAILED
            transaction.error_message = f"Upstream returned HTTP {response_status_code}"

        transaction.save(
            update_fields=[
                "status",
                "response_status_code",
                "response_headers",
                "response_body",
                "error_message",
                "completed_at",
            ]
        )
    finally:
        close_old_connections()


def _submit_async_transaction(**worker_kwargs):
    worker = threading.Thread(
        target=_process_transaction_in_background,
        kwargs=worker_kwargs,
        daemon=True,
    )
    worker.start()
    return worker


def _is_pure_esb_mode():
    return (settings.MEDIATOR_MODE or "hybrid").strip().lower() == "pure_esb"


def _resolve_external_registration(request, channel=None, organization_override=""):
    registrations = ExternalSystemRegistration.objects.filter(is_active=True)
    if channel is not None:
        registrations = registrations.filter(channel=channel)
        if not registrations.exists():
            return None, None

    organization = (
        str(organization_override).strip()
        or request.headers.get("X-Organization")
        or request.GET.get("organization", "")
    ).strip()
    if not organization:
        return None, JsonResponse(
            {
                "ok": False,
                "error": "Organization is required for ESB routing",
                "detail": "Provide X-Organization header or ?organization=...",
            },
            status=400,
        )

    if channel is None:
        global_matches = registrations.filter(
            channel__isnull=True,
            organization__iexact=organization,
        )
        if global_matches.count() == 1:
            return global_matches.first(), None
        if global_matches.count() > 1:
            return None, JsonResponse(
                {
                    "ok": False,
                    "error": "Ambiguous global registration for this organization",
                    "organization": organization,
                },
                status=409,
            )

    scoped_matches = registrations.filter(organization__iexact=organization)
    if scoped_matches.count() > 1 and channel is None:
        return None, JsonResponse(
            {
                "ok": False,
                "error": "Ambiguous registration for this organization",
                "organization": organization,
                "detail": (
                    "Create one global registration (channel empty) or "
                    "use hybrid mode routing by channel."
                ),
            },
            status=409,
        )

    registration = scoped_matches.first()
    if registration is None:
        return None, JsonResponse(
            {
                "ok": False,
                "error": "No external system registration for this organization",
                "organization": organization,
            },
            status=404,
        )
    return registration, None


def _build_esb_client():
    if ESB is None:
        raise RuntimeError(
            "ESB utility is unavailable. Install required dependencies and verify esb_utils."
        )

    required = {
        "GOVESB_TOKEN_URL": settings.GOVESB_TOKEN_URL,
        "GOVESB_ENGINE_URL": settings.GOVESB_ENGINE_URL,
        "GOVESB_GRANT_TYPE": settings.GOVESB_GRANT_TYPE,
        "ESB_CLIENT_ID": settings.ESB_CLIENT_ID,
        "ESB_CLIENT_SECRET": settings.ESB_CLIENT_SECRET,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ValueError(f"Missing ESB settings: {', '.join(missing)}")

    return ESB(
        auth_url=settings.GOVESB_TOKEN_URL,
        request_url=settings.GOVESB_ENGINE_URL,
        grant_type=settings.GOVESB_GRANT_TYPE,
        client_id=settings.ESB_CLIENT_ID,
        client_secret=settings.ESB_CLIENT_SECRET,
    )


def _build_esb_payload(request_body):
    if len(request_body) == 0:
        return {"requestdata": {}}

    try:
        decoded_payload = json.loads(request_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("ESB forwarding expects a valid JSON request body")

    if not isinstance(decoded_payload, dict):
        raise ValueError("ESB forwarding expects a JSON object body")

    requestdata = decoded_payload.get("requestdata", decoded_payload)
    if not isinstance(requestdata, dict):
        raise ValueError("ESB forwarding requires 'requestdata' to be an object")
    return {"requestdata": requestdata}


def _resolve_esb_mode_and_code(request, registration, esb_mode_override=""):
    preferred_mode = (
        str(esb_mode_override).strip()
        or request.headers.get("X-ESB-Mode")
        or request.GET.get("esb_mode", "")
    ).strip().lower()

    if preferred_mode not in {"", "normal", "push"}:
        raise ValueError("Unsupported ESB mode. Allowed values: normal, push")

    if preferred_mode == "push":
        if not registration.push_code:
            raise ValueError("This organization has no push_code configured")
        return "push", registration.push_code

    if preferred_mode == "normal":
        if not registration.api_code:
            raise ValueError("This organization has no api_code configured")
        return "normal", registration.api_code

    if registration.api_code:
        return "normal", registration.api_code
    if registration.push_code:
        return "push", registration.push_code
    raise ValueError("Registration must have at least one code (api_code or push_code)")


def _resolve_esb_data_format():
    fmt = (settings.ESB_REQUEST_DATA_FORMAT or "json").strip().lower()
    if DataFormat is None:
        raise RuntimeError("ESB data format enum is unavailable")
    if fmt == "xml":
        return DataFormat.XML
    return DataFormat.JSON


def _normalize_esb_response(response):
    if hasattr(response, "get_body"):
        response = response.get_body()

    if isinstance(response, bytes):
        response = response.decode("utf-8", errors="replace")

    if isinstance(response, (dict, list)):
        return response, json.dumps(response, ensure_ascii=False)

    if isinstance(response, str):
        try:
            parsed = json.loads(response)
            return parsed, response
        except json.JSONDecodeError:
            return {"raw_response": response}, response

    normalized = str(response)
    return {"raw_response": normalized}, normalized


def _parse_json_object_body(request):
    try:
        payload = json.loads((request.body or b"{}").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Invalid JSON body")

    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _as_request_body_bytes(value, content_type):
    if value is None:
        return b""
    if isinstance(value, (dict, list)):
        return json.dumps(value).encode("utf-8")
    if isinstance(value, str):
        return value.encode("utf-8")
    if content_type and "json" in content_type.lower():
        return json.dumps(value).encode("utf-8")
    return str(value).encode("utf-8")


@swagger_doc(
    methods=["post"],
    summary="Call integration by channel id",
    description=(
        "Wrapper endpoint to invoke a configured channel using channel_id and body payload."
    ),
    tags=["Mediator Proxy"],
    security=[{"BasicAuth": []}, {"XClientId": [], "XClientSecret": []}],
    request_body={
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "integer"},
                        "method": {"type": "string"},
                        "path": {"type": "string"},
                        "query": {"type": "object"},
                        "body": {},
                        "headers": {"type": "object"},
                        "content_type": {"type": "string"},
                        "async": {"type": "boolean"},
                        "organization": {"type": "string"},
                        "esb_mode": {"type": "string", "enum": ["normal", "push"]},
                    },
                    "required": ["channel_id", "body"],
                },
                "example": {
                    "channel_id": 1,
                    "method": "POST",
                    "path": "/demo/teachers/license/renew",
                    "query": {"source": "lms", "async": False},
                    "body": {"teacher_id": "TTPB-DEMO-1001", "renewal_year": 2026},
                    "headers": {"X-Request-Source": "integration-wrapper"},
                    "content_type": "application/json",
                    "organization": "MOEST",
                },
            }
        },
    },
    responses={
        "200": {"description": "Synchronous proxy success"},
        "202": {"description": "Accepted for asynchronous processing"},
        "400": {"description": "Validation error"},
        "401": {"description": "Authentication required"},
        "403": {"description": "Forbidden"},
        "404": {"description": "Channel not found"},
        "405": {"description": "Method not allowed"},
        "415": {"description": "Unsupported media type"},
        "502": {"description": "Upstream or ESB relay failure"},
        "503": {"description": "Mediator offline"},
    },
)
@csrf_exempt
def integration_call(request):
    if request.method.upper() != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)

    client, auth_error = _authenticate_client(request)
    if auth_error is not None:
        return auth_error

    try:
        payload = _parse_json_object_body(request)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    channel_id = payload.get("channel_id")
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "channel_id must be an integer"}, status=400)

    channel = (
        Channel.objects.select_related("mediator")
        .filter(id=channel_id, is_active=True)
        .first()
    )
    if channel is None:
        return JsonResponse({"ok": False, "error": "Channel not found"}, status=404)
    if not channel.mediator.is_online:
        return JsonResponse({"ok": False, "error": "Matched mediator is offline"}, status=503)

    method = str(
        payload.get("method")
        or (channel.methods[0] if channel.methods else "POST")
    ).strip().upper()
    if channel.methods and method.lower() not in {m.lower() for m in channel.methods}:
        return JsonResponse({"ok": False, "error": "Method not allowed for this channel"}, status=405)

    resource_path = str(payload.get("path") or channel.path_pattern).strip()
    if not resource_path:
        return JsonResponse({"ok": False, "error": "path cannot be empty"}, status=400)
    if not resource_path.startswith("/"):
        resource_path = f"/{resource_path}"

    query = payload.get("query", {})
    if query is None:
        query = {}
    if not isinstance(query, dict):
        return JsonResponse({"ok": False, "error": "query must be a JSON object"}, status=400)

    outbound_content_type = str(
        payload.get("content_type")
        or request.headers.get("Content-Type")
        or "application/json"
    ).strip()
    request_body = _as_request_body_bytes(payload.get("body"), outbound_content_type)

    if (
        channel.requires_request_body
        and method not in {"GET", "HEAD", "OPTIONS"}
        and len(request_body) == 0
    ):
        correlation_id = Transaction._meta.get_field("correlation_id").default()
        target_url = _build_target_url_from_parts(channel, resource_path, query)
        Transaction.objects.create(
            correlation_id=correlation_id,
            channel=channel,
            client=client,
            status=Transaction.Status.FAILED,
            request_method=method,
            request_url=target_url,
            request_headers=_loggable_request_headers(request),
            request_body="",
            error_message="Channel requires request body",
            completed_at=timezone.now(),
        )
        return JsonResponse(
            {
                "ok": False,
                "error": "Request body is required for this channel",
                "correlation_id": str(correlation_id),
            },
            status=400,
        )

    if channel.request_content_type and len(request_body) > 0:
        incoming_content_type = _normalize_content_type(outbound_content_type)
        expected_content_type = _normalize_content_type(channel.request_content_type)
        if incoming_content_type != expected_content_type:
            correlation_id = Transaction._meta.get_field("correlation_id").default()
            target_url = _build_target_url_from_parts(channel, resource_path, query)
            Transaction.objects.create(
                correlation_id=correlation_id,
                channel=channel,
                client=client,
                status=Transaction.Status.FAILED,
                request_method=method,
                request_url=target_url,
                request_headers=_loggable_request_headers(request),
                request_body=_truncate_text(request_body),
                error_message=(
                    "Unsupported content type. "
                    f"Expected {channel.request_content_type}"
                ),
                completed_at=timezone.now(),
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "Unsupported content type for this channel. "
                        f"Expected {channel.request_content_type}"
                    ),
                    "correlation_id": str(correlation_id),
                },
                status=415,
            )

    organization = payload.get("organization", "")
    registration, registration_error = _resolve_external_registration(
        request,
        channel,
        organization_override=organization,
    )
    if registration_error is not None:
        return registration_error

    target_url = (
        settings.GOVESB_ENGINE_URL.rstrip("/") or "esb://unconfigured"
        if registration is not None
        else _build_target_url_from_parts(channel, resource_path, query)
    )
    async_requested = _looks_true(payload.get("async", False))
    correlation_id = Transaction._meta.get_field("correlation_id").default()

    transaction = Transaction.objects.create(
        correlation_id=correlation_id,
        channel=channel,
        client=client,
        status=Transaction.Status.PENDING if async_requested else Transaction.Status.PROCESSING,
        request_method=method,
        request_url=target_url,
        request_headers=_loggable_request_headers(request),
        request_body=_truncate_text(request_body),
    )

    provided_headers = payload.get("headers", {})
    if provided_headers is None:
        provided_headers = {}
    if not isinstance(provided_headers, dict):
        return JsonResponse({"ok": False, "error": "headers must be a JSON object"}, status=400)
    source_headers = {str(key): str(value) for key, value in provided_headers.items()}
    source_headers.setdefault("Content-Type", outbound_content_type)
    proxy_headers = _proxy_headers_from_mapping(source_headers, transaction.correlation_id)

    if async_requested:
        esb_mode = None
        esb_code = None
        esb_payload = None

        if registration is not None:
            try:
                esb_payload = _build_esb_payload(request_body)
                esb_mode, esb_code = _resolve_esb_mode_and_code(
                    request,
                    registration,
                    esb_mode_override=payload.get("esb_mode", ""),
                )
            except ValueError as exc:
                transaction.status = Transaction.Status.FAILED
                transaction.response_status_code = 400
                transaction.error_message = str(exc)
                transaction.completed_at = timezone.now()
                transaction.save(
                    update_fields=[
                        "status",
                        "response_status_code",
                        "error_message",
                        "completed_at",
                    ]
                )
                return JsonResponse(
                    {
                        "ok": False,
                        "error": str(exc),
                        "correlation_id": str(transaction.correlation_id),
                    },
                    status=400,
                )

        try:
            _submit_async_transaction(
                transaction_id=transaction.id,
                target_url=target_url,
                request_method=method,
                request_body=request_body,
                proxy_headers=proxy_headers,
                esb_mode=esb_mode,
                esb_code=esb_code,
                esb_payload=esb_payload,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime threading state
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 500
            transaction.error_message = f"Failed to queue async request: {exc}"
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Failed to queue async request",
                    "correlation_id": str(transaction.correlation_id),
                },
                status=500,
            )

        status_url = reverse(
            "mediator:transaction-status",
            kwargs={"correlation_id": str(transaction.correlation_id)},
        )
        response = JsonResponse(
            {
                "ok": True,
                "accepted": True,
                "message": "Request accepted for asynchronous processing",
                "correlation_id": str(transaction.correlation_id),
                "status_url": status_url,
            },
            status=202,
        )
        response["Location"] = status_url
        response["Preference-Applied"] = "respond-async"
        response["X-Correlation-Id"] = str(transaction.correlation_id)
        return response

    if registration is not None:
        try:
            esb_payload = _build_esb_payload(request_body)
            esb_mode, esb_code = _resolve_esb_mode_and_code(
                request,
                registration,
                esb_mode_override=payload.get("esb_mode", ""),
            )
            esb_client = _build_esb_client()
            data_format = _resolve_esb_data_format()

            if esb_mode == "push":
                esb_result = esb_client.push_data(
                    push_code=esb_code,
                    req_body=esb_payload,
                    data_format=data_format,
                )
                if isinstance(esb_result, tuple):
                    response_data, success = esb_result
                else:
                    response_data, success = esb_result, True
            else:
                response_data, success = esb_client.request_data(
                    api_code=esb_code,
                    req_body=esb_payload,
                    data_format=data_format,
                )
        except ValueError as exc:
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 400
            transaction.error_message = str(exc)
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": str(exc),
                    "correlation_id": str(transaction.correlation_id),
                },
                status=400,
            )
        except Exception as exc:
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 502
            transaction.error_message = str(exc)
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Failed to relay request via national ESB",
                    "correlation_id": str(transaction.correlation_id),
                },
                status=502,
            )

        response_payload, response_text = _normalize_esb_response(response_data)
        response_status_code = 200 if success else 502
        transaction.response_status_code = response_status_code
        transaction.response_headers = {"Content-Type": "application/json"}
        transaction.response_body = _truncate_text(response_text)
        transaction.completed_at = timezone.now()
        if success:
            transaction.status = Transaction.Status.SUCCESSFUL
            transaction.error_message = ""
        else:
            transaction.status = Transaction.Status.FAILED
            transaction.error_message = "National ESB returned an unsuccessful response"
        transaction.save(
            update_fields=[
                "status",
                "response_status_code",
                "response_headers",
                "response_body",
                "error_message",
                "completed_at",
            ]
        )

        response = JsonResponse(
            response_payload,
            safe=not isinstance(response_payload, list),
            status=response_status_code,
        )
        response["X-Correlation-Id"] = str(transaction.correlation_id)
        return response

    upstream_request = urllib.request.Request(
        target_url,
        data=(request_body if request_body else None),
        headers=proxy_headers,
        method=method,
    )

    response_status_code = None
    response_headers = {}
    response_body = b""

    try:
        with urllib.request.urlopen(
            upstream_request,
            timeout=settings.MEDIATOR_FORWARD_TIMEOUT,
        ) as upstream_response:
            response_status_code = upstream_response.getcode()
            response_headers = dict(upstream_response.headers.items())
            response_body = upstream_response.read()
    except urllib.error.HTTPError as exc:
        response_status_code = exc.code
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        response_body = exc.read()
    except UPSTREAM_NETWORK_EXCEPTIONS as exc:
        transaction.status = Transaction.Status.FAILED
        transaction.response_status_code = 502
        transaction.error_message = str(exc)
        transaction.completed_at = timezone.now()
        transaction.save(
            update_fields=[
                "status",
                "response_status_code",
                "error_message",
                "completed_at",
            ]
        )
        return JsonResponse(
            {
                "ok": False,
                "error": "Failed to reach mediator endpoint",
                "correlation_id": str(transaction.correlation_id),
            },
            status=502,
        )

    transaction.response_status_code = response_status_code
    transaction.response_headers = _safe_response_headers(response_headers)
    transaction.response_body = _truncate_text(response_body)
    transaction.completed_at = timezone.now()

    if 200 <= response_status_code < 400:
        transaction.status = Transaction.Status.SUCCESSFUL
        transaction.error_message = ""
    else:
        transaction.status = Transaction.Status.FAILED
        transaction.error_message = f"Upstream returned HTTP {response_status_code}"

    transaction.save(
        update_fields=[
            "status",
            "response_status_code",
            "response_headers",
            "response_body",
            "error_message",
            "completed_at",
        ]
    )

    response = HttpResponse(response_body, status=response_status_code)
    for key, value in response_headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered in {"content-length", "connection", "transfer-encoding"}:
            continue
        response[key] = value

    response["X-Correlation-Id"] = str(transaction.correlation_id)
    return response


@swagger_doc(
    methods=["get"],
    summary="Get transaction status by correlation id",
    tags=["Mediator"],
    security=[{"BasicAuth": []}, {"XClientId": [], "XClientSecret": []}],
    path_params=[
        {
            "name": "correlation_id",
            "schema": {"type": "string", "format": "uuid"},
            "description": "Transaction correlation id",
        }
    ],
    responses={
        "200": {"description": "Transaction status"},
        "401": {"description": "Authentication required"},
        "404": {"description": "Transaction not found"},
    },
)
def transaction_status(request, correlation_id):
    client, auth_error = _authenticate_client(request)
    if auth_error is not None:
        return auth_error

    transaction = Transaction.objects.filter(
        correlation_id=correlation_id,
        client=client,
    ).first()
    if transaction is None:
        return JsonResponse(
            {
                "ok": False,
                "error": "Transaction not found",
                "correlation_id": str(correlation_id),
            },
            status=404,
        )

    return JsonResponse(_transaction_status_payload(transaction))


@swagger_doc(
    methods=["get", "post", "put", "patch", "delete", "options", "head"],
    summary="Proxy request through mediator channel routing",
    description=(
        "Catch-all endpoint for mediator forwarding. "
        "Supports async processing with ?async=true or Prefer: respond-async."
    ),
    tags=["Mediator Proxy"],
    security=[{"BasicAuth": []}, {"XClientId": [], "XClientSecret": []}],
    path_params=[
        {
            "name": "resource_path",
            "schema": {"type": "string"},
            "description": "Arbitrary path forwarded by mediator",
        }
    ],
    query_params=[
        {
            "name": "async",
            "schema": {"type": "boolean"},
            "description": "Queue request for async processing when true",
        },
        {
            "name": "organization",
            "schema": {"type": "string"},
            "description": "Organization identifier used in ESB routing",
        },
        {
            "name": "esb_mode",
            "schema": {"type": "string", "enum": ["normal", "push"]},
            "description": "ESB mode override when registration exists",
        },
    ],
    request_body={
        "required": False,
        "content": {
            "application/json": {"schema": {"type": "object"}},
            "text/plain": {"schema": {"type": "string"}},
        },
    },
    responses={
        "200": {"description": "Synchronous proxy success"},
        "202": {"description": "Accepted for asynchronous processing"},
        "400": {"description": "Validation error"},
        "401": {"description": "Authentication required"},
        "403": {"description": "Forbidden"},
        "404": {"description": "No route or transaction found"},
        "405": {"description": "Method not allowed"},
        "415": {"description": "Unsupported media type"},
        "502": {"description": "Upstream or ESB relay failure"},
        "503": {"description": "Mediator offline"},
    },
)
@csrf_exempt
def proxy_request(request, resource_path=""):
    del resource_path

    client, auth_error = _authenticate_client(request)
    if auth_error is not None:
        return auth_error

    pure_esb_mode = _is_pure_esb_mode()

    if pure_esb_mode:
        channel = None
        channel_error = None
    else:
        channel, channel_error = _match_channel(request)
        if channel_error is not None:
            return channel_error

    registration, registration_error = _resolve_external_registration(request, channel)
    if registration_error is not None:
        return registration_error

    if pure_esb_mode:
        if registration is None:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "Pure ESB mode requires an active External System Registration "
                        "for the provided organization"
                    ),
                },
                status=400,
            )
        target_url = settings.GOVESB_ENGINE_URL.rstrip("/") or "esb://unconfigured"
    elif registration is None:
        target_url = _build_target_url(channel, request)
    else:
        target_url = settings.GOVESB_ENGINE_URL.rstrip("/") or "esb://unconfigured"

    request_body = request.body or b""

    if (
        not pure_esb_mode
        and channel is not None
        and channel.requires_request_body
        and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        and len(request_body) == 0
    ):
        correlation_id = Transaction._meta.get_field("correlation_id").default()
        Transaction.objects.create(
            correlation_id=correlation_id,
            channel=channel,
            client=client,
            status=Transaction.Status.FAILED,
            request_method=request.method.upper(),
            request_url=target_url,
            request_headers=_loggable_request_headers(request),
            request_body="",
            error_message="Channel requires request body",
            completed_at=timezone.now(),
        )
        return JsonResponse(
            {
                "ok": False,
                "error": "Request body is required for this channel",
                "correlation_id": str(correlation_id),
            },
            status=400,
        )

    if (
        not pure_esb_mode
        and channel is not None
        and channel.request_content_type
        and len(request_body) > 0
    ):
        incoming_content_type = _normalize_content_type(
            request.headers.get("Content-Type", "")
        )
        expected_content_type = _normalize_content_type(channel.request_content_type)
        if incoming_content_type != expected_content_type:
            correlation_id = Transaction._meta.get_field("correlation_id").default()
            Transaction.objects.create(
                correlation_id=correlation_id,
                channel=channel,
                client=client,
                status=Transaction.Status.FAILED,
                request_method=request.method.upper(),
                request_url=target_url,
                request_headers=_loggable_request_headers(request),
                request_body=_truncate_text(request_body),
                error_message=(
                    "Unsupported content type. "
                    f"Expected {channel.request_content_type}"
                ),
                completed_at=timezone.now(),
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "Unsupported content type for this channel. "
                        f"Expected {channel.request_content_type}"
                    ),
                    "correlation_id": str(correlation_id),
                },
                status=415,
            )

    async_requested = _is_async_request(request)
    correlation_id = Transaction._meta.get_field("correlation_id").default()

    transaction = Transaction.objects.create(
        correlation_id=correlation_id,
        channel=channel,
        client=client,
        status=Transaction.Status.PENDING if async_requested else Transaction.Status.PROCESSING,
        request_method=request.method.upper(),
        request_url=target_url,
        request_headers=_loggable_request_headers(request),
        request_body=_truncate_text(request_body),
    )

    if async_requested:
        esb_mode = None
        esb_code = None
        esb_payload = None

        if registration is not None:
            try:
                esb_payload = _build_esb_payload(request_body)
                esb_mode, esb_code = _resolve_esb_mode_and_code(request, registration)
            except ValueError as exc:
                transaction.status = Transaction.Status.FAILED
                transaction.response_status_code = 400
                transaction.error_message = str(exc)
                transaction.completed_at = timezone.now()
                transaction.save(
                    update_fields=[
                        "status",
                        "response_status_code",
                        "error_message",
                        "completed_at",
                    ]
                )
                return JsonResponse(
                    {
                        "ok": False,
                        "error": str(exc),
                        "correlation_id": str(transaction.correlation_id),
                    },
                    status=400,
                )

        try:
            _submit_async_transaction(
                transaction_id=transaction.id,
                target_url=target_url,
                request_method=request.method.upper(),
                request_body=request_body,
                proxy_headers=_proxy_headers_from_request(
                    request,
                    transaction.correlation_id,
                ),
                esb_mode=esb_mode,
                esb_code=esb_code,
                esb_payload=esb_payload,
            )
        except Exception as exc:  # pragma: no cover - depends on runtime threading state
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 500
            transaction.error_message = f"Failed to queue async request: {exc}"
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Failed to queue async request",
                    "correlation_id": str(transaction.correlation_id),
                },
                status=500,
            )

        status_url = reverse(
            "mediator:transaction-status",
            kwargs={"correlation_id": str(transaction.correlation_id)},
        )
        response = JsonResponse(
            {
                "ok": True,
                "accepted": True,
                "message": "Request accepted for asynchronous processing",
                "correlation_id": str(transaction.correlation_id),
                "status_url": status_url,
            },
            status=202,
        )
        response["Location"] = status_url
        response["Preference-Applied"] = "respond-async"
        response["X-Correlation-Id"] = str(transaction.correlation_id)
        return response

    if registration is not None:
        try:
            esb_payload = _build_esb_payload(request_body)
            esb_mode, esb_code = _resolve_esb_mode_and_code(request, registration)
            esb_client = _build_esb_client()
            data_format = _resolve_esb_data_format()

            if esb_mode == "push":
                esb_result = esb_client.push_data(
                    push_code=esb_code,
                    req_body=esb_payload,
                    data_format=data_format,
                )
                if isinstance(esb_result, tuple):
                    response_data, success = esb_result
                else:
                    response_data, success = esb_result, True
            else:
                response_data, success = esb_client.request_data(
                    api_code=esb_code,
                    req_body=esb_payload,
                    data_format=data_format,
                )
        except ValueError as exc:
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 400
            transaction.error_message = str(exc)
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": str(exc),
                    "correlation_id": str(transaction.correlation_id),
                },
                status=400,
            )
        except Exception as exc:
            transaction.status = Transaction.Status.FAILED
            transaction.response_status_code = 502
            transaction.error_message = str(exc)
            transaction.completed_at = timezone.now()
            transaction.save(
                update_fields=[
                    "status",
                    "response_status_code",
                    "error_message",
                    "completed_at",
                ]
            )
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Failed to relay request via national ESB",
                    "correlation_id": str(transaction.correlation_id),
                },
                status=502,
            )

        response_payload, response_text = _normalize_esb_response(response_data)
        response_status_code = 200 if success else 502
        transaction.response_status_code = response_status_code
        transaction.response_headers = {"Content-Type": "application/json"}
        transaction.response_body = _truncate_text(response_text)
        transaction.completed_at = timezone.now()
        if success:
            transaction.status = Transaction.Status.SUCCESSFUL
            transaction.error_message = ""
        else:
            transaction.status = Transaction.Status.FAILED
            transaction.error_message = "National ESB returned an unsuccessful response"
        transaction.save(
            update_fields=[
                "status",
                "response_status_code",
                "response_headers",
                "response_body",
                "error_message",
                "completed_at",
            ]
        )

        response = JsonResponse(
            response_payload,
            safe=not isinstance(response_payload, list),
            status=response_status_code,
        )
        response["X-Correlation-Id"] = str(transaction.correlation_id)
        return response

    upstream_request = urllib.request.Request(
        target_url,
        data=(request_body if request_body else None),
        headers=_proxy_headers_from_request(request, transaction.correlation_id),
        method=request.method.upper(),
    )

    response_status_code = None
    response_headers = {}
    response_body = b""

    try:
        with urllib.request.urlopen(
            upstream_request,
            timeout=settings.MEDIATOR_FORWARD_TIMEOUT,
        ) as upstream_response:
            response_status_code = upstream_response.getcode()
            response_headers = dict(upstream_response.headers.items())
            response_body = upstream_response.read()

    except urllib.error.HTTPError as exc:
        response_status_code = exc.code
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        response_body = exc.read()

    except UPSTREAM_NETWORK_EXCEPTIONS as exc:
        transaction.status = Transaction.Status.FAILED
        transaction.response_status_code = 502
        transaction.error_message = str(exc)
        transaction.completed_at = timezone.now()
        transaction.save(
            update_fields=[
                "status",
                "response_status_code",
                "error_message",
                "completed_at",
            ]
        )

        return JsonResponse(
            {
                "ok": False,
                "error": "Failed to reach mediator endpoint",
                "correlation_id": str(transaction.correlation_id),
            },
            status=502,
        )

    transaction.response_status_code = response_status_code
    transaction.response_headers = _safe_response_headers(response_headers)
    transaction.response_body = _truncate_text(response_body)
    transaction.completed_at = timezone.now()

    if 200 <= response_status_code < 400:
        transaction.status = Transaction.Status.SUCCESSFUL
        transaction.error_message = ""
    else:
        transaction.status = Transaction.Status.FAILED
        transaction.error_message = f"Upstream returned HTTP {response_status_code}"

    transaction.save(
        update_fields=[
            "status",
            "response_status_code",
            "response_headers",
            "response_body",
            "error_message",
            "completed_at",
        ]
    )

    response = HttpResponse(response_body, status=response_status_code)
    for key, value in response_headers.items():
        lowered = key.lower()
        if lowered in HOP_BY_HOP_HEADERS:
            continue
        if lowered in {"content-length", "connection", "transfer-encoding"}:
            continue
        response[key] = value

    response["X-Correlation-Id"] = str(transaction.correlation_id)
    return response
