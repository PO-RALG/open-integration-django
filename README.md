# TTPB Mediator (Postgres + RabbitMQ)

## Run infrastructure with Docker

```bash
docker compose up -d postgres rabbitmq nginx
```

This starts:
- Postgres: `localhost:5433`
- RabbitMQ AMQP: `localhost:5673`
- RabbitMQ Management UI: `http://localhost:15673`
- Nginx reverse proxy: `http://localhost:8080` -> Django `http://host.docker.internal:8000`

Optional local fake domain (`openhim.local`):

```bash
echo "127.0.0.1 openhim.local" | sudo tee -a /etc/hosts
```

Then use:
- `http://openhim.local:8080/`
- `http://openhim.local:8080/admin/`
- `http://openhim.local:8080/admin/mediator/tester/`

API documentation:
- OpenAPI JSON: `http://127.0.0.1:8000/api/docs/openapi.json`
- Swagger UI: `http://127.0.0.1:8000/api/docs/swagger/`
- Through nginx (example): `http://127.0.0.1:8080/api/docs/swagger/`

## One-command runner

Use the helper script:

```bash
scripts/run_openhim.sh
```

Useful modes:

```bash
# Create migration files before applying migrate
scripts/run_openhim.sh --makemigrations

# Create migration files for a single app
scripts/run_openhim.sh --makemigrations-app mediator

# Skip automatic superuser setup
scripts/run_openhim.sh --no-superuser

# Recreate services but keep DB data
scripts/run_openhim.sh --rebuild-no-db

# Full rebuild (removes DB + RabbitMQ volumes)
scripts/run_openhim.sh --rebuild-all
```

## Demo Seeder

Seed realistic demonstration records (clients, mediators, channels, transactions):

```bash
.env/bin/python manage.py seed_demo_data
```

Reset existing demo records first:

```bash
.env/bin/python manage.py seed_demo_data --reset
```

Seed demo data can be overridden from `.env.local`:
- `DEMO_SEED_REQUEST_BASE_URL`
- `DEMO_SEED_MEDIATOR_PREFIX`
- `DEMO_SEED_CLIENT_PREFIX`
- `DEMO_SEED_CHANNEL_PREFIX`
- `DEMO_SEED_CLIENTS_JSON`
- `DEMO_SEED_MEDIATORS_JSON`
- `DEMO_SEED_CHANNELS_JSON`

Example:

```bash
DEMO_SEED_REQUEST_BASE_URL=http://openhim.local:8087
DEMO_SEED_CLIENTS_JSON=[{"client_id":"demo-env-client","name":"Demo Env Client","client_secret":"env-secret","allowed_ips":["127.0.0.1"],"is_active":true}]
DEMO_SEED_MEDIATORS_JSON=[{"urn":"urn:ttpb:demo:env","name":"Demo Env Mediator","version":"2.0.0","endpoint_url":"https://env-mediator.example.org/api","is_online":true}]
DEMO_SEED_CHANNELS_JSON=[{"name":"Demo Env Channel","description":"Channel from env configuration","path_pattern":"/demo/env/ping","methods":["get"],"requires_request_body":false,"request_content_type":"","request_body_example":"","channel_type":"https","priority":1,"is_active":true,"mediator_urn":"urn:ttpb:demo:env"}]
```

## Notification Module

New app: `notification`

Implemented SQL-derived models for:
- lookup tables (`lk_module`, `lk_channel`, `lk_status`, `lk_priority`, `lk_actor_type`)
- `event_catalog`, `template`, `user_preference`
- `event_rule`, `event_rule_channel`
- `outbox`, `delivery_log`, `in_app_message`

Seed initial lookup/event data:

```bash
.env/bin/python manage.py seed_notification_data
```

Notification API endpoints:
- `GET /api/notification/`
- `GET /api/notification/lookups/`
- `GET /api/notification/events/?module=...&active=true|false`
- `GET /api/notification/rules/?event_code=...&active=true|false`
- `GET /api/notification/templates/?event_code=...&channel=...&language=...&active=true|false`
- `GET|POST /api/notification/preferences/<actor_type_code>/<user_id>/`
- `POST /api/notification/emit/`
- `GET /api/notification/outbox/?status=...&channel=...&due=true|false&limit=100`
- `POST /api/notification/outbox/process/`
- `GET /api/notification/outbox/<outbox_id>/delivery-logs/`
- `GET /api/notification/inbox/<user_id>/?unread=true|false&limit=100`
- `POST /api/notification/inbox/<message_id>/read/`

Notification transport configuration (in `.env.local`):
- SMTP:
  - `NOTIFICATION_SMTP_HOST`
  - `NOTIFICATION_SMTP_PORT`
  - `NOTIFICATION_SMTP_USERNAME`
  - `NOTIFICATION_SMTP_PASSWORD`
  - `NOTIFICATION_SMTP_USE_TLS`
  - `NOTIFICATION_SMTP_USE_SSL`
  - `NOTIFICATION_SMTP_TIMEOUT`
  - `NOTIFICATION_FROM_EMAIL`
- SMS provider:
  - `NOTIFICATION_SMS_URL`
  - `NOTIFICATION_SMS_TOKEN`
  - `NOTIFICATION_SMS_AUTH_HEADER` (raw header, e.g. `Basic ...`, takes precedence over token)
  - `NOTIFICATION_SMS_TIMEOUT`
  - `NOTIFICATION_SMS_SENDER_ID`

