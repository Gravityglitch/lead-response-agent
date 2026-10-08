# Lead Response Agent

Production service that answers inbound rental inquiries (SMS / email / web form), classifies
them, drafts a reply from real listing data, proposes showing times, escalates what it cannot
handle, and **never writes to the CRM or sends a message without a human approving it** (or an
explicit per-category allowlist you opt into after a trust period).

Built with Claude (structured outputs + tool use), FastAPI, SQLAlchemy 2, Pydantic, structlog, and
an importable n8n workflow for routing and Slack/email notifications.

> Listings, calendar, and inquiries under `data/` and `evals/` are fixtures. The "CRM" and
> calendar are JSON files behind an adapter seam; the pending-action queue, audit log, and
> processed-inquiry table are in Postgres (SQLite for local/tests).

## Architecture

```
            Twilio SMS ──► POST /webhooks/twilio   (X-Twilio-Signature verified)
  SendGrid Inbound Parse ──► POST /webhooks/sendgrid (?token= shared secret)
      n8n / web form ──► POST /inbound             (X-API-Key, Idempotency-Key)
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │ LeadAgent (pipeline.py)  │
                    │ 1. dedupe by message id  │
                    │ 2. classify  (Claude)    │  read-only tools: lookup_property,
                    │ 3. draft     (Claude)    │  propose_showing_slot, escalate
                    │ 4. policy rails (code)   │
                    │ 5. build actions         │
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐      ┌───────────────┐
                    │ ApprovalQueue (queue.py) │◄────►│ Postgres      │
                    │ pending ─► approve/reject│      │ actions       │
                    │ executes writes ONLY here│      │ audit_log     │
                    └────────────┬─────────────┘      │ inquiries     │
                                 ▼                    └───────────────┘
                 update_lead / book_showing (CRM, calendar)
                 send_reply  ─► DryRun | Twilio Messages | SendGrid Mail
                 escalate    ─► logged; n8n notifies Slack/on-call
```

| Step | Mechanism | Output |
|---|---|---|
| Dedupe | `provider_message_id` (Twilio `MessageSid`, email `Message-ID`) or `Idempotency-Key` header, unique index in `inquiries` | duplicate posts return the stored result with `duplicate: true` |
| Classify | Claude structured output, validated by Pydantic | `availability`, `pricing`, `showing_request`, `maintenance`, `other` + confidence |
| Draft | Claude tool loop over read-only tools | reply text, proposed slot, property id |
| Policy rails | Code, not the model | `maintenance`/`other` always escalate; confidence < 0.6 escalates; no booking on an escalated thread; model failure escalates with a holding reply |
| Actions | Code | `update_lead`, `book_showing`, `send_reply` (approval required), `escalate` (immediate, notify only) |
| Approval | Persistent queue + audit log | `POST /approve/{id}` executes; `POST /reject/{id}` discards; both survive restarts |

Reads and writes are deliberately separate classes: the model can only reach `ToolBox` (read side).
`ApprovalQueue._perform` is the only code that mutates CRM/calendar state or sends a message.

### Reliability

- Claude calls go through `tenacity` (exponential backoff with jitter, `LLM_MAX_RETRIES`) with a
  per-call `LLM_TIMEOUT_SECONDS`; only connection errors, timeouts, 429 and 5xx are retried.
- Token usage and estimated cost are logged per call (`llm.usage` event, `cost_usd` field).
- If the model still fails, the inquiry is classified `other` at confidence 0, escalated, and a
  holding reply is queued for approval. Nothing is dropped; the audit log shows `model failure`.
- Every log line is JSON (structlog) and carries `request_id` (from/echoed in `X-Request-ID`) and
  `inquiry_id`. Audit rows store the `request_id` that caused them.
- A failed write (provider down on `send_reply`) marks the action `failed` with the error in the
  audit log; replay the inquiry to get a fresh action.

## Deploy

### Docker Compose (Postgres + API)

```bash
cp .env.example .env
# set at minimum: API_KEYS=<random>, ANTHROPIC_API_KEY=<key>   (compose sets APP_ENV=production)
make up                      # docker compose up --build -d
docker compose exec api alembic upgrade head   # optional: create_all already ran on startup
curl -s localhost:8000/ready # {"status":"ready","db":true}
```

`APP_ENV=production` refuses to start without `API_KEYS`, a Postgres `DATABASE_URL`, and the
provider credentials for any enabled webhook / live outbound mode. Fix the printed list and restart.

### Local

```bash
uv sync
cp .env.example .env         # leave API_KEYS empty to disable auth locally (logged at startup)
make run                     # uvicorn agent.api:app --reload, SQLite at ./lead_agent.db
```

Without `ANTHROPIC_API_KEY` the server uses the deterministic keyword-rule `FakeLLM`;
`GET /health` reports which backend is active.

### Quick tour

