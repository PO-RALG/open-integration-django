import json
import smtplib
import uuid
import urllib.error
import urllib.request
from datetime import timedelta
from email.message import EmailMessage

from django.db import models, transaction
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from gateway.swagger_annotations import swagger_doc

from .models import (
    DeliveryLog,
    EventCatalog,
    EventRule,
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
from .serializers import EmitRequestSerializer


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _json_error(message, status=400, **extra):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("Invalid JSON body")


def _lookup_by_code(model, code, label):
    obj = model.objects.filter(code=str(code).strip()).first()
    if obj is None:
        raise ValueError(f"Unknown {label} code: {code}")
    return obj


def _serialize_lookup(queryset):
    return [
        {
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "is_active": item.is_active,
        }
        for item in queryset
    ]


def _serialize_outbox_item(item):
    return {
        "id": item.id,
        "event_code": item.event_id,
        "channel": item.channel.code,
        "priority": item.priority.code,
        "status": item.status.code,
        "recipient_user_id": item.recipient_user_id,
        "recipient_actor_type": item.recipient_actor_type.code,
        "subject": item.subject,
        "body": item.body,
        "to_phone": item.to_phone,
        "to_email": item.to_email,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "payload": item.payload,
        "attempt_count": item.attempt_count,
        "max_attempts": item.max_attempts,
        "next_attempt_at": item.next_attempt_at.isoformat(),
        "last_error": item.last_error,
        "idempotency_key": item.idempotency_key,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _send_email_via_smtp(item):
    host = settings.NOTIFICATION_SMTP_HOST
    from_email = settings.NOTIFICATION_FROM_EMAIL
    if not host:
        return False, "", {}, "SMTP host is not configured"
    if not from_email:
        return False, "", {}, "Notification from email is not configured"

    msg = EmailMessage()
    msg["Subject"] = item.subject or f"Notification: {item.event_id}"
    msg["From"] = from_email
    msg["To"] = item.to_email
    msg.set_content(item.body or "")

    try:
        if settings.NOTIFICATION_SMTP_USE_SSL:
            server_factory = smtplib.SMTP_SSL
        else:
            server_factory = smtplib.SMTP

        with server_factory(
            host,
            settings.NOTIFICATION_SMTP_PORT,
            timeout=settings.NOTIFICATION_SMTP_TIMEOUT,
        ) as smtp:
            if (
                settings.NOTIFICATION_SMTP_USE_TLS
                and not settings.NOTIFICATION_SMTP_USE_SSL
            ):
                smtp.starttls()
            if settings.NOTIFICATION_SMTP_USERNAME:
                smtp.login(
                    settings.NOTIFICATION_SMTP_USERNAME,
                    settings.NOTIFICATION_SMTP_PASSWORD,
                )
            failed = smtp.send_message(msg)
            if failed:
                return (
                    False,
                    "",
                    {"failed_recipients": failed},
                    "SMTP rejected recipient",
                )

        provider_message_id = f"smtp-{uuid.uuid4()}"
        return (
            True,
            provider_message_id,
            {"info": "Email accepted by SMTP server"},
            "",
        )
    except Exception as exc:
        return False, "", {}, str(exc)


def _send_sms_via_provider(item):
    sms_url = settings.NOTIFICATION_SMS_URL
    if not sms_url:
        return False, "", {}, "SMS provider URL is not configured"

    sms_from = settings.NOTIFICATION_SMS_SENDER_ID
    if not sms_from:
        return False, "", {}, "SMS sender id is not configured"

    raw_phone = item.to_phone
    destinations = []
    if isinstance(raw_phone, (list, tuple)):
        destinations = [str(phone).strip() for phone in raw_phone if str(phone).strip()]
    elif isinstance(raw_phone, str):
        phone_text = raw_phone.strip()
        if phone_text:
            if phone_text.startswith("["):
                try:
                    parsed = json.loads(phone_text)
                    if isinstance(parsed, list):
                        destinations = [
                            str(phone).strip() for phone in parsed if str(phone).strip()
                        ]
                    elif parsed:
                        destinations = [str(parsed).strip()]
                except json.JSONDecodeError:
                    destinations = [p.strip() for p in phone_text.split(",") if p.strip()]
            else:
                destinations = [p.strip() for p in phone_text.split(",") if p.strip()]
    elif raw_phone:
        destinations = [str(raw_phone).strip()]

    if not destinations:
        return False, "", {}, "SMS destination phone number is missing"

    payload = {
        "from": sms_from,
        "to": destinations[0] if len(destinations) == 1 else destinations,
        "text": item.body or "",
    }
    if len(destinations) > 1:
        payload["reference"] = item.idempotency_key

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    auth_header = (settings.NOTIFICATION_SMS_AUTH_HEADER or "").strip()
    sms_token = (settings.NOTIFICATION_SMS_TOKEN or "").strip()
    if auth_header:
        headers["Authorization"] = auth_header
    elif sms_token:
        if sms_token.lower().startswith(("basic ", "bearer ")):
            headers["Authorization"] = sms_token
        else:
            headers["Authorization"] = f"Bearer {sms_token}"

    request_obj = urllib.request.Request(
        sms_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request_obj,
            timeout=settings.NOTIFICATION_SMS_TIMEOUT,
        ) as response:
            status_code = response.getcode()
            raw_body = response.read().decode("utf-8", errors="replace")
            try:
                parsed_body = json.loads(raw_body) if raw_body else {}
            except json.JSONDecodeError:
                parsed_body = {"raw": raw_body}

            if 200 <= status_code < 300:
                provider_message_id = (
                    parsed_body.get("message_id")
                    or parsed_body.get("id")
                    or parsed_body.get("reference")
                    or f"sms-{uuid.uuid4()}"
                )
                return True, str(provider_message_id), parsed_body, ""

            return (
                False,
                "",
                parsed_body,
                f"SMS provider returned HTTP {status_code}",
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return (
            False,
            "",
            {"status_code": exc.code, "body": body},
            f"SMS provider HTTP error {exc.code}",
        )
    except Exception as exc:
        return False, "", {}, str(exc)


@swagger_doc(
    methods=["get"],
    summary="Notification API root",
    tags=["Notification"],
    responses={"200": {"description": "Notification service metadata"}},
)
@require_GET
def api_root(_request):
    return JsonResponse(
        {
            "ok": True,
            "service": "TTPB Notification API",
            "version": "1.0",
            "endpoints": {
                "lookups": "/api/notification/lookups/",
                "events": "/api/notification/events/",
                "rules": "/api/notification/rules/",
                "templates": "/api/notification/templates/",
                "emit": "/api/notification/emit/",
                "outbox": "/api/notification/outbox/",
                "process_outbox": "/api/notification/outbox/process/",
                "inbox": "/api/notification/inbox/<user_id>/",
                "preferences": "/api/notification/preferences/<actor_type_code>/<user_id>/",
            },
        }
    )


@swagger_doc(
    methods=["get"],
    summary="Get notification lookup values",
    tags=["Notification"],
    responses={"200": {"description": "Lookup datasets"}},
)
@require_GET
def lookups(_request):
    return JsonResponse(
        {
            "ok": True,
            "data": {
                "modules": _serialize_lookup(LkModule.objects.all()),
                "channels": _serialize_lookup(LkChannel.objects.all()),
                "statuses": _serialize_lookup(LkStatus.objects.all()),
                "priorities": _serialize_lookup(LkPriority.objects.all()),
                "actor_types": _serialize_lookup(LkActorType.objects.all()),
            },
        }
    )


@swagger_doc(
    methods=["get"],
    summary="List notification events",
    tags=["Notification"],
    query_params=[
        {"name": "module", "schema": {"type": "string"}},
        {"name": "active", "schema": {"type": "boolean"}},
    ],
    responses={"200": {"description": "Events list"}},
)
@require_GET
def events(request):
    module_code = request.GET.get("module")
    is_active = request.GET.get("active")

    queryset = EventCatalog.objects.select_related("module").all().order_by("code")
    if module_code:
        queryset = queryset.filter(module__code=module_code)
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active.lower() == "true")

    payload = [
        {
            "code": event.code,
            "description": event.description,
            "module": event.module.code,
            "is_active": event.is_active,
            "created_at": event.created_at.isoformat(),
        }
        for event in queryset
    ]
    return JsonResponse({"ok": True, "count": len(payload), "data": payload})


@swagger_doc(
    methods=["get"],
    summary="List templates",
    tags=["Notification"],
    query_params=[
        {"name": "event_code", "schema": {"type": "string"}},
        {"name": "channel", "schema": {"type": "string"}},
        {"name": "language", "schema": {"type": "string"}},
        {"name": "active", "schema": {"type": "boolean"}},
    ],
    responses={"200": {"description": "Template list"}},
)
@require_GET
def templates(request):
    event_code = request.GET.get("event_code")
    channel_code = request.GET.get("channel")
    language = request.GET.get("language")
    active = request.GET.get("active")

    queryset = (
        Template.objects.select_related("event", "channel")
        .all()
        .order_by("event_id", "channel__code", "-version")
    )
    if event_code:
        queryset = queryset.filter(event_id=event_code)
    if channel_code:
        queryset = queryset.filter(channel__code=channel_code)
    if language:
        queryset = queryset.filter(language=language)
    if active is not None:
        queryset = queryset.filter(is_active=active.lower() == "true")

    payload = [
        {
            "id": item.id,
            "event_code": item.event_id,
            "channel": item.channel.code,
            "language": item.language,
            "subject": item.subject,
            "body": item.body,
            "version": item.version,
            "is_active": item.is_active,
            "created_at": item.created_at.isoformat(),
        }
        for item in queryset
    ]
    return JsonResponse({"ok": True, "count": len(payload), "data": payload})


@swagger_doc(
    methods=["get"],
    summary="List active/inactive rules",
    tags=["Notification"],
    query_params=[
        {"name": "event_code", "schema": {"type": "string"}},
        {"name": "active", "schema": {"type": "boolean"}},
    ],
    responses={"200": {"description": "Rule list"}},
)
@require_GET
def rules(request):
    event_code = request.GET.get("event_code")
    active = request.GET.get("active")

    queryset = (
        EventRule.objects.select_related("event", "priority")
        .prefetch_related("rule_channels__channel")
        .order_by("event_id", "id")
    )
    if event_code:
        queryset = queryset.filter(event_id=event_code)
    if active is not None:
        queryset = queryset.filter(is_active=active.lower() == "true")

    payload = []
    for rule in queryset:
        channels = [link.channel.code for link in rule.rule_channels.all()]
        payload.append(
            {
                "id": rule.id,
                "event_code": rule.event_id,
                "recipient_policy": rule.recipient_policy,
                "priority": rule.priority.code,
                "is_active": rule.is_active,
                "channels": channels,
                "created_at": rule.created_at.isoformat(),
            }
        )

    return JsonResponse({"ok": True, "count": len(payload), "data": payload})


@swagger_doc(
    methods=["get"],
    summary="Get user channel preference",
    tags=["Notification"],
    path_params=[
        {"name": "actor_type_code", "schema": {"type": "string"}},
        {"name": "user_id", "schema": {"type": "integer"}},
    ],
    responses={
        "200": {"description": "Preference payload"},
        "404": {"description": "Unknown actor type"},
    },
)
@swagger_doc(
    methods=["post"],
    summary="Create or update user channel preference",
    tags=["Notification"],
    path_params=[
        {"name": "actor_type_code", "schema": {"type": "string"}},
        {"name": "user_id", "schema": {"type": "integer"}},
    ],
    request_body={
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    responses={
        "201": {"description": "Preference persisted"},
        "400": {"description": "Invalid payload"},
        "404": {"description": "Unknown actor type"},
    },
)
@require_http_methods(["GET", "POST"])
@csrf_exempt
def user_preference(request, actor_type_code, user_id):
    actor_type = LkActorType.objects.filter(code=actor_type_code).first()
    if actor_type is None:
        return _json_error("Unknown actor type", status=404, actor_type=actor_type_code)

    if request.method == "GET":
        pref = UserPreference.objects.filter(user_id=user_id, actor_type=actor_type).first()
        if pref is None:
            return JsonResponse(
                {
                    "ok": True,
                    "data": {
                        "user_id": user_id,
                        "actor_type": actor_type.code,
                        "allow_sms": True,
                        "allow_email": True,
                        "allow_in_app": True,
                        "preferred_lang": "en",
                        "is_default": True,
                    },
                }
            )

        return JsonResponse(
            {
                "ok": True,
                "data": {
                    "user_id": pref.user_id,
                    "actor_type": pref.actor_type.code,
                    "allow_sms": pref.allow_sms,
                    "allow_email": pref.allow_email,
                    "allow_in_app": pref.allow_in_app,
                    "preferred_lang": pref.preferred_lang,
                    "updated_at": pref.updated_at.isoformat(),
                    "is_default": False,
                },
            }
        )

    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return _json_error(str(exc))

    pref, _created = UserPreference.objects.update_or_create(
        user_id=user_id,
        actor_type=actor_type,
        defaults={
            "allow_sms": bool(payload.get("allow_sms", True)),
            "allow_email": bool(payload.get("allow_email", True)),
            "allow_in_app": bool(payload.get("allow_in_app", True)),
            "preferred_lang": payload.get("preferred_lang", "en"),
        },
    )

    return JsonResponse(
        {
            "ok": True,
            "data": {
                "user_id": pref.user_id,
                "actor_type": pref.actor_type.code,
                "allow_sms": pref.allow_sms,
                "allow_email": pref.allow_email,
                "allow_in_app": pref.allow_in_app,
                "preferred_lang": pref.preferred_lang,
                "updated_at": pref.updated_at.isoformat(),
            },
        },
        status=201,
    )


def _choose_template(event_code, channel, language):
    exact = (
        Template.objects.filter(
            event_id=event_code,
            channel=channel,
            language=language,
            is_active=True,
        )
        .order_by("-version")
        .first()
    )
    if exact:
        return exact

    fallback = (
        Template.objects.filter(
            event_id=event_code,
            channel=channel,
            language="en",
            is_active=True,
        )
        .order_by("-version")
        .first()
    )
    return fallback


def _preference_for(user_id, actor_type):
    pref = UserPreference.objects.filter(
        user_id=user_id,
        actor_type=actor_type,
    ).first()
    if pref is not None:
        return pref

    class DefaultPreference:
        allow_sms = True
        allow_email = True
        allow_in_app = True
        preferred_lang = "en"

    return DefaultPreference()


def _channel_allowed_by_preference(channel_code, pref):
    if channel_code == "SMS":
        return pref.allow_sms
    if channel_code == "EMAIL":
        return pref.allow_email
    if channel_code == "IN_APP":
        return pref.allow_in_app
    return True


def _resolve_recipients_for_rule(recipients, recipient_policy):
    policy = (recipient_policy or "").strip().upper()
    if policy in {"", "ALL", "*"}:
        return recipients

    filtered = []
    for recipient in recipients:
        actor_type = str(recipient.get("actor_type", "")).strip().upper()
        if actor_type == policy:
            filtered.append(recipient)
    return filtered


def _normalize_phone_for_storage(value):
    if isinstance(value, (list, tuple)):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return json.dumps(cleaned)
    if value is None:
        return ""
    return str(value).strip()


@swagger_doc(
    methods=["post"],
    summary="Emit notification event",
    tags=["Notification"],
    request_body=EmitRequestSerializer.swagger_request_body(),
    responses={
        "200": {"description": "Suppressed event result"},
        "201": {"description": "Queued event result"},
        "400": {"description": "Validation error"},
        "404": {"description": "Unknown event"},
    },
)
@require_http_methods(["POST"])
@csrf_exempt
def emit(request):
    try:
        payload = EmitRequestSerializer.validate(_parse_json_body(request))
    except ValueError as exc:
        return _json_error(str(exc))

    event_code = payload["event_code"]

    event = EventCatalog.objects.filter(code=event_code, is_active=True).first()
    if event is None:
        return _json_error("Unknown or inactive event_code", status=404, event_code=event_code)

    context = payload["context"]

    rules = (
        EventRule.objects.select_related("priority")
        .prefetch_related("rule_channels__channel")
        .filter(event_id=event_code, is_active=True)
        .order_by("id")
    )
    if not rules.exists():
        return JsonResponse(
            {
                "ok": True,
                "event_code": event_code,
                "disposition": "suppressed",
                "reason": "no_active_rule",
                "queued_count": 0,
                "queued_outbox_ids": [],
                "skipped": [],
            }
        )

    recipients = payload.get("recipients", [])
    if not recipients:
        return JsonResponse(
            {
                "ok": True,
                "event_code": event_code,
                "disposition": "suppressed",
                "reason": "no_recipients",
                "queued_count": 0,
                "queued_outbox_ids": [],
                "skipped": [],
            }
        )

    queued_status = _lookup_by_code(LkStatus, "QUEUED", "status")

    queued_items = []
    skipped = []
    idempotency_base = payload.get("idempotency_key", str(uuid.uuid4()))

    with transaction.atomic():
        for rule in rules:
            rule_recipients = _resolve_recipients_for_rule(recipients, rule.recipient_policy)
            if not rule_recipients:
                skipped.append(
                    {
                        "rule_id": rule.id,
                        "recipient_policy": rule.recipient_policy,
                        "reason": "No recipients matched recipient_policy",
                    }
                )
                continue

            channels = [link.channel for link in rule.rule_channels.all()]
            if not channels:
                skipped.append(
                    {
                        "rule_id": rule.id,
                        "reason": "No channels configured for active rule",
                    }
                )
                continue

            for recipient in rule_recipients:
                try:
                    recipient_user_id = int(recipient["user_id"])
                    actor_type = _lookup_by_code(
                        LkActorType,
                        recipient["actor_type"],
                        "actor_type",
                    )
                except (KeyError, ValueError) as exc:
                    skipped.append({"recipient": recipient, "reason": str(exc)})
                    continue

                pref = _preference_for(recipient_user_id, actor_type)
                language = recipient.get("preferred_lang") or pref.preferred_lang or "en"

                for channel_obj in channels:
                    if not _channel_allowed_by_preference(channel_obj.code, pref):
                        skipped.append(
                            {
                                "recipient_user_id": recipient_user_id,
                                "channel": channel_obj.code,
                                "reason": "Blocked by user preference",
                            }
                        )
                        continue

                    template = _choose_template(event_code, channel_obj, language)
                    if template is None:
                        skipped.append(
                            {
                                "recipient_user_id": recipient_user_id,
                                "channel": channel_obj.code,
                                "reason": "No active template found for event/channel/language",
                            }
                        )
                        continue

                    subject = template.subject.format_map(SafeDict(context)) if template.subject else ""
                    body = template.body.format_map(SafeDict(context))
                    idem_key = (
                        f"{idempotency_base}:{event_code}:{rule.id}:"
                        f"{recipient_user_id}:{actor_type.code}:{channel_obj.code}"
                    )

                    item, created = Outbox.objects.get_or_create(
                        idempotency_key=idem_key,
                        defaults={
                            "event": event,
                            "channel": channel_obj,
                            "priority": rule.priority,
                            "status": queued_status,
                            "recipient_user_id": recipient_user_id,
                            "recipient_actor_type": actor_type,
                            "subject": subject,
                            "body": body,
                            "to_phone": _normalize_phone_for_storage(
                                recipient.get("to_phone", "")
                            ),
                            "to_email": recipient.get("to_email", ""),
                            "entity_type": payload.get("entity_type", ""),
                            "entity_id": str(payload.get("entity_id", "")),
                            "payload": payload.get("payload", {}),
                            "max_attempts": int(payload.get("max_attempts", 5)),
                        },
                    )
                    if created:
                        queued_items.append(item.id)
                    else:
                        skipped.append(
                            {
                                "recipient_user_id": recipient_user_id,
                                "channel": channel_obj.code,
                                "reason": "Duplicate idempotency key (already queued)",
                                "outbox_id": item.id,
                            }
                        )

    disposition = "queued" if queued_items else "suppressed"
    reason = "" if queued_items else "no_messages_built"

    return JsonResponse(
        {
            "ok": True,
            "event_code": event_code,
            "disposition": disposition,
            "reason": reason,
            "queued_count": len(queued_items),
            "queued_outbox_ids": queued_items,
            "skipped": skipped,
        },
        status=201,
    )


@swagger_doc(
    methods=["get"],
    summary="List outbox items",
    tags=["Notification"],
    query_params=[
        {"name": "status", "schema": {"type": "string"}},
        {"name": "channel", "schema": {"type": "string"}},
        {"name": "due", "schema": {"type": "boolean"}},
        {"name": "limit", "schema": {"type": "integer"}},
    ],
    responses={"200": {"description": "Outbox list"}},
)
@require_GET
def outbox_list(request):
    status_code = request.GET.get("status")
    channel_code = request.GET.get("channel")
    due_only = request.GET.get("due", "false").lower() == "true"
    limit = int(request.GET.get("limit", "100"))
    limit = min(max(limit, 1), 500)

    queryset = (
        Outbox.objects.select_related("event", "channel", "priority", "status", "recipient_actor_type")
        .all()
        .order_by("next_attempt_at", "id")
    )
    if status_code:
        queryset = queryset.filter(status__code=status_code)
    if channel_code:
        queryset = queryset.filter(channel__code=channel_code)
    if due_only:
        queryset = queryset.filter(next_attempt_at__lte=timezone.now())

    items = list(queryset[:limit])
    return JsonResponse(
        {
            "ok": True,
            "count": len(items),
            "data": [_serialize_outbox_item(item) for item in items],
        }
    )


def _resolve_required_statuses():
    return {
        "QUEUED": _lookup_by_code(LkStatus, "QUEUED", "status"),
        "SENT": _lookup_by_code(LkStatus, "SENT", "status"),
        "DELIVERED": _lookup_by_code(LkStatus, "DELIVERED", "status"),
        "FAILED": _lookup_by_code(LkStatus, "FAILED", "status"),
        "CANCELLED": _lookup_by_code(LkStatus, "CANCELLED", "status"),
    }


def _process_single_outbox_item(item, statuses):
    attempt_no = item.attempt_count + 1
    now = timezone.now()
    is_critical = item.priority.code == "CRITICAL"

    def _log(status_obj, provider, provider_message_id="", response_payload=None, error_message=""):
        DeliveryLog.objects.create(
            outbox=item,
            attempt_no=attempt_no,
            status=status_obj,
            provider=provider,
            provider_message_id=provider_message_id,
            response_payload=response_payload or {},
            error_message=error_message,
        )

    def _backoff_for_attempt(no):
        # 1st retry=1m, then 2m, 4m, ... capped at 60m.
        return timedelta(minutes=min(60, max(1, 2 ** (no - 1))))

    def _mark_failed(error_msg, provider_name):
        item.attempt_count = attempt_no
        item.last_error = error_msg
        final_failure = item.attempt_count >= item.max_attempts
        escalation_required = final_failure and is_critical
        if final_failure:
            item.status = statuses["FAILED"]
            item.next_attempt_at = now
        else:
            item.status = statuses["FAILED"]
            item.next_attempt_at = now + _backoff_for_attempt(attempt_no)
        item.save(update_fields=["attempt_count", "status", "last_error", "next_attempt_at", "updated_at"])
        _log(
            statuses["FAILED"],
            provider=provider_name,
            response_payload={
                "phase": "final" if final_failure else "temporary",
                "escalation_required": escalation_required,
            },
            error_message=error_msg,
        )
        return {
            "outbox_id": item.id,
            "result": "failed_final" if final_failure else "failed_temporary",
            "error": error_msg,
            "retry_scheduled": not final_failure,
            "next_attempt_at": item.next_attempt_at.isoformat(),
            "escalation_required": escalation_required,
        }

    if item.channel.code == "IN_APP":
        InAppMessage.objects.create(
            user_id=item.recipient_user_id,
            title=item.subject,
            body=item.body,
            event_code=item.event_id,
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            is_read=False,
        )
        item.attempt_count = attempt_no
        item.status = statuses["DELIVERED"]
        item.last_error = ""
        item.next_attempt_at = now
        item.save(update_fields=["attempt_count", "status", "last_error", "next_attempt_at", "updated_at"])
        _log(
            statuses["DELIVERED"],
            provider="in_app",
            provider_message_id=f"inapp-{item.id}-{attempt_no}",
            response_payload={"info": "Delivered to in-app inbox"},
        )
        return {"outbox_id": item.id, "result": "delivered", "channel": "IN_APP"}

    if item.channel.code == "EMAIL" and not item.to_email:
        return _mark_failed("Missing to_email", "smtp")

    if item.channel.code == "SMS" and not item.to_phone:
        return _mark_failed("Missing to_phone", "sms_gateway")

    if item.channel.code == "EMAIL":
        success, provider_message_id, response_payload, error_msg = _send_email_via_smtp(
            item
        )
        if not success:
            return _mark_failed(error_msg, "smtp")
        provider = "smtp"
    else:
        success, provider_message_id, response_payload, error_msg = _send_sms_via_provider(
            item
        )
        if not success:
            return _mark_failed(error_msg, "sms_gateway")
        provider = "sms_gateway"

    item.attempt_count = attempt_no
    item.status = statuses["SENT"]
    item.last_error = ""
    item.next_attempt_at = now
    item.save(update_fields=["attempt_count", "status", "last_error", "next_attempt_at", "updated_at"])
    _log(
        statuses["SENT"],
        provider=provider,
        provider_message_id=provider_message_id,
        response_payload=response_payload or {"info": "Queued to provider"},
    )
    return {"outbox_id": item.id, "result": "sent", "channel": item.channel.code}


@swagger_doc(
    methods=["post"],
    summary="Process due outbox items",
    tags=["Notification"],
    request_body={
        "required": False,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    responses={
        "200": {"description": "Processing result"},
        "400": {"description": "Invalid payload"},
        "500": {"description": "Configuration error"},
    },
)
@require_http_methods(["POST"])
@csrf_exempt
def outbox_process(request):
    try:
        payload = _parse_json_body(request)
    except ValueError as exc:
        return _json_error(str(exc))

    limit = int(payload.get("limit", 100))
    limit = min(max(limit, 1), 500)
    now = timezone.now()

    try:
        statuses = _resolve_required_statuses()
    except ValueError as exc:
        return _json_error(str(exc), status=500)

    due_items = list(
        Outbox.objects.select_related("channel", "status")
        .filter(
            status__in=[statuses["QUEUED"], statuses["FAILED"]],
            next_attempt_at__lte=now,
            attempt_count__lt=models.F("max_attempts"),
        )
        .order_by("next_attempt_at", "id")[:limit]
    )

    results = []
    with transaction.atomic():
        for item in due_items:
            results.append(_process_single_outbox_item(item, statuses))

    return JsonResponse(
        {
            "ok": True,
            "processed_count": len(results),
            "results": results,
        }
    )


@swagger_doc(
    methods=["get"],
    summary="Get delivery logs for outbox item",
    tags=["Notification"],
    path_params=[{"name": "outbox_id", "schema": {"type": "integer"}}],
    responses={
        "200": {"description": "Delivery logs"},
        "404": {"description": "Outbox not found"},
    },
)
@require_GET
def outbox_delivery_logs(request, outbox_id):
    if not Outbox.objects.filter(id=outbox_id).exists():
        return _json_error("Outbox not found", status=404, outbox_id=outbox_id)

    logs = (
        DeliveryLog.objects.select_related("status")
        .filter(outbox_id=outbox_id)
        .order_by("-created_at")
    )
    payload = [
        {
            "id": log.id,
            "outbox_id": log.outbox_id,
            "attempt_no": log.attempt_no,
            "status": log.status.code,
            "provider": log.provider,
            "provider_message_id": log.provider_message_id,
            "response_payload": log.response_payload,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]
    return JsonResponse({"ok": True, "count": len(payload), "data": payload})


@swagger_doc(
    methods=["get"],
    summary="List in-app inbox messages for user",
    tags=["Notification"],
    path_params=[{"name": "user_id", "schema": {"type": "integer"}}],
    query_params=[
        {"name": "unread", "schema": {"type": "boolean"}},
        {"name": "limit", "schema": {"type": "integer"}},
    ],
    responses={"200": {"description": "Inbox items"}},
)
@require_GET
def inbox(request, user_id):
    unread_only = request.GET.get("unread", "false").lower() == "true"
    limit = int(request.GET.get("limit", "100"))
    limit = min(max(limit, 1), 500)

    queryset = InAppMessage.objects.filter(user_id=user_id).order_by("-created_at")
    if unread_only:
        queryset = queryset.filter(is_read=False)
    messages = list(queryset[:limit])

    payload = [
        {
            "id": msg.id,
            "user_id": msg.user_id,
            "title": msg.title,
            "body": msg.body,
            "event_code": msg.event_code,
            "entity_type": msg.entity_type,
            "entity_id": msg.entity_id,
            "is_read": msg.is_read,
            "created_at": msg.created_at.isoformat(),
        }
        for msg in messages
    ]
    return JsonResponse({"ok": True, "count": len(payload), "data": payload})


@swagger_doc(
    methods=["post"],
    summary="Mark in-app message as read",
    tags=["Notification"],
    path_params=[{"name": "message_id", "schema": {"type": "integer"}}],
    responses={
        "200": {"description": "Message marked as read"},
        "404": {"description": "Message not found"},
    },
)
@require_http_methods(["POST"])
@csrf_exempt
def mark_inbox_read(_request, message_id):
    msg = InAppMessage.objects.filter(id=message_id).first()
    if msg is None:
        return _json_error("Message not found", status=404, message_id=message_id)

    if not msg.is_read:
        msg.is_read = True
        msg.save(update_fields=["is_read"])

    return JsonResponse(
        {
            "ok": True,
            "data": {
                "id": msg.id,
                "user_id": msg.user_id,
                "is_read": msg.is_read,
            },
        }
    )