Notification processing flow implemented:
1. Receive event (`event_code` + payload)
2. Validate event is active
3. Load active rules
4. If no active rule, suppress event (`disposition=suppressed`, reason `no_active_rule`)
5. Resolve recipients by each rule `recipient_policy`
6. Load user preferences
7. Select channels from rule channels filtered by preferences
8. Resolve templates by channel + language (with `en` fallback)
9. Build messages from template placeholders
10. Generate idempotency key per message
11. Insert message to outbox as `QUEUED`
12. Worker endpoint `/api/notification/outbox/process/` picks due messages
13. Send via SMS/Email or create In-App message
14. Write `delivery_log` per attempt
15. On success set status `SENT` (SMS/Email) or `DELIVERED` (In-App)
16. On failure increment `attempt_count`:
    - retry path: set backoff `next_attempt_at`, keep `FAILED` (temporary)
    - final path (`attempt_count >= max_attempts`): set `FAILED` (final)
    - critical final failures include `escalation_required=true` metadata

## Integration Proxy Layer

Channel-based forwarding is active through the Django app with client auth.

Browser tester page (staff/admin login required):

```text
http://127.0.0.1:8000/admin/mediator/tester/
```

Through nginx:

```text
http://127.0.0.1:8080/admin/mediator/tester/
```

It is also linked inside the Mediator section of the admin sidebar (Jazzmin custom link).

Authentication supported:
- Header auth:
  - `X-Client-Id`
  - `X-Client-Secret`
- HTTP Basic auth (`client_id:client_secret`)

Behavior:
- In `MEDIATOR_MODE=hybrid` (default):
  - Matches request path/method to active `Channel`.
  - Verifies linked mediator is online.
- If active `ExternalSystemRegistration` records are used:
  - Requires `X-Organization` header (or `?organization=` query parameter).
  - Resolves the organization registration (`api_code` / `push_code`).
  - Relays through National ESB using `esb_utils`.
- In `MEDIATOR_MODE=pure_esb`:
  - Skips channel path/method matching.
  - Requires `X-Organization` header (or `?organization=`).
  - Uses global registration (`ExternalSystemRegistration` with empty `channel`) first.
  - Relays every request through National ESB.
- Enforces channel request rules:
  - `requires_request_body`
  - `request_content_type` (if set)
- Forwards request to `channel.mediator.endpoint_url + incoming_path`.
- Logs full transaction state in `Transaction`.

ESB mode selection (when registration exists):
- Default: uses `api_code` when present, otherwise `push_code`.
- Override via `X-ESB-Mode: normal|push` or `?esb_mode=normal|push`.

Asynchronous proxy mode:
- Add `?async=true` or `Prefer: respond-async` to enqueue request processing.
- Requests are processed by an in-process background worker thread in Django.
- Immediate response: HTTP `202 Accepted` with:
  - `correlation_id`
  - `status_url` (`/transactions/<correlation_id>/`)
- Poll transaction status with client credentials:
  - `GET /transactions/<correlation_id>/`
  - Returns status (`pending`, `processing`, `successful`, `failed`) and response/error fields when available.

Example request:

```bash
curl -X POST 'http://127.0.0.1:8000/integrations/teachers?sync=true' \\
  -H 'Content-Type: application/json' \\
  -H 'X-Client-Id: demo-integrator' \\
  -H 'X-Client-Secret: demo-secret' \\
  -d '{"teacher_id":"T-1"}'
```

Async request example:

```bash
curl -X POST 'http://127.0.0.1:8000/integrations/teachers?async=true' \\
  -H 'Content-Type: application/json' \\
  -H 'X-Client-Id: demo-integrator' \\
  -H 'X-Client-Secret: demo-secret' \\
  -d '{"teacher_id":"T-1"}'
```

## Configure Django to use Docker services

Copy env template and adjust if needed:

```bash
cp .env.docker.example .env.local
```

If Django runs on your host machine, keep:
- `POSTGRES_HOST=localhost`
- `POSTGRES_PORT=5433`
- `RABBITMQ_URL=amqp://guest:guest@localhost:5673//`

If Django runs as a container in the same compose network, use:
- `POSTGRES_HOST=postgres`
- `RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672//`

Then export the variables (example):

```bash
set -a
source .env.local
set +a
```

Run migrations and start Django:

```bash
.env/bin/python manage.py migrate
.env/bin/python manage.py runserver
```

## Notes

- Set `DB_ENGINE=postgres` to enable PostgreSQL.
- `RABBITMQ_URL` should point to your broker.
- `NGINX_PORT` controls published nginx proxy port.
- `DJANGO_CSRF_TRUSTED_ORIGINS` must include the exact browser origin (scheme + host + port), for example `http://localhost:8087`.
- `MEDIATOR_FORWARD_TIMEOUT` controls upstream forwarding timeout in seconds.
- `TRANSACTION_BODY_MAX_LENGTH` controls request/response body size persisted in `Transaction`.
- National ESB variables:
  `MEDIATOR_MODE` (`hybrid` or `pure_esb`),
  `GOVESB_TOKEN_URL`, `GOVESB_ENGINE_URL`, `GOVESB_GRANT_TYPE`,
  `ESB_CLIENT_ID`, `ESB_CLIENT_SECRET`, `ESB_REQUEST_DATA_FORMAT`,
  `CLIENT_PRIVATE_KEY`, `CLIENT_PUBLIC_KEY`, `GOV_ESB_PUBLIC_KEY`.
- Emblem sizing is context-based using:
  `EMBLEM_LOGO_STATIC_PATH`, `EMBLEM_LOGIN_STATIC_PATH`,
  `EMBLEM_ICON_STATIC_PATH`, `EMBLEM_FAVICON_STATIC_PATH`.
- `scripts/run_openhim.sh` auto-creates/updates the Django superuser from:
  `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`,
  `DJANGO_SUPERUSER_PASSWORD`.
- `/health/` checks DB + RabbitMQ when `ENABLE_HEALTH_DEPENDENCY_CHECKS=true`.