```bash
H='-H content-type:application/json -H X-API-Key:dev'
curl -s -X POST localhost:8000/inbound $H \
  -d '{"channel":"sms","from":"+15550100003","body":"Can I tour Maple Court 2B this weekend?","provider_message_id":"SM1"}'
# -> showing_request, proposed_slot_id S-1, actions update_lead/book_showing/send_reply pending

curl -s localhost:8000/pending -H X-API-Key:dev
curl -s -X POST localhost:8000/approve/<send_reply id> -H X-API-Key:dev    # dry-run send
curl -s -X POST localhost:8000/reject/<update_lead id> -H X-API-Key:dev
curl -s localhost:8000/audit -H X-API-Key:dev
```

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/health` | none | Liveness, active LLM backend, outbound mode |
| `GET` | `/ready` | none | Readiness; runs `SELECT 1`, 503 if the DB is unreachable |
| `GET` | `/metrics` | none | Prometheus metrics (only when `METRICS_ENABLED=true`) |
| `POST` | `/inbound` | API key | Normalised webhook `{channel, from, body, subject?, provider_message_id?}`; honours `Idempotency-Key` |
| `POST` | `/webhooks/twilio` | Twilio signature | Raw Twilio SMS webhook (flag `TWILIO_WEBHOOK_ENABLED`) |
| `POST` | `/webhooks/sendgrid?token=` | shared secret | SendGrid Inbound Parse (flag `SENDGRID_WEBHOOK_ENABLED`) |
| `GET` | `/pending` | API key | Actions awaiting approval |
| `POST` | `/approve/{id}` | API key | Execute a pending action |
| `POST` | `/reject/{id}` | API key | Discard a pending action |
| `GET` | `/inquiries/{id}` | API key | Stored result for an inquiry |
| `POST` | `/inquiries/{id}/replay` | API key | Re-run the pipeline for a stored inquiry (new actions) |
| `GET` | `/audit` | API key | Append-only event log |

## Environment variables

All are read by `agent/config.py` (pydantic-settings) and validated at startup. `.env.example`
lists every one with a comment.

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `production` enforces the checks above |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | JSON lines for shippers; `false` for console |
| `METRICS_ENABLED` | `false` | Exposes `/metrics` via prometheus-fastapi-instrumentator |
| `API_KEYS` | empty | Comma-separated `X-API-Key` values. Empty = auth off (dev only) |
| `DATABASE_URL` | `sqlite:///./lead_agent.db` | Postgres: `postgresql+psycopg://user:pass@host/db` |
| `ANTHROPIC_API_KEY` | empty | Live Claude; otherwise `FakeLLM` |
| `LEAD_AGENT_LLM` | `claude` | `fake` forces the offline backend |
| `LEAD_AGENT_MODEL` | `claude-sonnet-5-5` | Model for both calls |
| `LLM_TIMEOUT_SECONDS` / `LLM_MAX_RETRIES` | `60` / `3` | Per-call timeout and tenacity attempts |
| `LLM_PRICE_INPUT_PER_MTOK` / `LLM_PRICE_OUTPUT_PER_MTOK` | `2.0` / `10.0` | USD per million tokens for the logged cost |
| `TWILIO_WEBHOOK_ENABLED` / `TWILIO_AUTH_TOKEN` / `TWILIO_WEBHOOK_URL` | off | Signature verification uses the auth token; set the URL if a proxy rewrites Host |
| `SENDGRID_WEBHOOK_ENABLED` / `SENDGRID_INBOUND_SECRET` | off | Inbound Parse is unsigned, so `?token=` is required |
| `OUTBOUND_MODE` | `dry_run` | `live` sends via Twilio (SMS) and SendGrid (email / web_form) |
| `TWILIO_ACCOUNT_SID` / `TWILIO_FROM_NUMBER` | empty | Needed for live SMS |
| `SENDGRID_API_KEY` / `SENDGRID_FROM_EMAIL` | empty | Needed for live email |
| `AUTO_SEND_CATEGORIES` | empty | e.g. `pricing,availability`; `send_reply` for those categories executes without approval. Escalated threads never auto-send |

## Runbook

