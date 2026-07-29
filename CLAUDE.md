# CLAUDE.md

Project context and working agreements for AI assistants on this repo.
Loaded automatically each session.

## What this is

An AI writer that sources tech stories, drafts LinkedIn posts grounded in them,
and runs them past a human on Telegram before publishing to LinkedIn.

Pipeline: ingest (Hacker News) → fetch + clean article → LLM draft →
Telegram approve/reject/rewrite → LinkedIn. State lives in MongoDB; the `status`
field is the single source of truth for a draft's lifecycle.

Related docs: `PROJECT_STRUCTURE.md`, `LOCAL_LLM_GPU.md`, `MONGODB_RUNBOOK.md`,
`LINKEDIN_SETUP.md`. Plans live in `docs/plans/`.

## Working agreement

**Plan before coding.** Present a concrete written plan and get approval before
editing. Investigation, diagnosis and read-only verification are fine to do
immediately — edits are not. On a feature request: investigate, report findings,
then propose a plan naming the files to be touched and the approach. When asked
to "check" or diagnose something, do the full investigation and report; still no
edits.

*Why:* a Reddit-ingestion request was once implemented directly across ~12 files.
The work was sound but unwanted at that scope, and unwinding it was risky given
the uncommitted work in the tree. A plan first would have surfaced both the scope
and a blocking credentials issue before any code existed.

**Never blanket-revert.** The working tree carries large uncommitted changes and
`git stash list` is empty — there is no recovery path if they are discarded.
Never use `git checkout -- .` or `git reset --hard` to undo a session's work.
Instead: compare against the session-start `git status`, restore files that were
clean at HEAD with `git checkout HEAD -- <file>`, and reverse only the specific
hunks in files that already carried uncommitted work. Verify afterwards that
`git diff --stat HEAD` matches the session-start file list.

## Content rules for generated posts

**No first-person appropriation.** Posts must never restate the source author's
incidents as the user's own ("my GPU died", "I found the token"). Specific events
go in third person or impersonally; first person is reserved for opinion and
judgment.

*Why:* these publish under the user's own name. Grounding a post in an article
and writing it "as my own thinking" pull against each other, and the model
resolves that tension by appropriating the author's story unless told not to.

*Enforced by* the "Whose experience is whose" section of
`POST_WRITING_PROMPT_TEMPLATE` in `src/config.py`. If that section is edited,
re-test with a first-person source article and grep the output for
"my <device>", "I found", "saw this".

**Always show the source URL.** Whenever showing a generated post — in chat or in
a Telegram draft — include the actual source article URL alongside it.

*Why:* posts are first person with no citations, so a grounded claim and an
invented one read identically. The link is the only way to verify before
publishing. In the app, `send_draft(draft_id, draft, source_url=...)` appends it.

**Invent nothing.** If the source text cannot be read, fail loudly rather than
draft from a headline — a confident post built on an unread article is worse than
no post. `_fetch_node` in `src/workflow/content_graph.py` already does this;
preserve that behaviour.

## Local LLM setup

Runs via Ollama (`LLM_PROVIDER=ollama`, `OLLAMA_MODEL=gemma2:9b`) on a laptop:
Ryzen 7 6800HS (Radeon 680M iGPU) + RTX 3060 Laptop (6 GB VRAM). Established
2026-07-25. Full runbook: `LOCAL_LLM_GPU.md`.

Winning config for 100% GPU offload:

- **Display driven by the iGPU, not the 3060.** Set GPU mode to Standard/Hybrid
  (Optimus) in Armoury Crate. Frees the 3060's ~1 GB display VRAM. Verify with
  `nvidia-smi` — idle VRAM should be ~120 MiB, not ~1 GB.
- **`OLLAMA_NUM_GPU=99`** — forces all layers including the output layer onto the
  GPU. Auto mode under-offloads (~56%, leaving ~1.7 GB idle); the output layer
  was the stubborn last ~13%.
- **`OLLAMA_NUM_CTX=4096`** — full context still fits on GPU (~31 MiB free). Drop
  to 3072 if an Ollama update OOMs.

Wired through `src/config.py` → `src/llm.py` (`ChatOllama`). Plugging in an
external HDMI monitor routes through the 3060 again and breaks full offload.
`.wslconfig` sets `memory=8GB` + `[experimental] autoMemoryReclaim=gradual`.

**Known limitation:** gemma2:9b's instruction-following collapses under very long
prompts. A ~3400-char instruction block caused it to drop the source article
entirely and emit generic filler. Keep prompts to this model short and put the
source material before the rules. See `docs/plans/two-stage-grounded-drafts.md`.

## Operational notes

- MongoDB must be running before `app.py`: `mongod --dbpath /data/db`. A manually
  started `mongod` does not survive the terminal closing, and
  `src/repository/draft_repository.py` connects at import time, so the app fails
  at startup rather than at first use.
- Reddit's API returns 403 for all anonymous access (JSON and RSS, any user
  agent). Any Reddit ingestion needs free credentials from reddit.com/prefs/apps.
