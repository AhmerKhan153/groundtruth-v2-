# Plan: two-stage local drafting (extract → write)

## Context

Drafts from gemma2:9b come back generic — no facts from the source article.
Measured on `fermisense.com/when-machines-take-the-wheel/` (HN "$500 RL
fine-tune of a 9B model"):

| Prompt to gemma2:9b | Size | Result |
|---|---|---|
| Current `POST_WRITING_PROMPT_TEMPLATE` + 4000 chars article | 7427 chars | generic filler, 0 article facts, 195 words |
| Bare probe + same 4000 chars | 4116 chars | correctly read "9B", "catalog review", "$0.50/1000" |
| Compact rules + same 4000 chars | 4533 chars | grounded: $500, 9B, 40×, 340× |

The fetch → clean → write plumbing is correct and the article text *does* reach
the model. The cause is the 3366-char instruction block: gemma2:9b's
instruction-following collapses under it and it falls back on generic training
data, dropping the source.

Contributing: `_MAX_CONTENT_CHARS = 4000` against a 27,388-char article means the
model sees the first ~15%, and leading nav boilerplate ("Skip to content",
bylines) burns part of that budget.

Prototyped and verified: sentence-level extraction then writing produces 275
words with every figure correctly attributed and no source leaks.

**Guard rail found in prototyping:** a brief of bare numbers (`- 40× cheaper`,
`- 21%`) makes stage 2 *invent* what they measure — it produced a fabricated
"21% of all LLM use cases". Stage 1 must emit complete sentences and drop any
number whose subject is unclear. This is the difference between the approach
working and it being worse than today.

## Changes

### 1. `src/processing/fetcher/fetcher.py` — strip boilerplate
Add a `_strip_boilerplate(text)` helper applied in `fetch_article` before the
`_MIN_BODY_CHARS` check: drop standalone nav lines (skip to content, menu,
search, subscribe, sign up, log in, newsletter, advertisement, cookie policy)
and collapse inline repeated "Skip to content" runs. Reuses the existing
`_normalize_paragraphs` conventions; keep `_MIN_BODY_CHARS` measured on the
stripped text so gated pages still fail.

### 2. `src/config.py` — two prompts
- Add `FACT_EXTRACTION_PROMPT_TEMPLATE`: 8–12 bullets, **each a complete
  sentence stating what the number measures**; omit any figure whose subject is
  not stated; strip the source author's first person ("our model" → "the model");
  invent nothing.
- Replace `POST_WRITING_PROMPT_TEMPLATE` with a compact (~900 char) version that
  takes the brief instead of raw article text. Keeps the rules that survived
  testing: events belong to other people, first person for opinion only, never
  mention a source, simple English, 5–7 paragraphs to hit 250+ words, one real
  question. Add: never use a number whose meaning the brief does not state.
- Keep the banned-word list out of the prompt (it was diluting instruction
  following); see "Deferred" below.

### 3. New `src/workflow/extraction/` (`__init__.py`, `extraction.py`)
`ExtractionWorkflow.extract(title, content) -> str`, mirroring the existing
`WritingWorkflow` shape in `src/workflow/writing/writing.py`. Uses
`get_chat_model()` from `src/llm.py` — no new model wiring.

Deliberately **not** `src/processing/extractor/`: that package is deleted in the
current uncommitted work, so this must not resurrect it.

### 4. `src/workflow/content_graph.py` — add the node
- `_MAX_CONTENT_CHARS` 4000 → 6000 (the write prompt is far smaller now, so this
  still fits `OLLAMA_NUM_CTX=4096`; verify measured tokens, see below).
- `ContentState` gains `brief: str`.
- Graph becomes `fetch → extract → write`, with a conditional edge after
  `extract`: an empty or very short brief sets `error` and ends, exactly as
  `_fetch_node` already does for unreadable articles — no writing from nothing.

### 5. `src/workflow/writing/writing.py`, `src/workflow/post_writer.py`
`create_post(title, brief, is_rewrite)` formats the new template from the brief.
Signature shape is unchanged; only the meaning of the second argument changes.
`REWRITE_PROMPT_SUFFIX` still appends for rewrites — the brief is reused, so a
rewrite costs one LLM call, not two.

## Verification

1. `python -m py_compile` across touched files.
2. Prototype harness (already written) re-run against 5 live HN stories, asserting
   per draft: 250–350 words, no phrase from the source-leak list
   ("according to", "a recent example", "the article", …), and every number in
   the draft traceable to the brief.
3. Confirm the full write prompt stays under `OLLAMA_NUM_CTX` (4096) — measure
   chars/4 for the largest of the 5.
4. End-to-end through Telegram: run `app.py`, pick a story, confirm the draft
   arrives with its source URL attached.

Expect ~51s per draft (21s extract + 30s write), up from ~30s.

## Deferred (not in this change)

- Banned-word/buzzword enforcement as a post-generation check with one targeted
  retry. The prototype still produced "blows them out of the water" and "a
  whopping 36%". Worth doing, but it is a separate mechanism from the grounding
  fix and should be judged on its own.
- Nothing here touches Reddit ingestion.
