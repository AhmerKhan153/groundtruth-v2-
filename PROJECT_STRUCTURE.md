# Groundtruth — project structure

A module-by-module tour of the tree. For *why* the design looks like this, see the
["Engineering decisions"](README.md#engineering-decisions) section of the README;
this document is the map, not the rationale.

## Runtime shape

One long-running asyncio process, [app.py](app.py), owns three things:

1. a Telegram long-polling bot (one `CallbackQueryHandler`),
2. `source_news_job` on a 180-minute APScheduler interval, and
3. `publish_approved_job` on a 10-minute interval.

Everything else in the tree is called from those three. There is no web server, no
queue broker, and no separate worker process.

**The live path** — the only code that runs in normal operation — is:

```
app.py
  └─ scheduler/jobs.py
       ├─ ingestion/hackernews/hn_provider.py      (source stories)
       ├─ repository/draft_repository.py           (persist + lifecycle)
       ├─ integration/telegram_bot.py              (send pick list / drafts)
       └─ integration/linkedin_client.py           (publish)

integration/telegram_handler.py                    (every button tap lands here)
  └─ workflow/content_graph.py
       ├─ processing/fetcher/fetcher.py
       ├─ processing/cleaner/cleaner.py
       ├─ workflow/extraction/extraction.py        (stage 1: facts)
       └─ workflow/writing/writing.py
            └─ workflow/post_writer.py             (stage 2: post)
                 └─ llm.py
```

Modules outside that list are either seams for future sources/stages or earlier
scaffolding kept for reference; each is marked below.

## Import convention

[app.py](app.py) inserts both the repo root and `./src` onto `sys.path` before
importing anything else. That is why most modules import as `from config import …`
and `from repository import draft_repository` rather than `from src.config import
…`. A few modules use the `src.`-prefixed form
([content_graph.py](src/workflow/content_graph.py),
[writing.py](src/workflow/writing/writing.py)); both resolve to the same module
objects at runtime because both directories are on the path. If you add a module,
follow the unprefixed form — it is the majority convention.

Running a module directly, outside `app.py`, needs the path set manually:

```bash
PYTHONPATH=src python -m src.ingestion.run_hackernews
```

## Top level

| Path | Role |
|---|---|
| [app.py](app.py) | Entrypoint. Builds the bot, registers both interval jobs, fires one sourcing cycle immediately. Holds `SOURCE_INTERVAL_MINUTES` and `PUBLISH_INTERVAL_MINUTES`. |
| [requirements.txt](requirements.txt) | Dependencies. |
| [.env.example](.env.example) | Every environment variable, documented. Copy to `.env`. |
| `models/` | Plain data shapes, no behaviour. |
| `src/` | The application package. |
| `docs/plans/` | Design plans written before the work, kept as a record. |

### `models/`

| File | Contents |
|---|---|
| [article_format.py](models/article_format.py) | `ArticleFormat` — the article shape. |
| [topics.py](models/topics.py) | `TopicIdea`, `TopicList` — pydantic schemas bound to the model for structured output in the topic-analysis path. |
| [workflow_state.py](models/workflow_state.py) | The earlier end-to-end workflow state. Superseded by `ContentState` in [content_graph.py](src/workflow/content_graph.py); the live graph does not use it. |

## `src/config.py` — configuration and prompts

Loads `.env` and exposes every setting as a module constant. Two things worth
knowing:

- **All prompt templates live here**, not in a template directory:
  `FACT_EXTRACTION_PROMPT_TEMPLATE` (stage 1),
  `POST_WRITING_PROMPT_TEMPLATE` (stage 2), `REWRITE_PROMPT_SUFFIX`,
  `TOPIC_ANALYSIS_PROMPT_TEMPLATE`.
- **The five lifecycle statuses are defined here** — `STATUS_SOURCED`,
  `STATUS_DRAFTED`, `STATUS_APPROVED`, `STATUS_POSTED`, `STATUS_REJECTED` — and the
  Mongo `status` field holding one of them is the single source of truth for where
  a draft sits.

The long comments on `OLLAMA_NUM_CTX` and the prompt templates record failures that
cost real debugging time. Read them before changing those values.

## `src/llm.py` — the model seam

The only module in the codebase that constructs a chat model. `LLM_PROVIDER` selects
a builder from the `_PROVIDERS` map — `ollama` (local) or `openai` (any endpoint
speaking the OpenAI chat protocol, with `LLM_API_BASE` for self-hosted servers and
gateways). Every caller goes through `get_chat_model()`; `get_structured_llm(schema)`
returns a model bound to a pydantic schema.

Adapters are imported **lazily**, inside each builder, so you only install the SDK
for the provider you use. Adding a backend is one builder plus a `_PROVIDERS` entry;
the contract is just "return something with `.invoke()` and
`.with_structured_output()`".

This is the reason no vendor name appears anywhere else in the tree. Swapping the
entire pipeline between a hosted and a local model is one env var and zero code
changes, and `LLM_MODEL` carries no default so a provider id is never baked in.

## `src/ingestion/` — where stories come from

| Path | Role |
|---|---|
| [article_provider.py](src/ingestion/article_provider.py) | `ArticleProvider` ABC — one method, `fetch_top_articles(limit)`, returning a list of dicts. |
| [base.py](src/ingestion/base.py) | `Ingestor` ABC — an identical earlier interface, kept so older references keep importing. Implement `ArticleProvider` for anything new. |
| [provider_registry.py](src/ingestion/provider_registry.py) | `ProviderRegistry` plus a module-level `registry` singleton: name → provider class. A seam for multi-source selection; **the live path does not read it** — `jobs.py` instantiates `HackerNewsProvider` directly. |
| `hackernews/` | **The active provider.** [hn_provider.py](src/ingestion/hackernews/hn_provider.py) does the work; [hn_ingestor.py](src/ingestion/hackernews/hn_ingestor.py) is a one-line subclass kept for compatibility. |
| `reddit/` | Stub. **Reddit returns 403 for all anonymous access** (JSON and RSS, any user agent), so this needs free credentials from reddit.com/prefs/apps before it can work. |
| `rss/` | Stub. |
| [run_hackernews.py](src/ingestion/run_hackernews.py) | Standalone CLI: previews the top 10 stories without touching the bot, then exports approved records to `./data/articles.json`. |

**How `hn_provider` ranks.** It reads the first 50 top-story ids from the HN
Firebase API and discards items with no external `url` (Ask HN and self posts —
nothing to fetch) and items scoring under 50. Survivors are sorted by
`score + 2 × descendants`, favouring *discussed* stories over merely upvoted ones.

## `src/processing/` — article text handling

| Path | Role |
|---|---|
| [fetcher/fetcher.py](src/processing/fetcher/fetcher.py) | **The heavy one.** Dependency-free HTML→text extraction on `html.parser.HTMLParser`: browser User-Agent, depth-based skip regions, whole-word noise matching on `class`/`id`, boilerplate stripping, and a 400-character body floor below which `fetch_article` returns `None`. |
| [cleaner/cleaner.py](src/processing/cleaner/cleaner.py) | `ArticleCleaner.clean()` normalizes the `{title, url, articlehtml}` dict into `{title, url, content}`. |
| [embeddings/embeddings.py](src/processing/embeddings/embeddings.py) | Placeholder. `EmbeddingsGenerator.generate()` returns a constant `[0.0]` vector — the shape exists, the implementation does not. Not on the live path. |

The 400-char floor is load-bearing: it is what converts "this page is a JS shell or
a consent wall" into a hard failure instead of a thin, plausible-looking draft.

## `src/workflow/` — the graph and its stages

| Path | Role |
|---|---|
| [content_graph.py](src/workflow/content_graph.py) | **The orchestrator.** Compiles a three-node LangGraph `StateGraph` over `ContentState`: `fetch → extract → write`. Conditional edges short-circuit to `END` the moment `error` is set. `generate_draft()` is the public entry; it raises `ArticleUnavailable` rather than returning an ungrounded draft. |
| [extraction/extraction.py](src/workflow/extraction/extraction.py) | Stage 1 — up to 6 000 chars of article into 8–12 complete-sentence factual bullets. |
| [writing/writing.py](src/workflow/writing/writing.py) | Thin wrapper delegating to `post_writer.create_post`. |
| [post_writer.py](src/workflow/post_writer.py) | Stage 2 — brief → LinkedIn post. Sees the brief only, never the raw article. |
| [topic_generation/](src/workflow/topic_generation/) | `TopicAnalyzer` runs `TOPIC_ANALYSIS_PROMPT_TEMPLATE` against HN discussions for a structured `TopicList`; `TopicGeneration` is a trivial title-passthrough. Exploratory, not on the live path. |
| [reviewing/reviewing.py](src/workflow/reviewing/reviewing.py) | Stub returning `{"approved": True}` unconditionally. **Superseded by the human gate** — real review is the Telegram approval step, so this must not be mistaken for it. |
| [publishing/publishing.py](src/workflow/publishing/publishing.py) | Dumps a JSON artifact via `shared.save`. Earlier scaffolding; real publishing is [linkedin_client.py](src/integration/linkedin_client.py). Not on the live path. |

**Why the graph has no human node.** Approval is an open-ended wait — minutes or
hours — and modelling it as a graph node would mean holding process state across
that whole window and losing it on any restart. The graph runs the automated
stretch and returns; the human decision happens in `integration/`.

**Why two stages.** One combined prompt (digest a long article *and* obey a long
list of voice rules) collapsed on a small local model — a ~3 400-char instruction
block made `gemma2:9b` drop the article entirely and emit generic filler. See
[docs/plans/two-stage-grounded-drafts.md](docs/plans/two-stage-grounded-drafts.md).

## `src/integration/` — the outside world

| Path | Role |
|---|---|
| [telegram_handler.py](src/integration/telegram_handler.py) | **The real orchestrator of the human half.** Dispatches `"<action>:<draft_id>"` callbacks: `pick` (run the graph, attach the draft, send for review), `approve`, `reject`, `rewrite`. Each action does one bounded chunk of work against the Mongo record and stops. |
| [telegram_bot.py](src/integration/telegram_bot.py) | Send helpers and inline keyboards: `send_story_selection`, `send_draft(draft_id, content, source_url)`, `send_text_message`. Every button carries the Mongo `_id`. |
| [linkedin_client.py](src/integration/linkedin_client.py) | `post_text()` against the LinkedIn `ugcPosts` API; raises `LinkedInError`. |

Two invariants live here:

- **Ids, not indexes, in `callback_data`.** This is what makes the bot restart-safe
  and lets many drafts be in flight at once.
- **Every draft ships with its source URL.** The post is first person with no
  citations, so a grounded claim and an invented one look identical in Telegram.
  The link is the only thing that makes the human gate meaningful.

## `src/repository/` — persistence

| Path | Role |
|---|---|
| [draft_repository.py](src/repository/draft_repository.py) | **The source of truth.** MongoDB lifecycle: `insert_sourced`, `attach_draft`, `set_status`, `find_approved`, `get`. Opens its client and creates the TTL index **at import time**, so a missing `mongod` fails at startup rather than at first use. |
| [article_repository.py](src/repository/article_repository.py) | File-based JSON export (`default=str` so `ObjectId`/`datetime` serialize). Used only by the standalone CLI. |

The TTL index sits on `expire_at` with `expireAfterSeconds=0`, making the field an
absolute delete time. `sourced`/`drafted`/`rejected` carry one; `approved`/`posted`
have it `null`, which the index ignores. `attach_draft()` refreshes the clock so
the review window starts when the draft appears, not when the story was sourced.

## `src/scheduler/jobs.py` — the two jobs

- `source_news_job` — fetch 6 ranked stories, `insert_sourced` each, send the pick
  list.
- `publish_approved_job` — for each approved record, `post_text` then mark `posted`
  with the returned URL. A `LinkedInError` is reported to Telegram and the record
  is **left `approved`**, so the next tick retries it.

Both wrap blocking calls (`requests`, `pymongo`) in `asyncio.to_thread` so they
never stall the Telegram event loop.

## `src/shared/save.py`

`save_json()` — writes to `src/output/`. Used by the publishing stub only. Draft
persistence is `draft_repository`, not this.

## Why the `__init__.py` files exist

A directory becomes an importable Python package when it contains `__init__.py`.
Most are empty; they exist purely so `processing.cleaner.cleaner` and
`ingestion.article_provider` resolve as module paths. Note that
[.vscode/settings.json](.vscode/settings.json) hides them from the file explorer —
they are present on disk even when the editor does not show them.

## Common tasks

**Add a story source.** Implement `ArticleProvider.fetch_top_articles(limit)` under
`src/ingestion/<source>/`, returning dicts with `title`, `url`, `score` and
optionally `comments`. Register it with `registry.register("<name>", Provider)`,
then swap it into `source_news_job` — the registry alone will not put it on the
live path.

**Add a graph stage.** Add a field to `ContentState`, write `_node(state) -> dict`,
register it in `_build_graph()`, wire the edge. If the stage can fail in a way that
would leave a post ungrounded, set `state["error"]` and add a conditional edge to
`END` — the pattern `fetch` and `extract` already use.

**Change the voice.** Edit `POST_WRITING_PROMPT_TEMPLATE` in
[src/config.py](src/config.py), and keep it short: prompt length is the failure mode
on small local models.

**Swap models.** Change `LLM_PROVIDER` in `.env`. Nothing else builds a chat model.
