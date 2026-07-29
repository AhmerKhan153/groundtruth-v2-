# Local LLM & GPU runbook

How the app runs a local LLM, how we got it to use the GPU fully, the terminology
involved, and the quick commands to test and diagnose it.

Hardware this was tuned on: **laptop** — AMD Ryzen 7 6800HS (integrated Radeon 680M)
+ **NVIDIA RTX 3060 Laptop, 6 GB VRAM**, running under **WSL2**.

---

## 1. TL;DR — the winning setup

Three changes, together, made `gemma2:9b` run **100% on the GPU** with the RAM
spike gone:

1. **Display moved to the integrated GPU** (Radeon 680M) instead of the RTX 3060.
   Done in the laptop vendor app (e.g. Asus Armoury Crate) → GPU Mode →
   **Standard / Hybrid (Optimus)** → reboot. This frees the ~1 GB of VRAM the 3060
   was spending on the display and turns it into a dedicated compute card.
2. **`OLLAMA_NUM_GPU=99`** — force *all* model layers (including the output layer)
   onto the GPU. Ollama's automatic mode is over-cautious.
3. **`OLLAMA_NUM_CTX=4096`** — the full context still fits entirely in 6 GB.

Plus a WSL tweak so Windows doesn't feel starved (see §6).

Result (verified via the app's own code path):

```
gemma2:9b   6.3 GB   100% GPU   4096
```

---

## 2. The config knobs (what they are and where they live)

All local-LLM settings are env vars in **`.env`**, read by **`src/config.py`**, and
passed to the model in **`src/llm.py`**. Nothing else in the code makes a model
directly, so this is the single place to tune.

| Env var | Value | What it does |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | Picks the backend: `ollama` (local) or `claude` (Anthropic API, remote). |
| `OLLAMA_MODEL` | `gemma2:9b` | Which local model Ollama serves. |
| `OLLAMA_NUM_CTX` | `4096` | **Context window** (max tokens the model keeps in view). Bigger = bigger KV cache = more VRAM. |
| `OLLAMA_NUM_GPU` | `99` | **How many model layers to put on the GPU.** `99` = "all of them". Blank/`-1` = let Ollama decide (it under-fills). |

The plumbing:

- `src/config.py` — parses `OLLAMA_NUM_CTX` (int) and `OLLAMA_NUM_GPU` (int, or
  `None` when blank).
- `src/llm.py` — builds `ChatOllama(model=..., num_ctx=..., num_gpu=...)`; only
  passes `num_gpu` when it's set.

To switch back to the cloud model (Claude), set `LLM_PROVIDER=claude` and put a key
in `ANTHROPIC_API_KEY`. The Ollama knobs are then ignored.

---

## 3. Terminology (plain-English glossary)

- **iGPU (integrated GPU):** graphics built into the CPU (here, AMD Radeon 680M).
  Shares system RAM as its video memory. Low power, fine for driving a display.
- **dGPU (dedicated GPU):** the separate graphics card (RTX 3060). Has its own fast
  memory (VRAM) and is what we want doing the LLM math.
- **VRAM:** the dedicated GPU's own memory. The RTX 3060 Laptop has **6 GB**. A model
  only runs fast if it fits in VRAM.
- **OOM (Out Of Memory):** the error you get when something doesn't fit in memory. A
  **GPU OOM** = the model + its buffers exceeded VRAM and the load/generation crashes.
  This is the failure we tune *around* — push layers onto the GPU, but not so many
  that it OOMs.
- **Layer / offload:** a model is a stack of "layers" (`gemma2:9b` has 42 transformer
  blocks + 1 output layer = 43). "Offloading a layer to the GPU" means that layer's
  math runs on the GPU. Layers that don't fit run on the **CPU** instead — slower, and
  they live in system **RAM**.
- **CPU/GPU split:** when a model is too big for VRAM, Ollama runs part on the GPU and
  the rest on the CPU. `ollama ps` shows it like `47%/53% CPU/GPU`. Anything less than
  `100% GPU` means some layers are on the slow CPU path.
- **KV cache:** memory the model uses to "remember" the tokens in the current context.
  It grows with `num_ctx`. Bigger context → bigger KV cache → less room for layers.
- **Quantization (e.g. Q4, Q3):** compressing model weights to use less memory. `Q4`
  ≈ 4 bits per weight (`gemma2:9b` Q4 is ~5.4 GB). A smaller quant (Q3) is smaller and
  fits easier, at a small quality cost.
- **MUX switch / Hybrid (Optimus) / Ultimate mode:** laptop graphics modes.
  - *Ultimate/dGPU:* the dedicated GPU drives the display (best for gaming, but it
    spends VRAM on the display).
  - *Hybrid/Standard (Optimus):* the **iGPU** drives the display; the dGPU is used for
    compute on demand. **This is what we want** — dGPU stays free for the LLM but is
    still available to CUDA.
  - *Eco/iGPU-only:* dGPU powered off entirely (don't use — CUDA can't reach it).
- **CUDA:** NVIDIA's compute layer that lets programs (Ollama) run math on the GPU.
- **WSL2 / vmmem:** WSL2 runs Linux in a lightweight VM on Windows. Windows shows its
  memory as the `vmmem`/`Vmmem` process. By default WSL2 grabs RAM and is slow to give
  it back, which is why Windows can look "maxed" (see §6).
- **buff/cache vs used (in `free -h`):** Linux uses spare RAM as disk cache
  (`buff/cache`). It looks "used" but is reclaimable — the **`available`** column is the
  number that actually matters.

---

## 4. Quick commands — test the LLM and check the GPU

### Test the app's actual model path (the shortcut used while tuning)

This imports the project's own factory (`src/llm.py`), so it exercises the exact
`num_ctx`/`num_gpu` the app will use — the fastest way to confirm a config change:

```bash
cd /mnt/c/Users/ahmer/ai-writer
PYTHONPATH=src python -c "
from src.llm import get_chat_model
m = get_chat_model()
print('model:', getattr(m,'model',None), '| num_ctx:', getattr(m,'num_ctx',None), '| num_gpu:', getattr(m,'num_gpu',None))
print(m.invoke('Write one sentence about databases.').content)
"
```

### Test Ollama directly (bypass the app, try options ad-hoc)

Handy for probing a setting without editing `.env`:

```bash
curl -s http://localhost:11434/api/generate \
  -d '{"model":"gemma2:9b","prompt":"hi","stream":false,"options":{"num_ctx":4096,"num_gpu":99}}'
```

Or interactively:

```bash
ollama run gemma2:9b "Write two sentences about databases."
```

### See where the model actually loaded (the key diagnostic)

```bash
ollama ps          # shows SIZE, PROCESSOR (e.g. "100% GPU" or "47%/53% CPU/GPU"), CONTEXT
```

### Check the GPU itself

```bash
nvidia-smi                                              # full view
nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv   # quick
```

- After the display was moved to the iGPU, idle VRAM used dropped from ~1014 MiB to
  ~121 MiB — that's how you confirm the dGPU is display-free.

### Other useful Ollama commands

```bash
ollama list                 # installed models + sizes
ollama stop gemma2:9b       # unload from VRAM (frees the card to re-test a config)
ollama show gemma2:9b       # model details (params, context length, block_count)
```

---

## 5. How we got to 100% GPU (the measured story)

Symptom: high RAM, low GPU utilization during writing.

1. **Which LLM?** `LLM_PROVIDER=ollama`, `OLLAMA_MODEL=gemma2:9b` — local, not Claude.
2. **`ollama ps` showed `47%/53% CPU/GPU`** — half the model was on the CPU (→ high
   RAM), so the GPU sat idle waiting on it (→ low GPU util).
3. **Root causes:** (a) the RTX 3060 was driving the display (~1 GB VRAM gone), and
   (b) Ollama's auto-offload is conservative — with 5 GB free it still only used 3.8 GB,
   leaving ~1.2 GB idle.
4. **Fixes:** display → iGPU (frees the card), then force layers with `num_gpu`.

Measured `num_gpu` curve **after** the display moved off the 3060 (`num_ctx` shown):

| Setting | Result | Note |
|---|---|---|
| auto (blank) | 56% GPU, ~1.7 GB idle | Ollama under-offloads |
| `num_gpu=42` (blocks only) | 87% GPU | output layer still on CPU |
| **`num_gpu=99` @ ctx 4096** | **100% GPU, ~31 MiB free** | **chosen** |
| `num_gpu=99` @ ctx 3072 | 100% GPU, ~53 MiB free | safer margin if needed |

**Key insight:** the last ~13% was the **output layer**. `num_gpu=42` only offloads the
42 transformer blocks; `num_gpu=99` (or `43`) includes the output layer → 100%.

---

## 6. RAM: why Windows looked "maxed", and the fix

Local inference on the CPU path loads gigabytes of model layers into system RAM. On
top of that, WSL2 holds RAM and is slow to return it, and MongoDB reserves cache. The
result looks like a permanently full RAM bar in Windows Task Manager.

Fixes applied in **`C:\Users\ahmer\.wslconfig`** (edit on Windows, then run
`wsl --shutdown` and reopen):

```ini
[wsl2]
memory=8GB                  # cap WSL so Windows keeps headroom

[experimental]
autoMemoryReclaim=gradual   # WSL returns unused cached RAM to Windows
```

The biggest RAM win, though, is **running the model 100% on the GPU** — with no CPU
offload there are no model layers sitting in system RAM. After the fix, WSL `used` RAM
dropped to ~2 GB.

---

## 7. Troubleshooting

- **`ollama ps` shows a CPU/GPU split, not `100% GPU`.** Raise `OLLAMA_NUM_GPU` (try
  `99`). If it still splits, the model is too big for your free VRAM — lower
  `OLLAMA_NUM_CTX`, use a smaller/quantized model, or free VRAM (see next point).
- **Idle VRAM is ~1 GB, not ~120 MiB.** The dGPU is driving a display. Switch the
  laptop to Hybrid/Optimus mode, or unplug an external monitor that's wired to the dGPU.
- **GPU OOM / model fails to load.** You forced too many layers or too big a context.
  Lower `OLLAMA_NUM_CTX` (e.g. 4096 → 3072) or `OLLAMA_NUM_GPU`.
- **External HDMI monitor plugged in.** On laptops the HDMI port is usually wired to
  the dGPU, so it re-consumes ~1 GB of VRAM and breaks full offload while connected.
- **Want max quality vs max speed.** `gemma2:9b` (9B) is higher quality but sits right
  at the 6 GB limit. `qwen3:4b` (2.5 GB) fits with lots of room, runs faster, lower
  quality — switch via `OLLAMA_MODEL`.
