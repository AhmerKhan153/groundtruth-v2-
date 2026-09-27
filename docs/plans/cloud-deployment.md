# Final plan: scale-to-zero cloud deployment (Cloud Run + Atlas + DeepSeek)

Status: **in progress — phases 1–2 done; phase 3 built and tested, awaiting a real-tap check with a dev bot.** Tick items as they land.

## Checkpoints

Each phase ends in a working state and is its own commit. The "why" for the order
is in section 11.

**Phase 1: Restructure**
- [x] `src/` → `groundtruth/` package, no `sys.path` hacks
- [x] Unused code deleted (placeholders, LinkedIn, scheduler, `app.py`, `models/`, old Telegram integration)
- [x] `llm.py` → `complete(prompt)` via the openai SDK (timeout 45s, 1 retry)
- [x] `pipeline.py` replaces LangGraph; fail-closed floors kept
- [x] Prompt length contradiction fixed (5 vs 6 paragraphs)
- [x] `sources/hackernews.py`: parallel fetch, 72h filter
- [x] Pinned `requirements.txt` + `requirements-dev.txt`; `.env.example` rewritten
- [x] Check: `python -m groundtruth.pipeline <url>` prints a grounded draft
- [x] Check: same command against DeepSeek (thinking off), draft quality reviewed
- [x] Prompts restructured as system + user messages for DeepSeek, cache-compatible (see notes)
- [x] Paywall/JS pages fail closed: extractor replies `NO_ARTICLE`

**Phase 2: Store**
- [x] `store.py`: lazy client, UTC tz-aware, TTL per status, atomic transitions, stale reclaim
- [x] hn_id dedup (unique index), `announced` flag, stored brief, `meta.last_run_at`
- [x] `scripts/init_db.py` creates indexes (nothing at import)
- [x] Check: transition tests pass against a real MongoDB

Notes from phases 1–2:
- **Local models dropped (2026-09-27).** DeepSeek is the only model, locally and on
  Cloud Run. LOCAL_LLM_GPU.md is obsolete (delete in phase 5).
