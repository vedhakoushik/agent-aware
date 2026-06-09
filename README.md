# Agent-Aware

AI-powered multi-platform search & comparison. Enter one query (flights, hotels,
trains, products…) and it searches several platforms at once, compares the
results like-for-like, and recommends the best option — with a live Google
sign-in gate, a read-only Slack channel viewer, and LLM tracing.

Built with **LangGraph** (orchestration), **Streamlit** (UI), **Groq** (LLM),
**browser-use / Playwright** (live scraping), and **ChromaDB** (price history).

---

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

---

## Keys

Copy `.env.example` to `.env` and fill in:

| Key | Required? | What it's for | Get it at |
|---|---|---|---|
| `GROQ_API_KEY` | **Yes** | the AI that parses queries & extracts results | https://console.groq.com |
| `TAVILY_API_KEY` | Recommended | fast, reliable search results | https://tavily.com |
| `RAPIDAPI_KEY` | Optional | official flight/hotel data | https://rapidapi.com |
| `SLACK_BOT_TOKEN` | Optional | in-app Slack channel viewer → see `SETUP_SLACK.md` | https://api.slack.com/apps |
| `LANGFUSE_*` | Optional | LLM tracing/observability → see `SETUP_LANGFUSE.md` | https://cloud.langfuse.com |

> **Groq is free-tier (≈100k tokens/day).** Heavy use — especially the browser-use
> agent — will exhaust it. Each person should use their **own** Groq key, or
> upgrade to a paid tier.

Everyone needs their **own** `.env` — it is **not** shared in this bundle (it holds secrets).

---

## Optional features

- **Google sign-in gate** — restrict access to specific Gmail accounts. Setup: `SETUP_GOOGLE_AUTH.md`. Without it, the app runs in open mode.
- **Slack channel viewer** (read-only) — browse your workspace channels in-app. Setup: `SETUP_SLACK.md`.
- **Langfuse tracing** — see token usage per AI call. Setup: `SETUP_LANGFUSE.md`.
- **browser-use agent** — LLM-driven live browser scraping. Tunable in `.env` (`BROWSER_USE_*`); set `BROWSER_USE_ENABLED=false` to save tokens.

## Deploying

To host it for the whole team (instead of each person running locally), see
`DEPLOY.md` (Docker + Render blueprint included).

---

## Security note

Never commit or share `.env` or `.streamlit/secrets.toml` — they hold real
secrets and are excluded by `.gitignore`. Use `.env.example` as the template.
