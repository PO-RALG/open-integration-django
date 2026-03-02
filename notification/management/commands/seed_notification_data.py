from django.core.management.base import BaseCommand

from notification.models import (
    EventCatalog,
    LkActorType,
    LkChannel,
    LkModule,
    LkPriority,
    LkStatus,
)


class Command(BaseCommand):
    help = "Seed notification lookup tables and event catalog entries."

    def handle(self, *args, **options):
        modules = [
            ("REGISTRATION", "Registration"),
            ("LICENCE", "Licence"),
            ("INTERNSHIP", "Internship"),
            ("CPD", "CPD"),
            ("PAYMENT", "Payment"),
            ("UAA", "User Access & Administration"),
            ("NOTIFICATION", "Notification"),
        ]
        channels = [
            ("SMS", "SMS"),
            ("EMAIL", "Email"),
            ("IN_APP", "In-App"),
        ]
        statuses = [
            ("QUEUED", "Queued"),
            ("SENT", "Sent"),
            ("DELIVERED", "Delivered"),
            ("FAILED", "Failed"),
            ("CANCELLED", "Cancelled"),
        ]
        priorities = [
            ("LOW", "Low", 10),
            ("NORMAL", "Normal", 50),
            ("HIGH", "High", 80),
            ("CRITICAL", "Critical", 100),
        ]
        actor_types = [
            ("TEACHER", "Teacher"),
            ("STAFF", "Staff"),
            ("SYSTEM", "System"),
        ]
        events = [
            ("REGISTRATION_SUBMITTED", "Teacher submitted registration application", "REGISTRATION"),
            ("REGISTRATION_APPROVED", "Registration approved", "REGISTRATION"),
            ("REGISTRATION_REJECTED", "Registration rejected", "REGISTRATION"),
            ("LICENCE_ISSUED", "Licence issued and activated", "LICENCE"),
            ("LICENCE_EXPIRING_SOON", "Licence expiring soon reminder", "LICENCE"),
            ("INTERNSHIP_ASSIGNED", "Internship placement assigned", "INTERNSHIP"),
            ("CPD_NON_COMPLIANT", "Teacher is not compliant with CPD", "CPD"),
            ("PAYMENT_INVOICE_GENERATED", "Invoice generated", "PAYMENT"),
            ("PAYMENT_CONFIRMED", "Payment confirmed/reconciled", "PAYMENT"),
            ("ACCOUNT_ROLE_CHANGED", "User role changed", "UAA"),
        ]

        for code, name in modules:
            LkModule.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )

        for code, name in channels:
            LkChannel.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )

        for code, name in statuses:
            LkStatus.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )

        for code, name, weight in priorities:
            LkPriority.objects.update_or_create(
                code=code,
                defaults={"name": name, "weight": weight, "is_active": True},
            )

        for code, name in actor_types:
            LkActorType.objects.update_or_create(
                code=code,
                defaults={"name": name, "is_active": True},
            )

        for code, description, module_code in events:
            module = LkModule.objects.get(code=module_code)
            EventCatalog.objects.update_or_create(
                code=code,
                defaults={
                    "description": description,
                    "module": module,
                    "is_active": True,
                },
            )

        self.stdout.write(self.style.SUCCESS("Notification seed completed."))
