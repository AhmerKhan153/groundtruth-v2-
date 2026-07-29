from datetime import datetime
import os

from dotenv import load_dotenv

load_dotenv()

# --- LLM selection ---------------------------------------------------------
# LLM_PROVIDER: "claude" (Anthropic API, default) or "ollama" (local).
# Switching is a single env var; get_chat_model() in src/llm.py reads these.
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "claude").lower()
DEFAULT_LLM_MODEL = os.getenv("OLLAMA_MODEL") or "qwen3:4b"
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL") or "claude-opus-4-8"
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS") or "4096")
# ChatAnthropic reads ANTHROPIC_API_KEY from the environment; surfaced here so a
# missing key fails loudly at startup rather than on the first generation.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# --- Ollama GPU tuning (local inference only) ------------------------------
# Ollama fills the GPU with as many model layers as fit, then spills the rest to
# CPU/RAM. On a small-VRAM card the KV cache for a large context steals room that
# could hold layers, so a smaller context frees VRAM and pushes MORE layers onto
# the GPU. OLLAMA_NUM_CTX sets that context window (was effectively 4096).
# Do not drop this below ~3072: the writing prompt plus 4000 chars of article
# text is ~1600 tokens in, ~350 out, and Ollama silently truncates the prompt --
# which throws away the article body and leaves the model inventing from the
# headline. Trade VRAM elsewhere (num_gpu) before shrinking this.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX") or "4096")
# OLLAMA_NUM_GPU forces the exact number of layers to offload to the GPU. Leave
# blank to let Ollama auto-maximize (recommended); set a number to push harder,
# but too high will OOM the GPU. -1 also means "auto".
_num_gpu = os.getenv("OLLAMA_NUM_GPU")
OLLAMA_NUM_GPU = int(_num_gpu) if _num_gpu not in (None, "") else None

# --- Storage ---------------------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI") or "mongodb://localhost:27017/"
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME") or "KnowledgeExtractor"
ARTICLES_COLLECTION = "articles"

# Draft lifecycle statuses (Mongo `status` field is the source of truth).
STATUS_SOURCED = "sourced"    # story picked from a provider, no draft yet
STATUS_DRAFTED = "drafted"    # LLM draft generated, awaiting your approval
STATUS_APPROVED = "approved"  # you approved it; queued for LinkedIn
STATUS_POSTED = "posted"      # published to LinkedIn
STATUS_REJECTED = "rejected"  # you rejected it

# Only approved docs are meant to persist. Sourced/drafted/rejected records are
# in-flight state; a Mongo TTL index auto-deletes them this many hours after they
# were last touched unless they reach `approved` (which clears their expiry).
SOURCED_TTL_HOURS = int(os.getenv("SOURCED_TTL_HOURS") or "24")

# --- Telegram --------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
BOT_TOKEN = TELEGRAM_BOT_TOKEN

# --- LinkedIn --------------------------------------------------------------
LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_AUTHOR_URN = os.getenv("LINKEDIN_AUTHOR_URN")  # e.g. "urn:li:person:xxxx"

# --- Drafting prompts ------------------------------------------------------
# Drafting runs in two stages: extract a factual brief from the article, then
# write the post from that brief. One combined prompt does not work on a small
# local model -- a ~3400-char instruction block made gemma2:9b drop the article
# entirely and emit generic filler. Splitting it keeps each prompt short enough
# that the model still follows instructions AND reads its source.

# Stage 1. Bullets MUST be complete sentences: a brief of bare figures
# ("- 40x cheaper", "- 21%") makes stage 2 invent what they measure, which is
# worse than a generic post because the invention reads as credible.
FACT_EXTRACTION_PROMPT_TEMPLATE = """Summarise the key points of the text below as complete sentences.

TEXT — "{title}"
\"\"\"
{content}
\"\"\"

Rules:
- Write 8-12 bullets. EVERY bullet must be a full sentence saying WHAT the thing
  is, not just a figure. Write "The fine-tune cost $500 in compute", never "$500".
- A number without the thing it measures is useless. If the text does not make
  clear what a number refers to, leave that number out entirely.
- Keep numbers, units and names exactly as the text gives them.
- Write about the people in the text in the third person. If the text says "our
  model" or "we found", write "the model" or "the team found".
- Only what the text actually states. Invent nothing.
- No commentary, no opinions, no introduction, no closing line."""

# Stage 2. Deliberately short. The old version carried a banned-word list and a
# long voice section; that bulk is what broke instruction following, and the
# buzzword rules belong in a post-generation check instead (not built yet).
POST_WRITING_PROMPT_TEMPLATE = """Write a LinkedIn post in my voice: an experienced engineer sharing a genuine take with a smart colleague, not a thought-leader building a brand.

TOPIC: {title}

FACTS — the only source you may use:
\"\"\"
{content}
\"\"\"

Rules:
- Build the post on these specific facts and numbers. Invent nothing. Never use a
  number unless the facts above say what it measures.
- These events happened to OTHER people. Never write them as my own experience:
  no "I built", "I found", "my GPU". Say "there's a case where", "some teams".
- My first person is for opinion only: "I think", "my read is", "what bothers me".
- Never mention an article, post, study, source or author, and never hint that I
  read anything. No "according to", "a recent example", "studies show". No links.
- Plain, simple English. Short sentences. Use contractions. No corporate or AI
  buzzwords, no hype, no stacked adjectives.
- Open with a concrete observation or opinion, not a grand thesis.
- Length: write about 5 paragraphs of 4 to 6 sentences each, about 250 words in
  total. Short paragraphs are the usual failure here -- keep every one of the six
  at four sentences or more, and never stop before the sixth.
- No emojis at the start of lines.
- End with one genuine discussion question (not a rhetorical one)."""

TOPIC_ANALYSIS_PROMPT_TEMPLATE = """You are a principal architect.

These are current Hacker News discussions:

{text}

Identify:
1. Emerging themes
2. Under-discussed architecture topics
3. Contrarian viewpoints

Return 10 LinkedIn post ideas in structured JSON format.
"""

# Appended to the writing prompt when you tap "Rewrite" on a draft.
REWRITE_PROMPT_SUFFIX = (
    "\n\nThis is a rewrite request. Produce a distinctly different angle from the "
    "previous attempt: change the hook, restructure the argument, and vary the "
    "discussion question."
)

