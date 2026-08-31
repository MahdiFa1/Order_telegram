#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-shot installer for a plain server: checks Docker, builds the .env,
# starts the stack and runs migrations.
#
#   bash install.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'

info()  { echo "${GREEN}==>${RESET} $*"; }
warn()  { echo "${YELLOW}==>${RESET} $*"; }
fail()  { echo "${RED}==>${RESET} $*" >&2; exit 1; }

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    fail "Neither 'docker compose' nor 'docker-compose' is available."
  fi
}

echo "${BOLD}Telegram Order Management & Routing Bot — installer${RESET}"
echo

# --- 1. Docker -------------------------------------------------------------
info "Checking Docker"
command -v docker >/dev/null 2>&1 || fail "Docker is not installed. See https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || fail "Docker is installed but the daemon is not reachable. Start it (or add your user to the 'docker' group) and retry."
compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required."
info "Docker is ready"

# --- 2. .env ---------------------------------------------------------------
if [ -f .env ]; then
  warn ".env already exists — keeping it. Delete it first if you want to start over."
else
  info "Creating .env"

  read -r -p "Telegram BOT_TOKEN (from @BotFather): " BOT_TOKEN
  [ -n "$BOT_TOKEN" ] || fail "BOT_TOKEN cannot be empty."

  read -r -p "Super Admin Telegram user ID(s), comma separated: " SUPERADMIN_IDS
  [ -n "$SUPERADMIN_IDS" ] || fail "At least one Super Admin ID is required, or nobody can open the panel."

  read -r -p "Timezone [Asia/Tehran]: " TZ_INPUT
  TZ_VALUE="${TZ_INPUT:-Asia/Tehran}"

  # Generate a strong database password rather than shipping a default.
  if command -v openssl >/dev/null 2>&1; then
    DB_PASSWORD="$(openssl rand -hex 24)"
  else
    DB_PASSWORD="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi

  cat > .env <<ENVEOF
BOT_TOKEN=${BOT_TOKEN}
SUPERADMIN_IDS=${SUPERADMIN_IDS}

POSTGRES_DB=telegram_orders
POSTGRES_USER=telegram_orders
POSTGRES_PASSWORD=${DB_PASSWORD}
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Assembled by the application from the POSTGRES_* values above.
DATABASE_URL=

APP_ENV=production
LOG_LEVEL=INFO
LOG_FORMAT=json
TZ=${TZ_VALUE}

HEALTH_HOST=0.0.0.0
HEALTH_PORT=8080
RUN_MIGRATIONS_ON_START=true

TELEGRAM_MAX_RETRIES=3
TELEGRAM_RETRY_BASE_DELAY=1.0
ADMIN_NOTIFICATION_COOLDOWN=300
ENVEOF

  chmod 600 .env
  info "Wrote .env (mode 600) with a generated database password"
fi

# --- 3. Build --------------------------------------------------------------
info "Building containers (this can take a few minutes on first run)"
compose build

# --- 4. Start --------------------------------------------------------------
info "Starting PostgreSQL"
compose up -d postgres

info "Waiting for PostgreSQL to become healthy"
for _ in $(seq 1 60); do
  if compose ps postgres | grep -qi healthy; then break; fi
  sleep 2
done

info "Starting the bot (migrations run automatically on start)"
compose up -d bot

# --- 5. Status -------------------------------------------------------------
sleep 5
info "Current status"
compose ps

echo
info "Recent bot logs"
compose logs --tail 30 bot || true

cat <<'DONE'

------------------------------------------------------------------
Installation finished.

Next steps:
  1. Open Telegram and send /start to your bot.
  2. 📥 Source Channels   — add the channel(s) orders arrive in.
  3. 👥 Work Groups       — add the group(s) operators work in.
  4. 🔀 Routing           — connect each source to its work group(s).
  5. 👤 Operators         — add the users allowed to close orders.
  6. 📦 Result Destinations — where SUCCESS / FAILED orders are sent.
  7. ✅/❌ Rules           — how success and failure are detected.
  8. 👍 Result Reactions  — the acknowledgement reactions.

Useful commands:
  docker compose logs -f bot     # follow logs
  docker compose ps              # status
  bash update.sh                 # pull + rebuild + migrate
  bash backup.sh                 # database dump
------------------------------------------------------------------
DONE
