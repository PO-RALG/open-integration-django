import json
import os
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from mediator.models import Channel, Client, Mediator, Transaction

DEMO_MEDIATOR_PREFIX = "urn:ttpb:demo:"
DEMO_CLIENT_PREFIX = "demo-"
DEMO_CHANNEL_PREFIX = "/demo/"


class Command(BaseCommand):
    help = "Seed demonstration data for the TTPB mediator."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo records first, then reseed.",
        )
        parser.add_argument(
            "--transactions-per-channel",
            type=int,
            default=4,
            help="How many demo transactions to generate per channel (default: 4).",
        )

    def handle(self, *args, **options):
        self.demo_mediator_prefix = os.getenv(
            "DEMO_SEED_MEDIATOR_PREFIX",
            DEMO_MEDIATOR_PREFIX,
        )
        self.demo_client_prefix = os.getenv(
            "DEMO_SEED_CLIENT_PREFIX",
            DEMO_CLIENT_PREFIX,
        )
        self.demo_channel_prefix = os.getenv(
            "DEMO_SEED_CHANNEL_PREFIX",
            DEMO_CHANNEL_PREFIX,
        )
        self.demo_clients = self._load_json_list_env(
            "DEMO_SEED_CLIENTS_JSON",
            self._default_clients(),
        )
        self.demo_mediators = self._load_json_list_env(
            "DEMO_SEED_MEDIATORS_JSON",
            self._default_mediators(),
        )
        self.demo_channels = self._load_json_list_env(
            "DEMO_SEED_CHANNELS_JSON",
            self._default_channels(),
        )
        self.demo_request_base_url = (
            os.getenv("DEMO_SEED_REQUEST_BASE_URL", "https://api.ttpb.local")
            .strip()
            .rstrip("/")
        )

        transactions_per_channel = options["transactions_per_channel"]
        if transactions_per_channel < 1:
            self.stderr.write("--transactions-per-channel must be at least 1")
            return

        if options["reset"]:
            self._reset_demo_data()

        clients = self._seed_clients()
        mediators = self._seed_mediators()
        channels = self._seed_channels(mediators)
        tx_count = self._seed_transactions(channels, clients, transactions_per_channel)

        self.stdout.write(self.style.SUCCESS("Demo seeding completed."))
        self.stdout.write(
            f"Clients: {len(clients)} | Mediators: {len(mediators)} | "
            f"Channels: {len(channels)} | Transactions processed: {tx_count}"
        )

    def _reset_demo_data(self):
        Transaction.objects.filter(
            Q(channel__mediator__urn__startswith=self.demo_mediator_prefix)
            | Q(client__client_id__startswith=self.demo_client_prefix)
            | Q(request_url__contains=self.demo_channel_prefix)
        ).delete()

        Channel.objects.filter(
            Q(mediator__urn__startswith=self.demo_mediator_prefix)
            | Q(path_pattern__startswith=self.demo_channel_prefix)
        ).delete()

        Mediator.objects.filter(urn__startswith=self.demo_mediator_prefix).delete()
        Client.objects.filter(client_id__startswith=self.demo_client_prefix).delete()

        self.stdout.write(self.style.WARNING("Deleted existing demo records."))

    def _seed_clients(self):
        clients = []
        for index, item in enumerate(self.demo_clients, start=1):
            client, _ = Client.objects.update_or_create(
                client_id=self._required(item, "client_id", f"client #{index}"),
                defaults={
                    "name": self._required(item, "name", f"client #{index}"),
                    "client_secret": self._required(
                        item,
                        "client_secret",
                        f"client #{index}",
                    ),
                    "allowed_ips": item.get("allowed_ips", []),
                    "is_active": bool(item.get("is_active", True)),
                },
            )
            clients.append(client)

        return clients

    def _seed_mediators(self):
        now = timezone.now()
        mediators = []
        for index, item in enumerate(self.demo_mediators, start=1):
            mediator, _ = Mediator.objects.update_or_create(
                urn=self._required(item, "urn", f"mediator #{index}"),
                defaults={
                    "name": self._required(item, "name", f"mediator #{index}"),
                    "version": item.get("version", "1.0.0"),
                    "endpoint_url": self._required(
                        item,
                        "endpoint_url",
                        f"mediator #{index}",
                    ),
                    "is_online": bool(item.get("is_online", True)),
                    "last_heartbeat": now,
                },
            )
            mediators.append(mediator)

        return mediators

    def _seed_channels(self, mediators):
        by_urn = {mediator.urn: mediator for mediator in mediators}

        channels = []
        for index, item in enumerate(self.demo_channels, start=1):
            mediator_urn = self._required(item, "mediator_urn", f"channel #{index}")
            if mediator_urn not in by_urn:
                raise CommandError(
                    f"Unknown mediator_urn '{mediator_urn}' in channel #{index}"
                )

            channel, _ = Channel.objects.update_or_create(
                name=self._required(item, "name", f"channel #{index}"),
                defaults={
                    "description": item.get("description", ""),
                    "path_pattern": self._required(
                        item,
                        "path_pattern",
                        f"channel #{index}",
                    ),
                    "methods": item.get("methods", []),
                    "requires_request_body": bool(
                        item.get("requires_request_body", False)
                    ),
                    "request_content_type": item.get("request_content_type", ""),
                    "request_body_example": item.get("request_body_example", ""),
                    "request_body_schema": item.get("request_body_schema", {}),
                    "channel_type": item.get(
                        "channel_type",
                        Channel.ChannelType.HTTPS,
                    ),
                    "priority": int(item.get("priority", 1)),
                    "is_active": bool(item.get("is_active", True)),
                    "mediator": by_urn[mediator_urn],
                },
            )
            channels.append(channel)

        return channels

    def _seed_transactions(self, channels, clients, transactions_per_channel):
        status_cycle = [
            Transaction.Status.SUCCESSFUL,
            Transaction.Status.FAILED,
            Transaction.Status.PROCESSING,
            Transaction.Status.PENDING,
        ]

        tx_processed = 0
        now = timezone.now()

        for channel_idx, channel in enumerate(channels):
            for i in range(transactions_per_channel):
                status = status_cycle[(channel_idx + i) % len(status_cycle)]
                client = clients[(channel_idx + i) % len(clients)]
                method = (channel.methods[0] if channel.methods else "get").upper()

                correlation_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ttpb-demo:{channel.name}:{i}",
                )

                started_at = now - timedelta(minutes=(channel_idx * 10 + i * 3))
                completed_at = None
                response_status = None
                error_message = ""
                response_body = ""

                if status == Transaction.Status.SUCCESSFUL:
                    completed_at = started_at + timedelta(seconds=2)
                    response_status = 200
                    response_body = '{"ok": true, "message": "Processed"}'
                elif status == Transaction.Status.FAILED:
                    completed_at = started_at + timedelta(seconds=1)
                    response_status = 500
                    error_message = "Demo downstream service timeout"
                    response_body = '{"ok": false, "error": "Timeout"}'

                tx, _ = Transaction.objects.update_or_create(
                    correlation_id=correlation_id,
                    defaults={
                        "channel": channel,
                        "client": client,
                        "status": status,
                        "request_method": method,
                        "request_url": (
                            f"{self.demo_request_base_url}{channel.path_pattern}"
                        ),
                        "request_headers": {
                            "X-Demo-Client": client.client_id,
                            "Content-Type": "application/json",
                        },
                        "request_body": '{"teacher_id": "TTPB-DEMO-1001"}',
                        "response_status_code": response_status,
                        "response_headers": (
                            {"Content-Type": "application/json"}
                            if response_status
                            else {}
                        ),
                        "response_body": response_body,
                        "error_message": error_message,
                        "completed_at": completed_at,
                    },
                )

                Transaction.objects.filter(pk=tx.pk).update(started_at=started_at)
                tx_processed += 1

        return tx_processed

    def _required(self, item, key, context):
        value = item.get(key)
        if value in (None, ""):
            raise CommandError(f"Missing '{key}' in {context}")
        return value

    def _load_json_list_env(self, env_name, default):
        raw = (os.getenv(env_name) or "").strip()
        if not raw:
            return default

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError(f"{env_name} must be valid JSON: {exc}") from exc

        if not isinstance(parsed, list):
            raise CommandError(f"{env_name} must be a JSON array")
        if not parsed:
            raise CommandError(f"{env_name} must contain at least one item")
        return parsed

    def _default_clients(self):
        return [
            {
                "client_id": "demo-portal",
                "name": "Demo Teacher Portal",
                "client_secret": "portal-secret",
                "allowed_ips": ["127.0.0.1"],
                "is_active": True,
            },
            {
                "client_id": "demo-mobile",
                "name": "Demo Mobile App",
                "client_secret": "mobile-secret",
                "allowed_ips": ["127.0.0.1", "10.0.0.10"],
                "is_active": True,
            },
            {
                "client_id": "demo-lms",
                "name": "Demo LMS Connector",
                "client_secret": "lms-secret",
                "allowed_ips": ["10.10.0.5"],
                "is_active": True,
            },
        ]

    def _default_mediators(self):
        return [
            {
                "urn": "urn:ttpb:demo:registration",
                "name": "Demo Registration Mediator",
                "version": "1.0.0",
                "endpoint_url": "https://demo-registration.ttpb.local/api",
                "is_online": True,
            },
            {
                "urn": "urn:ttpb:demo:licensing",
                "name": "Demo Licensing Mediator",
                "version": "1.1.0",
                "endpoint_url": "https://demo-licensing.ttpb.local/api",
                "is_online": True,
            },
        ]

    def _default_channels(self):
        return [
            {
                "name": "Demo Teacher Registration",
                "description": "Register new teacher records from demo clients.",
                "path_pattern": "/demo/teachers/register",
                "methods": ["post"],
                "requires_request_body": True,
                "request_content_type": "application/json",
                "request_body_example": '{"teacher_id":"TTPB-DEMO-1001","full_name":"Demo Teacher"}',
                "channel_type": Channel.ChannelType.HTTPS,
                "priority": 1,
                "is_active": True,
                "mediator_urn": "urn:ttpb:demo:registration",
            },
            {
                "name": "Demo License Verification",
                "description": "Verify existing teacher licenses.",
                "path_pattern": "/demo/teachers/license/verify",
                "methods": ["get"],
                "requires_request_body": False,
                "request_content_type": "",
                "request_body_example": "",
                "channel_type": Channel.ChannelType.HTTPS,
                "priority": 2,
                "is_active": True,
                "mediator_urn": "urn:ttpb:demo:licensing",
            },
            {
                "name": "Demo License Renewal",
                "description": "Process teacher license renewal requests.",
                "path_pattern": "/demo/teachers/license/renew",
                "methods": ["post"],
                "requires_request_body": True,
                "request_content_type": "application/json",
                "request_body_example": '{"teacher_id":"TTPB-DEMO-1001","renewal_year":2026}',
                "channel_type": Channel.ChannelType.HTTPS,
                "priority": 3,
                "is_active": True,
                "mediator_urn": "urn:ttpb:demo:licensing",
            },
        ]
