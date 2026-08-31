# Telegram Order Management & Routing Bot

Production-ready Telegram bot that receives orders from source channels, numbers
them daily, re-sends them to work groups **without a forward header**, detects
success or failure from operator replies and reactions, dispatches the result to
separate destinations, and — only once that dispatch actually succeeded — places
an acknowledgement reaction back on the operator's message.

Everything functional is configured **from inside Telegram**. Only the bot token,
the bootstrap super-admin list and the database credentials come from the
environment.

---

## Table of contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
- [BotFather setup](#botfather-setup)
- [Telegram permissions](#telegram-permissions)
- [Local installation](#local-installation)
- [Coolify installation](#coolify-installation)
- [Environment variables](#environment-variables)
- [Configuring the bot](#configuring-the-bot)
- [Database migration](#database-migration)
- [Backup and restore](#backup-and-restore)
- [Update](#update)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Known Telegram limitations](#known-telegram-limitations)
- [Known project limitations](#known-project-limitations)

---

## Overview

```
Source Channel
      ↓
   New Order
      ↓
     Bot  ──►  allocate daily order number (order1, order2, …)
      ↓
Copy / resend WITHOUT forward header
      ↓
  Work Group
      ↓
   Operator  ──►  Reply / Image / Text / Reaction
      ↓
 Rule Engine  ──►  SUCCESS / FAILED / CONFLICT
      ↓
Result Destination
      ↓
Telegram confirms the send
      ↓
Acknowledgement Reaction
      ↓
 Reports / Audit
```

### Technology

| Concern | Choice |
| --- | --- |
| Language | Python 3.13 |
| Bot framework | aiogram 3.x (long polling) |
| Database | PostgreSQL 16 |
| ORM | SQLAlchemy 2.x async + asyncpg |
| Migrations | Alembic |
| Config | pydantic-settings |
| Logging | structlog (JSON, secrets scrubbed) |
| Packaging | Docker + Docker Compose |
| Tests | pytest + pytest-asyncio |

No domain, TLS certificate, webhook or Redis is required. The architecture leaves
room for all of them later (see [Future direction](#future-direction)).

---

## Architecture

Business logic never lives in a Telegram handler. Each layer has one job:

```
Telegram Handler        app/bot/handlers/
      ↓
Application Service     app/orders/, app/services/
      ↓
Domain / Rule Engine    app/rules/
      ↓
Dispatch Service        app/dispatch/
      ↓
Acknowledgement Service app/acknowledgements/
      ↓
Repository              app/database/repositories/
      ↓
PostgreSQL
```

### Project structure

```
app/
├── main.py                   # entry point: long polling + health server
├── health.py                 # internal /health endpoint
├── bot/
│   ├── handlers/             # orders, operators, admin panel
│   ├── keyboards/            # inline keyboards + typed callback data
│   ├── middlewares/          # service injection, idempotency, error guard
│   ├── filters/              # admin / operator / chat scoping
│   └── states/               # FSM states for admin prompts
├── orders/                   # intake, numbering, routing
├── routing/                  # (route resolution lives in repositories)
├── rules/                    # signal extraction, matching, rule engine
├── acknowledgements/         # targeting + acknowledgement service
├── dispatch/                 # result dispatch outbox
├── reports/                  # statistics
├── admin/                    # admin screen rendering
├── audit/                    # (audit repository lives in database/)
├── telegram/                 # gateway, composer, payload, retry, errors
├── database/
│   ├── models/               # SQLAlchemy models
│   ├── repositories/         # data access
│   └── engine.py             # async engine + session scope
├── services/                 # finalizer, signals, notifications, bootstrap
├── config/                   # settings
└── utils/                    # enums, time, logging

tests/                        # 158 tests against a real PostgreSQL
alembic/                      # migrations
docker/entrypoint.sh          # wait for DB → migrate → exec app
```

### How the hard guarantees are achieved

| Guarantee | Mechanism |
| --- | --- |
| Two simultaneous orders never share a number | One statement: `INSERT … ON CONFLICT (business_date, scope_key) DO UPDATE SET last_number = last_number + 1 RETURNING last_number`. PostgreSQL serialises writers on the unique index. |
| The counter restarts at 1 each local day | The counter key **is** the business date, derived from `Asia/Tehran` wall-clock time. No cron job; correct even if the bot was offline at 00:00. |
| An album is one order | Partial unique index on `(source_chat_id, source_media_group_id) WHERE source_media_group_id IS NOT NULL`. Every part maps to the first part's order. |
| A redelivered update creates nothing | `processed_updates` ledger claimed in an outer middleware, plus a unique `(source_chat_id, source_message_id)`. |
| Result dispatched exactly once | Outbox row per `(order, destination)`; claimed `PENDING → SENDING` with a conditional `UPDATE … RETURNING` before the API call. |
| Acknowledged exactly once | The same claim pattern on the order's `acknowledgement_status`. |
| Never acknowledge an undelivered result | The acknowledgement gate reads the dispatch rows and applies the configured policy. |
| No long lock across a network call | Status decisions run in a short locked transaction; every Telegram call happens outside it. |
| A restart loses nothing | All state is in PostgreSQL. Startup releases rows stuck in `SENDING`/`APPLYING` and resumes them. |

### Database tables

`admins`, `operators`, `operator_work_groups`, `source_channels`, `work_groups`,
`routes`, `daily_counters`, `orders`, `order_source_messages`, `order_deliveries`,
`order_delivery_messages`, `status_rules`, `rule_signals`, `rule_text_patterns`,
`rule_reactions`, `order_signals`, `result_destinations`, `result_dispatches`,
`acknowledgement_configs`, `acknowledgement_events`, `status_events`, `settings`,
`audit_logs`, `processed_updates`, `notification_throttle`.

---

## Features

### Order intake
- Multiple source channels, added and removed from the admin panel.
- Text, photo, video, document, audio, voice, animation, caption and albums.
- An album (shared `media_group_id`) is **one** order; every message id is mapped.
- Duplicate Telegram updates never create a second order.
- `edited_channel_post` never creates a new order.

### Numbering
- `order1`, `order2`, … reset daily on the `Asia/Tehran` business date.
- Atomic allocation; two concurrent orders get N and N+1.
- Scope `GLOBAL` (default) or `PER_SOURCE`.
- Prefix and format configurable (`{prefix}{number}` → `order125`, `ORD-{number}` → `ORD-125`).

### Routing
- Any source → one or many work groups; routes can be enabled, disabled or deleted.
- Delivery uses `sendMessage` / `sendPhoto` / `sendMediaGroup` / `copyMessage` —
  **never `forwardMessage`** — so no "Forwarded from" header appears. Original
  formatting entities are preserved and re-offset for the prepended order number.

### Detection
- Independent rule sets for SUCCESS and FAILED.
- Signals: reply photo / video / document / audio / voice / animation, reply text, reaction.
- Each signal individually enabled or disabled.
- Text patterns with `EXACT` / `CONTAINS` / `REGEX` and case sensitivity.
- Accepted detection reactions configured per status.
- Mode `ANY` or `ALL` per status. Signals are persisted, so an `ALL` rule can be
  completed by events minutes apart or across a restart.
- Only an authorized operator can produce a signal.

### Result dispatch
- Separate destinations per status; several per status supported.
- Each destination can be marked *required* and one is *primary*.
- Per-destination state (`SENT` / `FAILED`) recorded individually.

### Acknowledgement reactions
- Configured independently for SUCCESS and FAILED (emoji, target, policy, retry).
- Applied **only after** Telegram confirms the result dispatch.
- Target modes: `SMART` (default), `TRIGGER_MESSAGE`, `ORDER_MESSAGE`.
- Dispatch policies: `ALL_REQUIRED_DESTINATIONS` (default), `ANY_DESTINATION`, `PRIMARY_DESTINATION`.
- Idempotent and restart-safe; a failed reaction never rolls back the order.
- The bot's own reaction can never re-enter the rule engine.

### Operations
- Dashboard, reports (today / yesterday / last 7 / last 30 / custom / by source / by operator).
- Order search by number, with manual override and a full per-order audit trail.
- System status, audit log browser, admin notifications with spam protection.
- Structured JSON logs with secrets scrubbed; `/health` endpoint; graceful shutdown.

---

## BotFather setup

1. Open [@BotFather](https://t.me/BotFather) → `/newbot` → pick a name and username.
2. Copy the token into `BOT_TOKEN`.
3. **Disable privacy mode** so the bot can see operator replies in groups:
   `/mybots` → your bot → *Bot Settings* → *Group Privacy* → **Turn off**.
   Without this the bot receives only commands and its own messages, and no reply
   will ever be detected.
4. Optionally set commands: `/setcommands` →
   ```
   start - Open the admin panel
   order - Find an order by number
   id - Show your user id and this chat id
   ```

Get your own numeric user id by sending `/id` to the bot.

---

## Telegram permissions

### Source channel
- Add the bot to the channel (as an **administrator** — Telegram only sends
  `channel_post` updates to channel admins).
- No posting permission is needed; the bot only reads.

### Work group
The bot must be able to:

| Need | Requirement |
| --- | --- |
| Send the order copy | *Send Messages* |
| Receive operator replies | **Group Privacy off** in BotFather |
| Receive reaction updates | Bot must be an **administrator** of the group |
| Place the acknowledgement reaction | Administrator, and the emoji must be permitted in the chat |

> Telegram only delivers `message_reaction` updates to bots that are
> administrators in the chat. If reaction detection appears dead, this is almost
> always the reason.

Supergroups are recommended over basic groups.

### Result destinations (success / failure)
- Add the bot as an administrator with **Post Messages** permission.

### Reaction availability
A chat can restrict which emoji are allowed. The admin panel shows the allowed
set under **Test Access**, warns when the configured acknowledgement emoji is not
in it, and offers **Test Reaction** to try it against a real message. If Telegram
rejects the reaction at runtime the acknowledgement is recorded as `FAILED`, an
admin notification is sent, and the order keeps its status — the bot never crashes.

---

## Local installation

### Scripted

```bash
git clone <repository>
cd <repository>
bash install.sh
```

The script checks Docker, asks for the bot token and super-admin ids, generates a
strong database password, writes `.env` (mode 600), builds the images, starts
PostgreSQL and the bot, runs migrations and prints the status.

### Manual

```bash
git clone <repository>
cd <repository>

cp .env.example .env
nano .env          # set BOT_TOKEN, SUPERADMIN_IDS, POSTGRES_PASSWORD, DATABASE_URL

docker compose up -d --build
docker compose logs -f bot
```

Migrations run automatically at container start
(`RUN_MIGRATIONS_ON_START=true`). To run them yourself:

```bash
docker compose run --rm bot alembic upgrade head
```

### Running without Docker (development)

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/telegram_orders"
export BOT_TOKEN="..." SUPERADMIN_IDS="123456789"

alembic upgrade head
python -m app.main
```

---

## Coolify installation

1. **Create Project** — in Coolify, *Projects* → *+ New* → name it.
2. **Add Resource** — inside the project, *+ New Resource*.
3. **Connect Git Repository** — pick your Git source and this repository/branch.
4. **Select Docker Compose** — build pack *Docker Compose*, compose file
   `docker-compose.yml`.
5. **Add Environment Variables** — at minimum:
   ```
   BOT_TOKEN=<token from BotFather>
   SUPERADMIN_IDS=<your numeric telegram id>
   POSTGRES_DB=telegram_orders
   POSTGRES_USER=telegram_orders
   POSTGRES_PASSWORD=<a long random password>
   TZ=Asia/Tehran
   APP_ENV=production
   LOG_LEVEL=INFO
   ```
   `DATABASE_URL` is assembled by the compose file from the `POSTGRES_*` values,
   so you do not need to set it separately.
6. **Check the PostgreSQL persistent volume** — the compose file declares a named
   volume `postgres_data` mounted at `/var/lib/postgresql/data`. Confirm it
   appears under *Storages* and is **not** ephemeral. This is what makes orders
   survive redeploys.
7. **Deploy** — press *Deploy*.
8. **Check logs** — *Logs* → `bot`. A healthy start prints
   `migrations complete` then `bot_started`.
9. **Run migrations** — they run automatically. To run them explicitly, use
   Coolify's *Execute Command* on the `bot` service:
   `alembic upgrade head`.
10. **Send `/start` to the bot** from a super-admin account — the panel opens.
11. **Configure the source channel** — 📥 Source Channels → ➕ Add → send the chat id.
12. **Configure the work group** — 👥 Work Groups → ➕ Add, then 🔀 Routing to connect them.
13. **Configure result destinations** — 📦 Result Destinations → Success / Failure.
14. **Configure success / failure rules** — ✅ Success Rules and ❌ Failure Rules.
15. **Configure acknowledgement reactions** — 👍 Result Reactions.
16. **Test the full flow** — post an order in the source channel, have an operator
    reply or react, and confirm the result channel receives it and the
    acknowledgement reaction appears.

No domain, SSL certificate or public port is required: the bot uses long polling
outbound only. The `/health` endpoint listens on port 8080 **inside** the
container for Docker/Coolify health checks.

---

## Environment variables

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `BOT_TOKEN` | ✅ | — | Token from @BotFather |
| `SUPERADMIN_IDS` | ✅ | — | Comma-separated Telegram user ids that bootstrap as Super Admin |
| `POSTGRES_DB` | | `telegram_orders` | Database name |
| `POSTGRES_USER` | | `telegram_orders` | Database user |
| `POSTGRES_PASSWORD` | ✅ | — | Database password |
| `POSTGRES_HOST` | | `postgres` | Database host |
| `POSTGRES_PORT` | | `5432` | Database port |
| `DATABASE_URL` | | assembled | Full async DSN; overrides the parts above |
| `APP_ENV` | | `production` | Environment label |
| `LOG_LEVEL` | | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `LOG_FORMAT` | | `json` | `json` or `console` |
| `TZ` | | `Asia/Tehran` | Business timezone (IANA name, never a fixed offset) |
| `HEALTH_HOST` | | `0.0.0.0` | Health server bind address |
| `HEALTH_PORT` | | `8080` | Health server port |
| `RUN_MIGRATIONS_ON_START` | | `true` | Run `alembic upgrade head` on container start |
| `TELEGRAM_MAX_RETRIES` | | `3` | Retry attempts for retryable Telegram errors |
| `TELEGRAM_RETRY_BASE_DELAY` | | `1.0` | Backoff base in seconds |
| `ADMIN_NOTIFICATION_COOLDOWN` | | `300` | Seconds between identical admin alerts |

Everything else — sources, work groups, routes, operators, destinations, rules,
texts, reactions, acknowledgement settings, counter scope, order prefix — lives in
PostgreSQL and is edited from the admin panel. Nothing is hard coded.

---

## Configuring the bot

Send `/start` as a super admin. The panel offers:

```
📊 Dashboard          📥 Source Channels
👥 Work Groups        🔀 Routing
👤 Operators          ✅ Success Rules
❌ Failure Rules      👍 Result Reactions
📦 Result Destinations 📈 Reports
🔎 Find Order         ⚙️ Settings
🩺 System Status      📝 Audit Logs
```

### Recommended order

1. **📥 Source Channels** — add each channel orders arrive in (numeric chat id;
   run `/id` inside the chat to read it). **Test Access** verifies the bot's rights.
2. **👥 Work Groups** — add each group operators work in.
3. **🔀 Routing** — connect sources to work groups (one source may feed several).
4. **👤 Operators** — add the users allowed to close orders. Scope them to all
   groups or to specific ones.
5. **📦 Result Destinations** — add the success and failure targets. Mark which are
   *required* and pick a *primary*.
6. **✅ Success Rules / ❌ Failure Rules** — enable the signals, set `ANY` or `ALL`,
   add text patterns and accepted detection reactions.
7. **👍 Result Reactions** — enable the acknowledgement per status, choose the
   emoji, target mode and dispatch policy, and use **Test Reaction**.

### Worked example

```
Success Rules              Result Reactions → Success Acknowledgement
  Detection: Enabled         Status:   🟢 Enabled
  Mode:      ANY             Reaction: 👍
  Reply Photo:  🟢           Target:   SMART
  Reaction:     🟢 (✅)      Policy:   All Required Destinations
```

An operator replying with a photo, **or** reacting ✅, finalises the order. The
order is sent to `@successful_orders`; once Telegram confirms the send, the bot
puts 👍 on the operator's photo — or, when the trigger was a reaction and there is
no operator message, on the original `order36` message.

### Configuration validation

The panel refuses or warns about unusable configurations:

- Mode cannot be switched to `ALL` while no signal is enabled.
- The last enabled signal cannot be turned off while the mode is `ALL`.
- An acknowledgement cannot be enabled before an emoji is chosen.
- A warning appears when success and failure acknowledgements use the same emoji.
- A warning appears when a rule enables Reply Text with no pattern, or Reaction
  with no accepted emoji.
- A warning appears when an acknowledgement is enabled with no result destination.
- The chosen emoji is checked against each destination's allowed reactions.
- Regular expressions are validated before they are saved.

---

## Database migration

Alembic manages the schema. Migrations are **additive** — no deploy drops data,
and `drop_all()` is never used in production.

```bash
docker compose run --rm bot alembic upgrade head     # apply
docker compose run --rm bot alembic current          # show current revision
docker compose run --rm bot alembic history          # list revisions
docker compose run --rm bot alembic downgrade -1     # roll back one (rarely needed)
```

Without Docker: `alembic upgrade head` with `DATABASE_URL` exported.

---

## Backup and restore

### Backup

```bash
bash backup.sh
# → backups/2026-08-24_02-00-00.sql.gz
```

Backups older than `BACKUP_RETENTION_DAYS` (default 30) are pruned. Schedule it
from cron:

```cron
0 2 * * * cd /opt/order-bot && bash backup.sh >> /var/log/order-bot-backup.log 2>&1
```

### Restore

```bash
docker compose stop bot

gunzip -c backups/2026-08-24_02-00-00.sql.gz \
  | docker compose exec -T postgres psql -U telegram_orders -d telegram_orders

docker compose start bot
```

The dump is taken with `--clean --if-exists`, so restoring over an existing
database replaces it cleanly.

---

## Update

```bash
bash update.sh
```

It takes a safety backup, pulls, rebuilds, applies migrations in a one-off
container (so a failed migration never leaves a half-started bot), and restarts.
Set `SKIP_BACKUP=true` to skip the backup step.

On Coolify, press *Redeploy*; migrations run from the entrypoint.

---

## Tests

```bash
pip install -r requirements-dev.txt
createdb order_bot_test
export TEST_DATABASE_URL="postgresql+asyncpg://postgres@127.0.0.1:5432/order_bot_test"
pytest
```

The suite runs against a **real PostgreSQL** database, because the guarantees
under test are database behaviour: atomic counter allocation, partial unique
indexes, and conditional `UPDATE … RETURNING` claims. Telegram is replaced by a
fake gateway that records what would have been sent.

Coverage includes every scenario the specification requires:

| Scenario | Test |
| --- | --- |
| Counter reset across the day boundary | `test_counter_resets_on_the_next_business_day` |
| Concurrent orders get distinct numbers | `test_concurrent_allocation_never_duplicates` |
| 50-way concurrent allocation is gapless | `test_high_concurrency_allocation_is_gapless_and_unique` |
| Album of five photos = one order | `test_album_of_five_photos_is_one_order` |
| Duplicate update = one order | `test_duplicate_source_message_creates_one_order` |
| Reaction detection off ⇒ PENDING | `test_reaction_detection_disabled_leaves_order_pending` |
| Success `ANY` fires on reaction alone | `test_success_any_mode_fires_on_reaction_alone` |
| Failure `ALL` needs both signals | `test_failure_all_mode_needs_every_enabled_signal` |
| Success acknowledgement on a reply | `test_success_acknowledgement_lands_on_the_operator_reply` |
| Failure acknowledgement on a reply | `test_failure_acknowledgement_lands_on_the_operator_reply` |
| Reaction trigger ⇒ order message | `test_reaction_trigger_acknowledges_the_order_message` |
| Destination failure ⇒ no acknowledgement | `test_failed_dispatch_withholds_the_acknowledgement` |
| Acknowledgement failure ⇒ no rollback | `test_reaction_failure_leaves_the_order_success_and_dispatch_sent` |
| Duplicate finalisation happens once | `test_multiple_simultaneous_success_signals_dispatch_once` |
| Full flow across a restart | `test_flow_completes_across_a_bot_restart` |
| Conflict blocks everything | `test_conflict_blocks_dispatch_and_acknowledgement` |
| No forward header | `test_text_order_is_resent_with_number_and_no_forward_header` |
| Startup, health, graceful shutdown | `test_main_run_starts_and_shuts_down_gracefully` |
| Every admin keyboard packs, fits 64 bytes and round-trips | `test_every_keyboard_builds_and_round_trips` |
| Route resolution explains why an order goes nowhere | `test_resolver_explains_a_disabled_route` |
| Env super admins cannot be demoted or removed | `test_env_super_admin_cannot_be_removed` |

---

## Troubleshooting

**The bot does not answer `/start`.**
Your user id must be in `SUPERADMIN_IDS`. Send `/id` to the bot to read it, then
add it and redeploy. Check `docker compose logs bot` for `bot_started`.

**Orders are not picked up from the channel.**
The channel must be added *and enabled* in 📥 Source Channels, and the bot must be
an administrator there (Telegram only sends `channel_post` to channel admins).
Use **Test Access**.

**Orders arrive but never reach the work group.**
Check 🔀 Routing — a source with no enabled route goes nowhere; the audit log
records `ORDER_ROUTE_FAILED`. Confirm the bot can send messages in the group.

**Operator replies do nothing.**
Three usual causes: Group Privacy is still on in BotFather; the user is not in
👤 Operators (or is disabled, or not assigned to that group); the relevant signal
is not enabled in the rule.

**Reactions do nothing.**
The bot must be an **administrator** in the work group to receive
`message_reaction` updates. Then check that the Reaction signal is enabled for
that status and the emoji is in the accepted list.

**The result is sent but no acknowledgement appears.**
Open 🔎 Find Order → the order. `acknowledgement_status` explains it:
`PENDING` means the dispatch gate is not satisfied yet; `FAILED` shows the
Telegram error (usually a reaction the chat does not allow); `NOT_REQUIRED` means
the acknowledgement is disabled for that status.

**Nothing is dispatched and the status is `CONFLICT`.**
Success and failure rules matched simultaneously. Resolve it in
🔎 Find Order → *Mark Success* / *Mark Failed*, and narrow the rules so the two
cannot both fire.

**Health check fails.**
`curl http://127.0.0.1:8080/health` inside the container. `database: error` means
the DSN or the database is wrong; `telegram_bot: initialising` means the token was
rejected or the network is blocked.

**Migrations fail on deploy.**
Read `docker compose logs bot`. Restore the latest backup if needed, fix the
cause, then `docker compose run --rm bot alembic upgrade head`.

---

## Known Telegram limitations

These are Bot API constraints, not project shortcuts. Each is handled explicitly
rather than being silently dropped.

1. **A bot may set only one reaction per message.** `setMessageReaction` accepts a
   list, but bots are limited to a single entry. The acknowledgement is therefore
   exactly one emoji per status, not a set. *Handled:* the panel accepts one emoji
   and says so.

2. **`message_reaction` updates require the bot to be a chat administrator** and
   must be explicitly requested in `allowed_updates`. *Handled:* the polling call
   lists `message_reaction` and `message_reaction_count`; the README and the
   panel's Test Access flag the admin requirement.

3. **Anonymous and channel-post reactions carry no user.** They cannot be
   attributed to an operator. *Handled:* such reactions are ignored rather than
   guessed at.

4. **A chat can restrict which emoji are allowed**, and there is no reliable API
   to test a reaction without applying it. *Handled:* `getChat.available_reactions`
   is checked when saving, a **Test Reaction** action applies it to a real message
   on demand, and a runtime rejection is recorded as `acknowledgement_status =
   FAILED` with an admin alert — never a crash and never a status rollback.

5. **Telegram never tells you how many messages an album contains.** Parts arrive
   as separate updates. *Handled:* parts are persisted immediately and collapsed
   onto one order by a partial unique index; routing waits a short debounce (2s)
   after the last part. Only the timer is in memory — a restart mid-album still
   routes the order from the database.

6. **`copyMessage` cannot change the text of a text message** (only captions of
   media). *Handled:* text orders are rebuilt with `sendMessage` and shifted
   entities; media orders use a caption; albums are rebuilt with
   `sendMediaGroup`; anything else falls back to a header message plus
   `copyMessage`. `forwardMessage` is never used, so no forward header ever appears.

7. **Caption limit 1024, text limit 4096 characters.** *Handled:* when prepending
   the order number would overflow, the number is sent as its own message so no
   original content is truncated.

8. **`sendMediaGroup` cannot mix documents with photos/videos**, and voice notes,
   video notes and stickers cannot appear in an album at all. *Handled:* the
   composer only groups album-compatible types and falls back otherwise.

9. **Telegram may redeliver an update** if the bot dies before confirming the
   polling offset. *Handled:* the `processed_updates` ledger.

10. **Long polling gives no delivery receipt for reactions the bot removes**, and
    Telegram does not push an update for a bot's own reaction. *Handled:* the
    extractor rejects the bot's own user id defensively regardless.

---

## Known project limitations

1. **Editing a source message does not update the delivered copy.** An
   `edited_channel_post` is logged and ignored, exactly as specified. Re-editing
   work-group copies would be a separate feature.

2. **Acknowledgement with no result destination configured.** With nothing to
   dispatch, the gate is vacuously open and the reaction *is* applied. The panel
   warns about this combination. If you want the reaction to depend on a real
   delivery, configure at least one destination.

3. **Album debounce is 2 seconds.** An album whose parts arrive more than 2s apart
   (very rare) would be routed in two batches. The order itself remains one order.

4. **Reaction removal never reverses a terminal order.** By design: once SUCCESS
   or FAILED has been dispatched and acknowledged, removing the reaction cannot
   undo it. Use Manual Override.

5. **FSM state is in memory.** Admin panel wizards (typing a chat id, a pattern)
   reset on restart. No order data is affected. Redis storage is a drop-in change.

6. **Single-bot, single-tenant.** One `BOT_TOKEN` per deployment.

7. **Long polling only.** Webhooks would need a domain and TLS; the gateway and
   handler layers are unchanged by that switch.

8. **Retries are bounded and in-process.** Failed dispatches and acknowledgements
   are retried on the next pipeline run (manual retry from the panel, or startup
   recovery). There is no background scheduler yet.

### Future direction

The architecture already accommodates, without redesign: a web admin panel and
REST API (the service layer is Telegram-agnostic), multiple bots and businesses
(scope keys and per-row configuration), Redis FSM storage, a Celery/queue worker
(the outbox is already there), webhooks, SLA and timeout rules, CSV/Excel export,
OCR, external API and payment-verification signals (the rule engine consumes
persisted signals from any source).

---

## License

Provided as-is for the commissioning project.
