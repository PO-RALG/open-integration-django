import json

from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from unittest.mock import MagicMock, patch

from notification.models import (
    DeliveryLog,
    EventCatalog,
    EventRule,
    EventRuleChannel,
    InAppMessage,
    LkActorType,
    LkChannel,
    LkPriority,
    LkStatus,
    Outbox,
    Template,
)


class NotificationApiTests(TestCase):
    def setUp(self):
        self.event = EventCatalog.objects.get(code="ACCOUNT_ROLE_CHANGED")
        self.channel_inapp = LkChannel.objects.get(code="IN_APP")
        self.channel_sms = LkChannel.objects.get(code="SMS")
        self.channel_email = LkChannel.objects.get(code="EMAIL")
        self.priority_high = LkPriority.objects.get(code="HIGH")
        self.priority_critical = LkPriority.objects.get(code="CRITICAL")
        self.actor_staff = LkActorType.objects.get(code="STAFF")
        self.actor_teacher = LkActorType.objects.get(code="TEACHER")
        self.status_queued = LkStatus.objects.get(code="QUEUED")
        self.status_delivered = LkStatus.objects.get(code="DELIVERED")
        self.status_failed = LkStatus.objects.get(code="FAILED")

        self.template = Template.objects.create(
            event=self.event,
            channel=self.channel_inapp,
            language="en",
            subject="Role Updated",
            body="Hello {name}, your role changed to {role}.",
            version=1,
            is_active=True,
        )
        self.rule = EventRule.objects.create(
            event=self.event,
            recipient_policy="STAFF",
            priority=self.priority_high,
            is_active=True,
        )
        EventRuleChannel.objects.create(
            event_rule=self.rule,
            channel=self.channel_inapp,
        )

    def test_notification_root_endpoint(self):
        response = self.client.get(reverse("notification:root"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("emit", response.json()["endpoints"])

    def test_lookups_endpoint_returns_seeded_values(self):
        response = self.client.get(reverse("notification:lookups"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        module_codes = {item["code"] for item in payload["data"]["modules"]}
        self.assertIn("NOTIFICATION", module_codes)

    def test_user_preference_upsert_and_get(self):
        get_default = self.client.get(
            reverse("notification:user_preference", args=["STAFF", 1001])
        )
        self.assertEqual(get_default.status_code, 200)
        self.assertTrue(get_default.json()["data"]["is_default"])

        upsert = self.client.post(
            reverse("notification:user_preference", args=["STAFF", 1001]),
            data='{"allow_sms": false, "allow_email": true, "allow_in_app": true, "preferred_lang": "en"}',
            content_type="application/json",
        )
        self.assertEqual(upsert.status_code, 201)

        get_saved = self.client.get(
            reverse("notification:user_preference", args=["STAFF", 1001])
        )
        self.assertEqual(get_saved.status_code, 200)
        self.assertFalse(get_saved.json()["data"]["allow_sms"])
        self.assertFalse(get_saved.json()["data"]["is_default"])

    def test_emit_creates_outbox_items(self):
        response = self.client.post(
            reverse("notification:emit"),
            data=(
                '{"event_code":"ACCOUNT_ROLE_CHANGED",'
                '"context":{"name":"Asha","role":"Reviewer"},'
                '"recipients":[{"user_id":2001,"actor_type":"STAFF"}]}'
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["queued_count"], 1)

        item = Outbox.objects.get(id=payload["queued_outbox_ids"][0])
        self.assertEqual(item.status, self.status_queued)
        self.assertEqual(item.channel, self.channel_inapp)
        self.assertIn("Asha", item.body)

    def test_emit_suppresses_when_no_active_rule(self):
        response = self.client.post(
            reverse("notification:emit"),
            data=(
                '{"event_code":"PAYMENT_CONFIRMED",'
                '"context":{"name":"Asha"},'
                '"recipients":[{"user_id":2001,"actor_type":"STAFF"}]}'
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["disposition"], "suppressed")
        self.assertEqual(payload["reason"], "no_active_rule")
        self.assertEqual(payload["queued_count"], 0)

    def test_emit_resolves_recipients_by_policy(self):
        response = self.client.post(
            reverse("notification:emit"),
            data=(
                '{"event_code":"ACCOUNT_ROLE_CHANGED",'
                '"context":{"name":"Asha","role":"Reviewer"},'
                '"recipients":['
                '{"user_id":111,"actor_type":"TEACHER"},'
                '{"user_id":222,"actor_type":"STAFF"}'
                ']}'
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["queued_count"], 1)
        outbox = Outbox.objects.get(id=payload["queued_outbox_ids"][0])
        self.assertEqual(outbox.recipient_user_id, 222)
        self.assertEqual(outbox.recipient_actor_type, self.actor_staff)

    def test_process_outbox_delivers_in_app_message(self):
        emit_response = self.client.post(
            reverse("notification:emit"),
            data=(
                '{"event_code":"ACCOUNT_ROLE_CHANGED",'
                '"context":{"name":"Asha","role":"Reviewer"},'
                '"recipients":[{"user_id":3001,"actor_type":"STAFF"}]}'
            ),
            content_type="application/json",
        )
        self.assertEqual(emit_response.status_code, 201)

        process_response = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(process_response.status_code, 200)
        self.assertEqual(process_response.json()["processed_count"], 1)

        outbox = Outbox.objects.latest("id")
        self.assertEqual(outbox.status, self.status_delivered)

        message = InAppMessage.objects.get(user_id=3001)
        self.assertFalse(message.is_read)

        mark_read = self.client.post(
            reverse("notification:inbox_read", args=[message.id]),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(mark_read.status_code, 200)
        message.refresh_from_db()
        self.assertTrue(message.is_read)

    def test_outbox_retry_then_final_failed_flow(self):
        item = Outbox.objects.create(
            event=self.event,
            channel=self.channel_sms,
            priority=self.priority_high,
            status=self.status_queued,
            recipient_user_id=7001,
            recipient_actor_type=self.actor_staff,
            subject="s",
            body="b",
            to_phone="",
            max_attempts=2,
            next_attempt_at=timezone.now(),
            idempotency_key=f"retry-{timezone.now().timestamp()}",
        )

        first = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["processed_count"], 1)
        item.refresh_from_db()
        self.assertEqual(item.status, self.status_failed)
        self.assertEqual(item.attempt_count, 1)

        item.next_attempt_at = timezone.now()
        item.save(update_fields=["next_attempt_at", "updated_at"])

        second = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["processed_count"], 1)
        item.refresh_from_db()
        self.assertEqual(item.status, self.status_failed)
        self.assertEqual(item.attempt_count, 2)

    def test_critical_final_failure_sets_escalation_flag(self):
        item = Outbox.objects.create(
            event=self.event,
            channel=self.channel_sms,
            priority=self.priority_critical,
            status=self.status_queued,
            recipient_user_id=8001,
            recipient_actor_type=self.actor_staff,
            subject="s",
            body="b",
            to_phone="",
            max_attempts=1,
            next_attempt_at=timezone.now(),
            idempotency_key=f"critical-{timezone.now().timestamp()}",
        )

        response = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed_count"], 1)
        result = response.json()["results"][0]
        self.assertTrue(result["escalation_required"])
        item.refresh_from_db()
        self.assertEqual(item.status, self.status_failed)

        log = DeliveryLog.objects.filter(outbox=item).latest("created_at")
        self.assertTrue(log.response_payload["escalation_required"])

    @override_settings(
        NOTIFICATION_SMTP_HOST="smtp.example.org",
        NOTIFICATION_SMTP_PORT=587,
        NOTIFICATION_SMTP_USE_TLS=True,
        NOTIFICATION_SMTP_USE_SSL=False,
        NOTIFICATION_SMTP_USERNAME="mailer@example.org",
        NOTIFICATION_SMTP_PASSWORD="secret",
        NOTIFICATION_FROM_EMAIL="noreply@example.org",
    )
    @patch("notification.views.smtplib.SMTP")
    def test_email_send_uses_smtp_configuration(self, mock_smtp):
        smtp_ctx = MagicMock()
        smtp_ctx.send_message.return_value = {}
        mock_smtp.return_value.__enter__.return_value = smtp_ctx

        item = Outbox.objects.create(
            event=self.event,
            channel=self.channel_email,
            priority=self.priority_high,
            status=self.status_queued,
            recipient_user_id=9001,
            recipient_actor_type=self.actor_staff,
            subject="Email Subject",
            body="Email Body",
            to_email="user@example.org",
            max_attempts=2,
            next_attempt_at=timezone.now(),
            idempotency_key=f"email-{timezone.now().timestamp()}",
        )

        response = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status.code, "SENT")
        smtp_ctx.send_message.assert_called_once()

    @override_settings(
        NOTIFICATION_SMS_URL="https://sms.example.org/send",
        NOTIFICATION_SMS_TOKEN="sms-token",
        NOTIFICATION_SMS_AUTH_HEADER="",
        NOTIFICATION_SMS_TIMEOUT=15,
        NOTIFICATION_SMS_SENDER_ID="TTPB",
    )
    @patch("notification.views.urllib.request.urlopen")
    def test_sms_send_uses_provider_configuration(self, mock_urlopen):
        response_obj = MagicMock()
        response_obj.getcode.return_value = 200
        response_obj.read.return_value = b'{"message_id":"abc-123"}'
        mock_urlopen.return_value.__enter__.return_value = response_obj

        item = Outbox.objects.create(
            event=self.event,
            channel=self.channel_sms,
            priority=self.priority_high,
            status=self.status_queued,
            recipient_user_id=9002,
            recipient_actor_type=self.actor_staff,
            subject="SMS Subject",
            body="SMS Body",
            to_phone="255700000000",
            max_attempts=2,
            next_attempt_at=timezone.now(),
            idempotency_key=f"sms-{timezone.now().timestamp()}",
        )

        response = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status.code, "SENT")
        mock_urlopen.assert_called_once()
        sent_request = mock_urlopen.call_args.args[0]
        self.assertEqual(sent_request.full_url, "https://sms.example.org/send")
        self.assertEqual(sent_request.get_header("Content-type"), "application/json")
        self.assertEqual(sent_request.get_header("Accept"), "application/json")
        self.assertEqual(sent_request.get_header("Authorization"), "Bearer sms-token")
        sent_payload = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_payload["from"], "TTPB")
        self.assertEqual(sent_payload["to"], "255700000000")
        self.assertEqual(sent_payload["text"], "SMS Body")

    @override_settings(
        NOTIFICATION_SMS_URL="https://messaging-service.co.tz/api/sms/v1/text/single",
        NOTIFICATION_SMS_TOKEN="",
        NOTIFICATION_SMS_AUTH_HEADER="Basic provider-auth",
        NOTIFICATION_SMS_TIMEOUT=15,
        NOTIFICATION_SMS_SENDER_ID="MUST",
    )
    @patch("notification.views.urllib.request.urlopen")
    def test_sms_send_supports_multiple_destinations(self, mock_urlopen):
        response_obj = MagicMock()
        response_obj.getcode.return_value = 200
        response_obj.read.return_value = b'{"reference":"abc-123"}'
        mock_urlopen.return_value.__enter__.return_value = response_obj

        item = Outbox.objects.create(
            event=self.event,
            channel=self.channel_sms,
            priority=self.priority_high,
            status=self.status_queued,
            recipient_user_id=9003,
            recipient_actor_type=self.actor_staff,
            subject="SMS Subject",
            body="Group SMS Body",
            to_phone='["255762470046","255710167020"]',
            max_attempts=2,
            next_attempt_at=timezone.now(),
            idempotency_key=f"sms-multi-{timezone.now().timestamp()}",
        )

        response = self.client.post(
            reverse("notification:outbox_process"),
            data='{"limit": 10}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status.code, "SENT")
        mock_urlopen.assert_called_once()

        sent_request = mock_urlopen.call_args.args[0]
        self.assertEqual(sent_request.get_header("Authorization"), "Basic provider-auth")
        sent_payload = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_payload["from"], "MUST")
        self.assertEqual(
            sent_payload["to"],
            ["255762470046", "255710167020"],
        )
        self.assertEqual(sent_payload["text"], "Group SMS Body")
        self.assertEqual(sent_payload["reference"], item.idempotency_key)