**How approvals work.** Every inquiry produces an `update_lead` and a `send_reply` action, plus
`book_showing` when a slot was proposed on a non-escalated thread. They are inserted as `pending`
rows in `actions`; nothing is written or sent. `POST /approve/{id}` moves the row to `approved`,
performs the write, then `executed` (or `failed`). `POST /reject/{id}` moves it to `rejected`.
Each transition appends to `audit_log` with the HTTP `request_id`. `escalate` actions execute
immediately (they only log; notification is n8n's job). State is in the database, so a restart or
a second replica sees the same queue.

**How to replay.** `GET /inquiries/{id}` shows the stored result. `POST /inquiries/{id}/replay`
re-runs classify + draft and queues a fresh set of actions (the old ones keep their status). Use it
after a model outage (actions created with `model failure` in the escalation reason), after a
failed `send_reply`, or after a prompt change. Duplicate deliveries from providers are handled
automatically: a repeated `provider_message_id` / `Idempotency-Key` returns the stored result with
`duplicate: true` and creates nothing.

**How to rotate keys.** `API_KEYS` accepts a list: set `API_KEYS=old,new`, restart, move callers
(n8n `LEAD_AGENT_API_KEY`) to `new`, then set `API_KEYS=new` and restart. Twilio auth token and
SendGrid inbound secret rotate the same way at the provider plus `.env`; Twilio signature failures
return 403 and are logged with the request id, so a mismatch is visible immediately.

**What to alert on.** `escalated=true` rate, `action.failed` events, `llm.failed` events,
`/ready` returning 503, and the approval rejection rate (a rising rejection rate means the prompt
drifted and the eval set needs the new cases).

**Migrations.** `create_all` runs on startup so a fresh database works without a step. Schema
changes go through Alembic: `uv run alembic revision --autogenerate -m "..."` then
`make migrate` (`DATABASE_URL` overrides `alembic.ini`). `migrations/versions/0001_initial.py`
matches the current models.

## n8n

Import `n8n/workflow.json`. It normalises Twilio / SendGrid-parse / form payloads into the
`/inbound` shape, forwards `MessageSid` as `provider_message_id`, sends `X-API-Key` from the
`LEAD_AGENT_API_KEY` env var and an `Idempotency-Key`, and branches on `escalated`: escalations go
to `#leasing-escalations` plus an on-call email; everything else posts the draft and pending action
ids to `#leasing-approvals`. Replace the `REPLACE_ME` credential ids after import. If you point
Twilio/SendGrid straight at `/webhooks/*` instead, n8n is only needed for notifications.

## Eval

`evals/inquiries.jsonl` holds 20 labeled inquiries (4 per category), including deliberately
ambiguous ones.

```bash
uv run python -m agent.eval          # offline, FakeLLM
uv run python -m agent.eval --live   # Claude, needs ANTHROPIC_API_KEY
```

| Backend | Accuracy | Notes |
|---|---|---|
| `FakeLLM` (keyword rules) | 20/20 | Rules were written alongside this set; a regression floor, not a generalisation claim. `tests/test_eval.py` asserts >= 80%. |
| Claude `claude-sonnet-5-5` | not measured here | No `ANTHROPIC_API_KEY` was present in the environment this build was produced in, so the live number is intentionally left blank rather than invented. Run `--live` with a key and paste the table. |

The eval is deliberately small. In an engagement the first week's real inquiries get labeled and
become the test set.

## Limits

- CRM and calendar are JSON fixtures loaded into memory per process; approved `update_lead` and
  `book_showing` writes mutate that in-memory copy only. Swapping AppFolio / Buildium / Rent
  Manager in means implementing `ToolBox` reads and the two write branches in
  `ApprovalQueue._perform`.
- Dedupe is per `provider_message_id` / `Idempotency-Key`; a provider that retries without an id
  will be processed twice. The n8n workflow falls back to the execution id.
- `/approve` and `/reject` are protected by the shared API key only. Put them behind SSO or
  Slack-signed interactive buttons before exposing them to a wider team.
- SendGrid Inbound Parse cannot be signature-verified; the query-string secret plus TLS is the
  gate. Twilio verification depends on the public URL Twilio was configured with; set
  `TWILIO_WEBHOOK_URL` when a proxy rewrites it.
- Single model for classify and draft; no caching or batching. Classification is a separate call
  so it can move to a cheaper model independently.
- `auto_send` is a trust lever; it is off by default and never applies to escalated threads.

## Development

```
agent/
  config.py        Settings (pydantic-settings), validated at startup
  models.py        Inquiry, Classification, DraftReply, Action, AgentResult
  tools.py         read-only tools + JSON schemas (strict)
  llm.py           ClaudeLLM (retries, usage logging) and FakeLLM
  pipeline.py      dedupe -> classify -> draft -> actions -> queue; model-failure path; replay
  queue.py         persistent approval queue + audit log; the only write path
  outbound.py      OutboundSender protocol: DryRun, Twilio, SendGrid, ChannelRouter
  db.py            SQLAlchemy models, engine, readiness check
  auth.py          X-API-Key dependency
  webhooks.py      /webhooks/twilio (signature), /webhooks/sendgrid (token)
  observability.py structlog config + request-id middleware
  api.py           FastAPI app
  eval.py          classification accuracy report
migrations/        Alembic (0001_initial)
tests/             44 tests, offline, no API key, ~1s
```

```bash
make lint   # ruff check + format --check
make test   # pytest with coverage gate (80%)
```

CI (`.github/workflows/ci.yml`) runs lint, format check, tests with coverage, and a Docker build.

## License

MIT
