# Agent-Aware

> An agentic AI that takes **one natural-language query** — *"cheapest BLR→DEL flight next Friday"*, *"iPhone 15 under ₹70k"* — searches **many platforms at once**, compares the results like-for-like, and recommends the best option, while showing you **how the agents reason and talk to each other** the whole way.

Built with **LangGraph** (multi-agent orchestration), **Streamlit** (live UI), a **multi-provider LLM router** (Groq · Gemini · Cerebras · Ollama with automatic failover), and **browser-use / Playwright** for live scraping when no structured API exists.

> **Status — learning / portfolio project.** This is the "student" build where I explored *how far LLM-driven, real-time browsing can go*. It works and is genuinely interesting to watch, but the real-time-scraping approach is fragile by nature (see **[Known Issues](#known-issues--limitations)**). The lessons learned here motivated an API-first professional rebuild → **[agent-aware-pro](https://github.com/vedhakoushik/agent-aware-pro)**.

---

## What it does

```
You: "cheapest flight from Bangalore to Delhi next Friday"
        │
        ▼
  Intent Agent ──► classifies category (flight) + extracts params, plans which sites to hit
        │
        ▼
  Search Coordinator ──► fans out to per-platform agents in parallel
        │                   ├─ Flights → SerpApi Google Flights (fast API, ~1s)
        │                   └─ Others  → Tavily → deep-link scrape → browser-use agent
        ▼
  Aggregate → Compare → Group → Insights → Recommend
        │
        ▼
  Validate (critic) ──► if results are thin/ungrounded → Remediate (actor) → re-search  ⟲
        │
        ▼
  Best pick + justification, with real proof (never fabricated)
```

Along the way, every message between agents is streamed to an **Agent Communication feed** so you can watch the system think.

## Features

- **Multi-platform search** — flights, hotels, products, trains, buses, events, restaurants, cars.
- **Multi-provider LLM router** (`backend/llm.py`) — each agent (intent, parser, insights, recommend…) is pinned to a provider:model and **auto-fails-over** down the chain when a free tier hits its rate limit. Spreads load so no single quota kills a run.
- **Fast flight tier** — flights come from the **SerpApi Google Flights API** (real fares in ~1s), bypassing slow/fragile browser automation entirely.
- **browser-use agent** — for platforms with no clean API, an LLM drives a **real Chrome** browser to fetch live results, with a local-Ollama fallback so it never dies on a rate limit.
- **Self-healing loop** — a `validate → remediate` cycle acts as critic + actor: it re-checks coverage/groundedness, recomputes the true cheapest option from real data, and re-searches empty platforms (bounded to 3 rounds).
- **Agent Communication bus** — a live, full-width feed of the actual inter-agent messages *with payloads*, so the reasoning is transparent, not a black box.
- **Monitor / supervisor agent** — when a browser tab gets blocked, it diagnoses the cause (CAPTCHA / bot-block / unreachable / timeout) and suggests a concrete fix.
- **Per-platform tabs UI** + a **computer-use stage** that streams the live browser screen into the app during automation.
- **Optional** Google sign-in gate, read-only Slack channel viewer, and Langfuse LLM tracing.

## Architecture

| Layer | Tech |
|---|---|
| Orchestration | **LangGraph** `StateGraph` — cyclic graph with a self-healing `validate → remediate` loop |
| UI | **Streamlit** — per-platform tabs, live agent feed, in-app browser view |
| LLM | **Router** over Groq · Gemini · Cerebras · local Ollama, with per-task pinning + failover |
| Data | SerpApi (flights), Tavily search, deterministic deep-link scrape, browser-use (live) |
| Price history | ChromaDB |
| Tracing | Langfuse (optional) |

## Quick start

> Requires **Python 3.11+**.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt
python -m playwright install chromium

# 3. Configure your keys
copy .env.example .env          # Windows  (cp on macOS/Linux)
#   then open .env and fill in your keys (see "Keys" below)

# 4. Run
python run.py
```

Open **http://localhost:8501**.

> **Note:** backend changes do **not** hot-reload — fully restart `python run.py` after editing `backend/`.

## Keys

Copy `.env.example` to `.env` and fill in:

| Key | Required? | What it's for | Get it at |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** | primary reasoning LLM (parsing, insights, recommend) | https://console.groq.com |
| `GEMINI_API_KEY` | Recommended | big-context failover so a single rate limit doesn't stall a run | https://aistudio.google.com/apikey |
| `CEREBRAS_API_KEY` | Recommended | high-throughput free tier (1M tokens/day) for the heavy per-platform parser | https://cloud.cerebras.ai |
| `SERPAPI_KEY` | Recommended | real flight fares via Google Flights (free tier: 250 searches/mo) | https://serpapi.com |
| `TAVILY_API_KEY` | Recommended | fast, reliable real-page search results | https://tavily.com |
| `SLACK_BOT_TOKEN` | Optional | in-app Slack channel viewer → see `SETUP_SLACK.md` | https://api.slack.com/apps |
| `LANGFUSE_*` | Optional | LLM tracing/observability → see `SETUP_LANGFUSE.md` | https://cloud.langfuse.com |

Everyone needs their **own** `.env` — it is **never** committed (it holds secrets) and is excluded by `.gitignore`.

## Known issues & limitations

I'd rather be upfront about where this build fights you — most of these are *why* the API-first rebuild exists:

- **Real-time browser scraping of travel/OTA sites is fragile.** Sites like MakeMyTrip / Skyscanner render fares async and bot-protect aggressively, so scrapes return junk, time out, or get a 403 / CAPTCHA. **Structured APIs (SerpApi) are the only fast + reliable path** — which is exactly the lesson that led to [agent-aware-pro](https://github.com/vedhakoushik/agent-aware-pro).
- **Free-tier LLM rate limits.** Groq's free daily token cap and Gemini's low free request cap both get exhausted during heavy sessions. The multi-provider router mitigates this, but a run can still slow down or stall if several providers are down at once.
- **Chrome automation constraints.** Modern Chrome blocks automating your *default* profile, so the app uses a separate dedicated profile (`launch_my_browser.bat` / `use_my_account.bat`). Parallel browser agents are serialized to avoid profile-lock collisions; occasional lock errors can still happen.
- **CAPTCHAs are not solved — by design.** When a site throws a bot-wall the agent honestly reports "not accessible" rather than trying to defeat it.
- **Not production-hardened.** Single Streamlit process (one heavy request blocks others), demo-grade error handling in places, no user accounts unless you enable the optional Google gate, and secrets live in a local `.env`. Treat it as a **demo / learning artifact**, not a service.

## The professional rebuild

Once it was clear that live LLM scraping is the fragile part, I rebuilt the same idea API-first: **[agent-aware-pro](https://github.com/vedhakoushik/agent-aware-pro)** — where the LLM is the *reasoning* layer and all data comes from *structured supplier APIs*, making it fast, reliable, and hallucination-proof.

## Security note

Never commit or share `.env` or `.streamlit/secrets.toml` — they hold real secrets and are excluded by `.gitignore`. Use `.env.example` as the template.

## License

[MIT](LICENSE) — built for learning. Use freely.
