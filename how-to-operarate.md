# How to Operate TTPB Mediator

## 1. What This Mediator Is

This project is a Django-based mediator control service for TTPB.

Current capabilities:
- Admin UI (`/admin/`) with Jazzmin theming and TTPB branding.
- Core data management for:
  - Clients
  - Mediators
  - Channels
  - External System Registrations
  - Transactions
- Basic API endpoints:
  - `/` service info
  - `/health/` health status for DB and RabbitMQ (when enabled)
- Integration proxy layer:
  - client authentication (`X-Client-Id`/`X-Client-Secret` or Basic auth)
  - channel path/method matching
  - upstream forwarding to mediator endpoint
  - transaction logging

## 2. Core Components

- Django project: `gateway`
- Main app: `mediator`
- Database: PostgreSQL (Docker) or SQLite fallback
- Broker: RabbitMQ (Docker)
- Reverse proxy: Nginx (Docker)
- Runtime helper: `scripts/run_openhim.sh`

Docker services (`docker-compose.yml`):
- Postgres on host port `5433`
- RabbitMQ AMQP on host port `5673`
- RabbitMQ management UI on `15673`
- Nginx on host port `8080` (proxy to Django on `8000`)

## 3. Data Model and How It Works

Main entities (`mediator/models.py`):
- `Client`: external caller credentials and allowed IP list.
- `Mediator`: target mediator service metadata and endpoint URL.
- `Channel`: route definition (path pattern, methods, type, priority) linked to a mediator.
- `ExternalSystemRegistration`: channel-level organization mapping for national ESB routing (`organization`, `api_code`, `push_code`).
- `Transaction`: request/response log object linked to `Client` and `Channel`.

Operational relationship:
- A `Client` calls through a `Channel`.
- A `Channel` points to one `Mediator`.
- Each execution attempt should be recorded as a `Transaction`.

Important constraints:
- Channel path is unique per mediator (`path_pattern + mediator`).
- Transaction has indexed status/time for operational filtering.

## 4. Startup Flow (Automated Script)

Use:

```bash
scripts/run_openhim.sh
```

What it does in order:
1. Loads env vars from `.env.local` (or `--env-file`).
2. Verifies Docker is running.
3. Starts Postgres + RabbitMQ containers.
4. Starts Nginx proxy container.
5. Optionally runs `makemigrations` (if requested).
6. Runs `migrate`.
7. Creates/updates Django superuser from env values.
8. Starts Django server on `0.0.0.0:8000`.

Superuser env vars used:
- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`

## 5. Daily Operations

Normal start:

```bash
scripts/run_openhim.sh
```

Create migrations first:

```bash
scripts/run_openhim.sh --makemigrations
```

Create migrations for one app:

```bash
scripts/run_openhim.sh --makemigrations-app mediator
```

Rebuild containers without deleting DB data:

```bash
scripts/run_openhim.sh --rebuild-no-db
```

Full destructive rebuild (deletes Docker volumes/data):

```bash
scripts/run_openhim.sh --rebuild-all
```

Skip superuser auto setup:

```bash
scripts/run_openhim.sh --no-superuser
```

Seed demonstration data:

```bash
.env/bin/python manage.py seed_demo_data
```

Reset and reseed demonstration data:

```bash
.env/bin/python manage.py seed_demo_data --reset
```

Optional env overrides for demo seeding (`.env.local`):
- `DEMO_SEED_REQUEST_BASE_URL`
- `DEMO_SEED_MEDIATOR_PREFIX`
- `DEMO_SEED_CLIENT_PREFIX`
- `DEMO_SEED_CHANNEL_PREFIX`
- `DEMO_SEED_CLIENTS_JSON`
- `DEMO_SEED_MEDIATORS_JSON`
- `DEMO_SEED_CHANNELS_JSON`

## 6. Admin Workflow

Login at `/admin/` using the configured superuser.

Use the integration tester page (staff users):
- Direct Django: `http://127.0.0.1:8000/admin/mediator/tester/`
- Via Nginx: `http://127.0.0.1:8080/admin/mediator/tester/`
- In admin sidebar: Mediator -> Integration Tester

Suggested setup order:
1. Create `Mediator` records (target services).
2. Create `Client` records (systems calling the mediator).
3. Create `Channel` records (routing definitions).
4. (Optional) Create `ExternalSystemRegistration` per channel/organization when routing via National ESB.
5. Monitor `Transaction` records for status and failures.

Transaction status meanings:
- `pending`: created, not processed yet
- `processing`: in progress
- `successful`: finished successfully
- `failed`: ended with error

## 7. Integration Request Flow

Incoming integration requests should target Django (direct `8000`) or Nginx (`8080`).

Request handling sequence:
1. Authenticate client from headers or Basic auth.
2. Validate client status and source IP against `allowed_ips`.
3. If `MEDIATOR_MODE=hybrid`:
   - match request path/method to active channel(s)
   - select highest-priority matched channel whose mediator is online
   - enforce channel body rules (`requires_request_body`, `request_content_type`)
4. If active external registrations are used:
   - require `X-Organization` header (or `?organization=`).
   - resolve matching organization registration.
   - relay using `esb_utils` and ESB credentials from env variables.
5. In `MEDIATOR_MODE=pure_esb`:
   - skip channel matching entirely
   - route by organization using global registration (registration with empty `channel`)
   - relay all requests via `GOVESB_ENGINE_URL`
6. In hybrid mode without registration match, forward to `channel.mediator.endpoint_url + incoming_path`.
7. Persist transaction details (`request`, `response`, status, errors).
8. Return response and `X-Correlation-Id`.

## 8. Health and Monitoring

Endpoint:
- `GET /health/`

Behavior:
- If `ENABLE_HEALTH_DEPENDENCY_CHECKS=true`, the endpoint actively checks DB and RabbitMQ connectivity.
- If false, endpoint returns basic app health without dependency probing.

Useful checks:
- App: `http://127.0.0.1:8000/`
- App via Nginx: `http://127.0.0.1:8080/`
- Health: `http://127.0.0.1:8000/health/`
- RabbitMQ UI: `http://127.0.0.1:15673`

## 9. Branding and Icons

Branding image files are in `static/branding/`.

Context-specific emblem paths from env:
- `EMBLEM_LOGO_STATIC_PATH`
- `EMBLEM_LOGIN_STATIC_PATH`
- `EMBLEM_ICON_STATIC_PATH`
- `EMBLEM_FAVICON_STATIC_PATH`

Favicon is served via `/favicon.ico` redirect to the configured static branding file.

## 10. Important Notes on Current Scope

This implementation is an operations/control baseline.

Mode switch:
- `MEDIATOR_MODE=hybrid` (default): channel-first routing with optional ESB relay.
- `MEDIATOR_MODE=pure_esb`: no channel matching; route directly to ESB by organization registration.

It currently does **not** yet include:
- RabbitMQ producers/consumers for async transaction processing
- Automated channel dispatch engine

So today, this project is best used for:
- Configuration management
- Admin-based operations
- Health visibility
- Foundation for full mediator processing logic
