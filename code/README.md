# Message Notification Router

An agentic router for WhatsApp messages. For every row in `dataset/messages.csv` it decides whether to **notify** the user now, hold the message for a **digest**, or **mute** it, and writes `dataset/output.csv`:

```text
message_id,action,message_type,reason,confidence,evidence_message_ids
```

Every routing decision is made by language models using tools; plain code only loads data, caches results and checks the output format. The full design, with the reasoning behind each choice and the evaluation runs, is in `AGENTIC_WORKFLOW.md`.

## Quick start

Requirements: Python 3.9 or newer. There is nothing to install; the code uses only the standard library.

1. Get a free API key from <https://console.groq.com/keys>.
2. From the repository root, copy `.env.example` to `.env` and set your key:

   ```text
   GROQ_API_KEY=your-key-here
   ```

   The key can also come from the environment. Never commit `.env`; it is git-ignored.
3. Route every message and write the output (run from the repository root, not from `code/`):

   ```bash
   python3 code/main.py run
   ```

4. Check the file against the output contract:

   ```bash
   python3 code/main.py check-output
   ```

The package ships the model-response cache (`cache/llm/`) from the run that produced the submitted `output.csv`, so step 3 replays those decisions exactly, without an API key and without spending any quota. Delete `cache/llm/` to route everything live again.

## How it works

```text
dataset/*.csv ─► context store ─► router agent ─► critic agent ─► output guard ─► dataset/output.csv
                  (code)           gpt-oss-120b     qwen3.8-27b     (code)          + traces/<id>.json
                  + media cache    gathers facts    reviews the
                  (images, voice)  with 7 tools     decision
```

1. **Context store** (`code/context/`): loads the CSVs, joins each past message with how the user reacted to it, and answers lookups. Sample labels are loaded only by the evaluation script, never by the agents.
2. **Media cache** (`code/perception/`, `cache/media/`): every image is described once by `qwen/qwen3.8-27b` (text in the image, brands, links, QR codes, what it asks for) and every voice note is transcribed once by `whisper-large-v3`. The cache is shipped, so routing never re-analyses media.
3. **Router agent** (`code/agent/router.py`): `openai/gpt-oss-120b` reads the message and calls a `gather` tool to fetch the facts it chooses from seven tools (`code/tools/`): the recipient's profile and quiet hours, the sender (person or business: verification, domains, reports), the group, the business relationship, a search of the user's own history with their reactions, the media analysis, and a safety scan of links and requests. The tools return facts, never verdicts. The agent answers with one JSON decision and cites past messages as evidence.
4. **Critic agent** (`code/agent/critic.py`): `qwen/qwen3.8-27b`, a different model family, checks the decision against the facts (six checks: action fits type and policy, nothing obvious skipped, evidence supports it, no prompt injection steered it, reason matches, confidence plausible). On an objection the router revises once; a lasting disagreement goes to a tie-break that mutes anything risky and sends the rest to digest.
5. **Output guard** (`code/agent/guard.py`, `code/agent/output.py`): checks allowed values, keeps only evidence IDs from the user's earlier history, caps confidence at 0.92, substitutes a safe fallback row if anything fails, and writes the CSV with proper quoting in the template's row order.

**Untrusted content.** Message text, text read from images and transcripts reach the models inside `<untrusted_content>` tags. They are treated as data, and an attempt to instruct the router is itself a scam signal.

## Free-tier limits

The default models run on Groq's free tier, where each model has its own daily token limit (200K tokens a day for each gpt-oss model, counted over a rolling 24 hours). Routing all 110 messages takes roughly 800K router tokens and 300K critic tokens, so a full live run spans several days of free quota:

- When `gpt-oss-120b` reaches its daily limit, the router switches to `gpt-oss-20b` and reuses any answers 120b already cached.
- When every model for a role is out of quota, the run stops cleanly. Run the same command again later: finished messages come from the cache, so it continues where it stopped. `output.csv` is written once every message has a decision.
- Every trace in `traces/` records which model made each decision and how many tokens it used.

## Configuration

All settings are environment variables (or lines in `.env`); the defaults are shown.

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | (required for live calls) | Groq API key; never logged or cached |
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | Any OpenAI-compatible endpoint |
| `ROUTER_MODEL` | `openai/gpt-oss-120b` | Router agent |
| `ROUTER_FALLBACK_MODEL` | `openai/gpt-oss-20b` | Used when the router model's quota is spent; set it empty to disable |
| `ROUTER_REASONING_EFFORT` | `low` | gpt-oss reasoning effort |
| `CRITIC_MODEL` | `qwen/qwen3.8-27b` | Critic agent |
| `CRITIC_FALLBACK_MODEL` | (none) | Optional critic fallback |
| `VISION_MODEL` | `qwen/qwen3.8-27b` | Image analysis (media cache) |
| `TRANSCRIBE_MODEL` | `whisper-large-v3` | Voice-note transcripts (media cache) |
| `PROMPT_GUARD_MODEL` | (none) | Optional prompt-injection score in the safety scan |
| `DATASET_DIR` | `dataset/` | Where the CSVs live |

## Other commands

Run these from the repository root.

| Command | What it does |
|---|---|
| `python3 code/main.py check` | Validate the dataset and print counts |
| `python3 code/main.py inspect MESSAGE_ID` | Print everything the dataset says about one message |
| `python3 code/main.py tool MESSAGE_ID all` | Run the agent's tools for a message, as the agent sees them |
| `python3 code/main.py route MESSAGE_ID ...` | Route chosen messages and print each decision (`--blind` for the blind samples, `--no-critic` for the router alone) |
| `python3 code/main.py perceive` | Rebuild missing media analyses (needs an API key) |
| `python3 code/evaluation/main.py` | Score routed sample messages against their labels (evaluation only) |
| `python3 code/main.py package` | Build `code.zip` for submission |
| `python3 -m unittest discover -s code/tests -t code` | Run the tests (no network needed) |

## Reproducibility and data

- Every model call uses temperature 0 and a fixed seed, and is cached on disk under a hash of the full request, so reruns give identical results.
- Message text and media are sent to Groq for analysis. The dataset is synthetic, and no secrets are put in prompts.
- Three images could not be analysed by the vision model (one dense notice, two files whose real formats are WebP and AVIF); for those messages the agent works from the message text.

## Layout

```text
code/
├── main.py            command-line entry point
├── llm.py             Groq client: caching, retries, rate limits
├── context/           CSV loaders and lookups
├── perception/        builds the image and voice cache
├── tools/             the seven fact tools and the gather tool
├── agent/             router, critic, output guard, output file
├── prompts/           policy, message types, style examples, router and critic prompts
├── evaluation/        blind-set split, scorer and run log
└── tests/             unit tests
cache/media/           image and voice analyses (shipped)
cache/llm/             model-response cache (shipped in code.zip)
```
