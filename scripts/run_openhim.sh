#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="start"
ENV_FILE=".env.local"
RUN_SERVER=1
RUN_MIGRATIONS=1
RUN_MAKEMIGRATIONS=0
MAKEMIGRATIONS_APP=""
RUN_SUPERUSER=1

usage() {
  cat <<'EOF'
Usage: scripts/run_openhim.sh [options]

Options:
  --rebuild-all          Rebuild everything and REMOVE Docker volumes (destructive).
  --rebuild-no-db        Rebuild services but KEEP postgres_data volume.
  --env-file <path>      Path to env file (default: .env.local).
  --makemigrations       Run Django makemigrations before migrate.
  --makemigrations-app   Run makemigrations for a specific app label.
  --no-migrate           Skip Django migrations.
  --no-superuser         Skip superuser create/update from env.
  --no-runserver         Skip Django runserver.
  -h, --help             Show this help.

Examples:
  scripts/run_openhim.sh
  scripts/run_openhim.sh --makemigrations
  scripts/run_openhim.sh --makemigrations-app mediator
  scripts/run_openhim.sh --no-superuser
  scripts/run_openhim.sh --rebuild-no-db
  scripts/run_openhim.sh --rebuild-all
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild-all)
      MODE="rebuild_all"
      shift
      ;;
    --rebuild-no-db)
      MODE="rebuild_no_db"
      shift
      ;;
    --env-file)
      if [[ $# -lt 2 ]]; then
        echo "Error: --env-file requires a path argument." >&2
        exit 1
      fi
      ENV_FILE="$2"
      shift 2
      ;;
    --makemigrations)
      RUN_MAKEMIGRATIONS=1
      shift
      ;;
    --makemigrations-app)
      if [[ $# -lt 2 ]]; then
        echo "Error: --makemigrations-app requires an app label." >&2
        exit 1
      fi
      RUN_MAKEMIGRATIONS=1
      MAKEMIGRATIONS_APP="$2"
      shift 2
      ;;
    --no-migrate)
      RUN_MIGRATIONS=0
      shift
      ;;
    --no-superuser)
      RUN_SUPERUSER=0
      shift
      ;;
    --no-runserver)
      RUN_SERVER=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Error: unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Error: env file '$ENV_FILE' not found." >&2
  exit 1
fi

if [[ ! -x ".env/bin/python" ]]; then
  echo "Error: .env/bin/python not found. Create/activate your virtualenv first." >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Error: docker command not found." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker daemon is not running." >&2
  exit 1
fi

wait_for_postgres() {
  local retries="${POSTGRES_WAIT_RETRIES:-30}"
  local interval="${POSTGRES_WAIT_INTERVAL:-2}"
  local attempt

  echo "Waiting for Postgres to become ready..."
  for ((attempt = 1; attempt <= retries; attempt++)); do
    if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-openhim}" -d "${POSTGRES_DB:-openhim}" >/dev/null 2>&1; then
      echo "Postgres is ready."
      return 0
    fi

    echo "Postgres not ready yet (${attempt}/${retries}); retrying in ${interval}s..."
    sleep "$interval"
  done

  echo "Error: Postgres did not become ready in time." >&2
  return 1
}

echo "Loading environment from $ENV_FILE"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

case "$MODE" in
  rebuild_all)
    echo "Rebuild mode: FULL RESET (this removes postgres_data and rabbitmq_data volumes)."
    docker compose down -v --remove-orphans
    docker compose up -d --force-recreate postgres rabbitmq nginx
    ;;
  rebuild_no_db)
    echo "Rebuild mode: preserving Postgres data volume."
    docker compose stop postgres rabbitmq nginx || true
    docker compose rm -f postgres rabbitmq nginx || true
    docker compose up -d --force-recreate postgres rabbitmq nginx
    ;;
  start)
    echo "Starting services without rebuild."
    docker compose up -d postgres rabbitmq nginx
    ;;
  *)
    echo "Error: unsupported mode '$MODE'." >&2
    exit 1
    ;;
esac

if [[ "$RUN_MIGRATIONS" -eq 1 || "$RUN_SUPERUSER" -eq 1 ]]; then
  wait_for_postgres
fi

if [[ "$RUN_MAKEMIGRATIONS" -eq 1 ]]; then
  if [[ -n "$MAKEMIGRATIONS_APP" ]]; then
    echo "Running Django makemigrations for app: $MAKEMIGRATIONS_APP"
    .env/bin/python manage.py makemigrations "$MAKEMIGRATIONS_APP"
  else
    echo "Running Django makemigrations"
    .env/bin/python manage.py makemigrations
  fi
fi

if [[ "$RUN_MIGRATIONS" -eq 1 ]]; then
  echo "Running Django migrations"
  .env/bin/python manage.py migrate
fi

if [[ "$RUN_SUPERUSER" -eq 1 ]]; then
  if [[ -z "${DJANGO_SUPERUSER_USERNAME:-}" || -z "${DJANGO_SUPERUSER_EMAIL:-}" || -z "${DJANGO_SUPERUSER_PASSWORD:-}" ]]; then
    echo "Skipping superuser setup: set DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, and DJANGO_SUPERUSER_PASSWORD in $ENV_FILE"
  else
    echo "Ensuring Django superuser exists from environment"
    .env/bin/python manage.py shell -c "
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ['DJANGO_SUPERUSER_USERNAME']
email = os.environ['DJANGO_SUPERUSER_EMAIL']
password = os.environ['DJANGO_SUPERUSER_PASSWORD']

user, created = User.objects.get_or_create(
    username=username,
    defaults={
        'email': email,
        'is_staff': True,
        'is_superuser': True,
    },
)

user.email = email
user.is_staff = True
user.is_superuser = True
user.set_password(password)
user.save()

print('Created superuser:' if created else 'Updated superuser:', username)
"
  fi
fi

if [[ "$RUN_SERVER" -eq 1 ]]; then
  echo "Starting Django runserver on 0.0.0.0:8000"
  exec .env/bin/python manage.py runserver 0.0.0.0:8000
fi

echo "Done."