- **DeepSeek settings:** `LLM_MODEL=deepseek-flash`,
  `LLM_EXTRA_BODY={"thinking": {"type": "disabled"}}` (thinking is on by default).
  Temperatures: extract 0.0, write 1.3 (per DeepSeek's parameter guide).
- **Prompt cache layout** (groundtruth/prompts.py): static system prompt first,
  per-request content last; a rewrite replays the first write call and appends the
  previous draft + rewrite instruction. Measured: rewrite 1024/1168 prompt tokens
  from cache (88%). A test enforces the verbatim prefix.
- **Known prompt gaps to watch:** the closing question is often its own paragraph
  (5 + 1); occasional small true-but-not-in-brief details (e.g. "his rent").
- Fetcher mis-decodes pages that don't declare a charset (`Iâm`); fix later.
- Test by hand: `python -m groundtruth.pipeline` (pick from HN, `r` to rewrite; the
  log lines show cache hits).
- HN sourcing uses one pooled session: 56s → 12s on the local link (TLS handshakes
  dominate); expect far less on Cloud Run.
- New collection is `drafts` (the old local `articles` collection is untouched and unused).

**Phase 3: Web app**
- [x] `main.py` (3 endpoints, JSON logs for Cloud Logging), `telegram.py`, `handlers.py`, auth checks
- [x] `scripts/dev_poll.py` (long-polls with the real `handle_update`)
- [x] Check: handler flows + endpoint auth tested (43 tests: repeats, failures, retries, other chats, bad secrets)
- [x] Check: uvicorn boots; `import main` 1.3s on a Linux filesystem (15s from `/mnt/c` is the WSL mount, not the app)
- [ ] Separate dev bot token in `.env` *(needs you: @BotFather → /newbot)*
- [ ] Check: full flow on the laptop with real taps via `dev_poll.py --source`

**Phase 4: Deploy**
- [ ] Dockerfile, `deploy.sh`, Atlas cluster + scoped user, webhook, Scheduler job
- [ ] $1 budget alert, Artifact Registry cleanup policy, prepaid DeepSeek balance
- [ ] Check: forced source → pick → rewrite → approve from the phone; a second unforced run returns `skipped`

**Phase 5: Docs and hardening**
- [ ] README, MONGODB_RUNBOOK (Atlas), PROJECT_STRUCTURE rewritten; LOCAL_LLM_GPU deleted
- [ ] Fetcher + handler tests; error reporting reviewed after real runs

## 1. Decisions

- Runs **once every 2 days**. There is no long-running process: every step is one
  HTTP request that does its work and exits, and Cloud Run scales to zero between
  them.
- **DeepSeek API** (`deepseek-flash`, OpenAI-compatible, thinking disabled) is the
  only model, locally and in production. Local models were dropped.
- **MongoDB Atlas M0** (free) is kept for idempotency, dedup and the stored brief.
  TTL deletes in-flight records; only `approved` records persist.
- **No LinkedIn integration.** Approve stores the draft as `approved` (terminal,
  permanent) and sends a copy-ready message. The user posts to LinkedIn by hand.
  Nothing ever reads approved records back.

## 2. Architecture

```
 ┌──────────────────┐  daily 09:00, POST       ┌──────────────── Cloud Run service "groundtruth" ────────────────┐
 │ Cloud Scheduler  │ ───────────────────────► │  POST /jobs/source   (X-Job-Secret)                              │
 └──────────────────┘                          │  POST /telegram      (X-Telegram-Bot-Api-Secret-Token)           │
                                               │  GET  /healthz                                                    │
 ┌──────────────────┐  webhook, POST           │                                                                   │
 │ Telegram servers │ ───────────────────────► │  FastAPI (sync handlers) · min 0 / max 1 instance · scale to 0    │
 └────────▲─────────┘                          └──────┬───────────────┬────────────────┬───────────────┬──────────┘
          │ Bot API (send/edit/answer)                │               │                │               │
          └───────────────────────────────────────────┘               ▼                ▼               ▼
                                                            MongoDB Atlas M0     DeepSeek API     HN API + article sites
                                                            (drafts, meta)       (chat/completions)  (read-only GETs)
```

Five moving parts: **Cloud Scheduler, Cloud Run, Atlas, DeepSeek, Telegram.** No
queues, no VM, no Secret Manager, no NAT.

## 3. API inventory

### 3a. Endpoints we expose: 3

| # | Method + path | Caller | Auth | Does | Response | Typical time |
|---|---|---|---|---|---|---|
| 1 | `POST /jobs/source[?force=1]` | Cloud Scheduler (or you, with curl) | `X-Job-Secret` header, constant-time compare | skip check → source HN → store → send pick list | `200 {"status": "skipped"\|"sent", "count": n}`; `401` on a bad secret; `500` on failure (Scheduler retries) | ~3–5s |
| 2 | `POST /telegram` | Telegram | `X-Telegram-Bot-Api-Secret-Token` header, plus `chat.id == TELEGRAM_CHAT_ID` | dispatch a button tap: pick / approve / reject / rewrite | **always `200`** once authenticated; errors go to the chat | <1s (approve/reject) to 15–40s (pick) |
| 3 | `GET /healthz` | you / uptime checks | none | returns `ok`; touches no dependencies | `200` | ms |

Callback data format (≤ 64 bytes): `p:<id>` pick, `a:<id>` approve, `r:<id>` reject,
`w:<id>` rewrite, where `<id>` is the 24-char Mongo ObjectId.

### 3b. External APIs we call: 4 services, 8 operations

| Service | Operation | Used by | Per call |
|---|---|---|---|
| Hacker News (Firebase) | `GET /v0/topstories.json` | source | 1 |
| | `GET /v0/item/{id}.json` | source | up to 100, 16 in parallel |
| Article sites | `GET <story url>` | pick | 1 (15s timeout) |
| DeepSeek | `POST /chat/completions` | pick (×2: extract + write), rewrite (×1) | 45s timeout, 1 retry |
| Telegram Bot API | `sendMessage` | source, pick, approve | |
| | `editMessageText` | pick, approve, reject, rewrite | |
| | `answerCallbackQuery` | every tap, first thing | |
| | `setWebhook` | deploy script only (one-time) | |

MongoDB Atlas is reached through the driver, not HTTP (one pooled client per warm
instance).

Local dev only: `deleteWebhook` + `getUpdates` (in `scripts/dev_poll.py`), so the
same handlers run on your laptop without ngrok.

## 4. Request flows

### Flow A: source (Cloud Scheduler → `/jobs/source`)

```
Scheduler ─POST─► /jobs/source
  1. verify X-Job-Secret                                   (401 if wrong)
  2. Mongo: meta.last_run_at < 44h ago and not force? ───► 200 {"status":"skipped"}
  3. HN: topstories → first 100 items (16 threads)
  4. filter: has url, score ≥ 50, posted in last 72h
  5. Mongo: drop hn_ids already stored (unique index) → rank score + 2×comments → top 6
  6. Mongo: insert_many as "sourced", announced=false (ordered=False, dup keys ignored)
  7. Mongo: load every sourced record with announced=false   ← also picks up a failed previous attempt
  8. Telegram sendMessage: numbered list + buttons [p:<id>] ×n
  9. Mongo: mark those announced=true; meta.last_run_at = now
 10. 200 {"status":"sent","count":n}                       → instance idles → scale to 0
```

Retry safety: if step 8 fails, the handler returns 500 and Scheduler retries. The
unique index stops duplicate inserts, and step 7 re-sends what never went out.
`last_run_at` is only written after a successful send.

### Flow B: pick (tap a number)

```
Telegram ─POST─► /telegram  {callback_query: data="p:<id>"}
  1. verify secret header + chat id
  2. answerCallbackQuery                                   (stops the button spinner)
  3. Mongo claim: sourced → drafting                       (miss → toast "Already picked", 200)
  4. sendMessage "✍️ Drafting: <title>…" → store msg_id
  5. GET article → clean → fail-closed if body < 400 chars
  6. DeepSeek #1 extract → fail-closed if brief < 120 chars
  7. DeepSeek #2 write
  8. Mongo: drafting → drafted  {brief, draft}
  9. editMessageText(msg_id): draft + source link + [✅ Approve][❌ Reject][✍️ Rewrite]
 10. 200
 on any failure in 5–7: Mongo → failed {error}; edit msg_id to "⚠️ Skipped: <reason>"; 200
```

### Flow C: approve

```
  1. verify · 2. answerCallbackQuery
  3. Mongo claim: drafted → approved {approved_at, expire_at: null}   (miss → toast, 200)
  4. editMessageText: draft + "✅ Approved" (buttons removed)
  5. sendMessage: post text only (no footer, no link) → long-press → Copy → paste into LinkedIn
  6. 200
```

### Flow D: reject

```
  1. verify · 2. answer · 3. claim drafted → rejected · 4. editMessageText "❌ Rejected" · 5. 200
```

### Flow E: rewrite

```
  1. verify · 2. answer
  3. Mongo claim: drafted → drafting                       (miss → toast, 200)
  4. editMessageText "✍️ Rewriting…" (buttons removed)
  5. DeepSeek write from the stored brief + REWRITE_PROMPT_SUFFIX   (1 call, no re-fetch)
  6. Mongo: drafting → drafted {draft}
  7. editMessageText: new draft + buttons
  8. 200
```

### Duplicates and crashes

- **Telegram resends** an update if the reply is slow or fails. The resend's claim
  (step 3) matches nothing, so it answers with a toast and exits. Work happens once.
- **Double taps** are handled the same way.
- **Instance dies mid-draft:** the record stays `drafting`. A claim may take over a
  `drafting` record whose `updated_at` is more than 5 minutes old, so tapping again
  recovers it.
- **Stale buttons** on old messages: the claim fails (wrong status) or the record has
  expired → toast "No longer available".

## 5. Data model

`drafts` (indexes: unique `hn_id`, TTL on `expire_at` with `expireAfterSeconds: 0`):
```js
{ _id, hn_id, title, url, score, comments,
  status,          // sourced | drafting | drafted | approved | rejected | failed
  announced,       // pick list delivered (Flow A retry safety)
  brief, draft, msg_id, error,
  created_at, updated_at, approved_at,   // UTC, timezone-aware
  expire_at }      // null = keep forever
```
`meta`: `{ _id: "source_job", last_run_at }`

| Status | `expire_at` | Why |
|---|---|---|
| sourced / drafting / drafted / failed | now + 7d | buttons stay valid for 3 cycles; hn_id blocks repeats |
| rejected | now + 14d | dedup only |
| approved | `null` | permanent archive of your approved posts |

```
sourced ──pick──► drafting ──ok──► drafted ──approve──► approved   (terminal)
                     │                │
                     └─fail─► failed  ├──reject──► rejected
                                      └──rewrite─► drafting
```

## 6. Cloud configuration

### Cloud Run

| Setting | Value | Why |
|---|---|---|
| Region | one Tier-1 region, the same one as Atlas | lower latency to the DB, cheapest tier |
| Billing | request-based (CPU only during requests) | idle costs nothing |
| min / max instances | 0 / 1 | scale to zero; one instance keeps it single-user and simple |
| Concurrency | 8 | a slow pick doesn't block an approve; FastAPI runs sync handlers in threads |
| Request timeout | 300s | worst-case pick: fetch 15s + 2 × (45s × 2 tries) ≈ 195s |
| Memory / CPU | 512 MiB / 1 vCPU | plenty for the slim app |
| Startup CPU boost | on | faster cold start |
| Ingress / auth | all, `--allow-unauthenticated` | Telegram can't send Google auth; the secret headers are the auth |
| Image | python:3.12-slim, non-root, `uvicorn main:app --workers 1` | small image, fast cold start |

Nothing heavy happens at import: the Mongo client is created lazily and reused
across requests; indexes are created by `scripts/init_db.py`, not at startup.
Expected cold start ≈ 1–2s.

### Cloud Scheduler

- One job: `0 9 * * *` in your timezone, `POST /jobs/source`, header `X-Job-Secret`.
- Retries: 2, with backoff; attempt deadline 120s.
- Runs daily; the 44h check gives the every-other-day cadence and recovers from a
  missed day. A plain `*/2` cron fires on both the 31st and the 1st.

### Atlas M0

- Same cloud and region as Cloud Run.
- Network access `0.0.0.0/0` (Cloud Run has no fixed outbound IP without a paid
  Cloud NAT). Compensate with a long random password and a DB user with readWrite on
  this one database only.
- Client options: `serverSelectionTimeoutMS=5000`, `maxPoolSize=5`.

### Telegram webhook

Set once by `scripts/set_webhook.py`:
`url=<run-url>/telegram`, `secret_token=TELEGRAM_WEBHOOK_SECRET`,
`allowed_updates=["callback_query"]`, `drop_pending_updates=true`.

### Guardrails to keep it at zero

- A GCP budget of $1 with email alerts.
- An Artifact Registry cleanup policy: keep the last 3 images, to stay under the
  free 0.5 GB.
- A prepaid DeepSeek balance, so spend can never exceed the top-up.
- Logs go to stdout as one JSON line per request (Cloud Logging free tier).

### Environment variables (Cloud Run env)

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`, `JOB_SECRET`,
`MONGODB_URI`, `MONGODB_DB_NAME`, `LLM_API_BASE`, `LLM_MODEL`, `LLM_API_KEY`,
`LLM_MAX_TOKENS`.

## 7. Volume and cost (per month)

| Item | Volume | Cost |
|---|---|---|
| Scheduler invocations | ~30 (15 skipped in ms) | free (3 jobs free) |
| Webhook requests | ~60–100 (picks, rewrites, approve/reject) | Cloud Run free tier (2M requests) |
| Cloud Run compute | ~30 min of vCPU | free tier |
| DeepSeek calls | ~70 (≈ 30 picks × 2 + 10 rewrites) | cents |
| Atlas storage | a few hundred KB | free (512 MB) |
| **Total** | | **≈ $0 + a few cents of DeepSeek** |

## 8. Code layout

Rename `src/` → `groundtruth/` and remove the `sys.path` hacks (fixes `config`
being imported as two modules).

```
main.py                     FastAPI: the 3 endpoints, auth checks, JSON logging
groundtruth/
  config.py                 env only
  prompts.py                the 3 prompt templates
  llm.py                    complete(messages, temperature) -> str via the openai SDK (DeepSeek); logs cache hits
  pipeline.py               fetch → extract → write, fail-closed (replaces langgraph)
  fetcher.py                kept as is
  sources/hackernews.py     parallel fetch, 72h filter, ranking
  store.py                  lazy Mongo, UTC, atomic claims, meta
  telegram.py               thin Bot API client (send / edit / answer)
  handlers.py               source_job(), handle_callback()
scripts/init_db.py          indexes (TTL + unique hn_id)
scripts/set_webhook.py      register the webhook
scripts/dev_poll.py         local getUpdates loop → the same handlers
Dockerfile · deploy.sh · requirements.txt (pinned) · requirements-dev.txt · tests/
```

**Delete:** `app.py`, `scheduler/`, `integration/linkedin_client.py`,
`LINKEDIN_SETUP.md`, the LinkedIn/Ollama/`LLM_PROVIDER` config, LOCAL_LLM_GPU.md, langgraph,
langchain-*, python-telegram-bot, apscheduler, the reddit/rss/embeddings/reviewing/
publishing/topic placeholders, `models/`, `shared/`, `article_repository`,
`provider_registry`, `hn_ingestor`, `base.py`, `run_hackernews.py`.

**Unchanged:** the fetcher, the two-stage prompts, and the fail-closed floors
(400-char body, 120-char brief).

**Dependencies (pinned):** fastapi, uvicorn, pymongo, openai, requests,
python-dotenv; pytest goes in `requirements-dev.txt`.

## 9. Build order

1. **Restructure:** rename the package, delete dead code, `llm.py` → openai SDK,
   `pipeline.py` replaces langgraph.
   Check: one draft end to end locally against DeepSeek.
2. **Store:** UTC, atomic claims, hn_id dedup, stored brief, `announced`, meta,
   `init_db.py`.
   Check: unit tests for every transition, including a repeated tap and a stale
   `drafting` record.
3. **Web app:** `main.py`, `telegram.py`, `handlers.py`, auth, `dev_poll.py`.
   Check: local uvicorn with simulated Scheduler and Telegram requests, plus a real
   run through `dev_poll.py`.
4. **Deploy:** Dockerfile, `deploy.sh` (`gcloud run deploy --source .` →
   `set_webhook.py` → create the Scheduler job), budget alert, registry cleanup
   policy.
   Check: `curl -X POST …/jobs/source?force=1` → pick → rewrite → approve → the
   copy-ready message arrives and the record is `approved` in Atlas.
5. **Docs and hardening:** rewrite README, MONGODB_RUNBOOK (Atlas) and LOCAL_LLM_GPU
   (local dev only); tests for the fetcher and handlers.

## 10. Existing bugs this fixes

- Naive `datetime.now()` against Mongo's UTC TTL → wrong expiry.
- A 24h TTL is shorter than the 2-day cycle.
- Rewrite re-fetches and re-extracts (the brief was never stored).
- Only `ArticleUnavailable` is caught; an LLM error leaves "Writing a draft..." stuck.
- `config` is imported twice through the mixed `src.`/bare imports.
- The Mongo connection and index are created at import → slow cold start.
- No dedup across runs.

## 11. Why the build order is what it is

Three rules: change one axis at a time (code shape → state → trigger → hosting), so
a failure points at one change; cheap local feedback first and slow cloud feedback
last; every phase ends working and is its own revertable commit.

1. **Restructure first.** Every later phase imports these modules, so rename once
   before new code exists. Deleting dead code first means nothing gets adapted only
   to be deleted. DeepSeek is the plan's biggest unknown (prompts were tuned for
   gemma2:9b), so it gets tested before any infrastructure is built around it.
2. **Store before the web app.** Every handler starts with a claim, and all the
   duplicate/crash safety lives in the claims. They're tested directly against a
   real MongoDB (fakes don't reproduce unique indexes, TTL or atomic updates) with no
   HTTP or Telegram in the way.
3. **Web app third, still local.** It joins pipeline and store, so it needs both.
   Simulated requests test auth and dispatch in seconds; `dev_poll.py` gives real
   taps through the same `handle_callback()` the webhook uses. A bot can have a
   webhook *or* `getUpdates`, never both, so local dev uses a separate dev bot.
4. **Deploy fourth.** With the code proven, anything that fails here is
   configuration (Atlas network access, env vars, timeouts, webhook, Scheduler
   header). Guardrails are created with the resources so nothing billable runs
   unguarded.
5. **Docs and extra tests last.** Docs describe the final state; core correctness
   tests already exist from phase 2; nothing here blocks going live.
