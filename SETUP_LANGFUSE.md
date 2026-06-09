# Enable Langfuse tracing (LLM observability)

Langfuse records a trace of **every AI call** Agent-Aware makes — the prompt, the
response, latency, and **token usage** — in a dashboard. Since the app keeps
hitting Groq's daily token limit, this shows you exactly **which step burns the
most tokens** so you can optimize the right thing.

It's **read-only observability**: it watches your existing AI calls and adds **no
extra tokens**. The app works with or without it.

> **What you do yourself:** create a Langfuse account + project and copy two API
> keys. ~3 minutes. The code is already wired in.

---

## Step 1 — Create a project
1. Go to **https://cloud.langfuse.com** and sign in (Google/GitHub/email).
2. Create an **Organization** → create a **Project** (name it `Agent-Aware`).

## Step 2 — Get the API keys
1. In the project: **Settings → API Keys → Create new API key**.
2. Copy the **Public Key** (`pk-lf-…`) and **Secret Key** (`sk-lf-…`).

## Step 3 — Paste into `.env`
```
LANGFUSE_PUBLIC_KEY=pk-lf-your-public-key
LANGFUSE_SECRET_KEY=sk-lf-your-secret-key
LANGFUSE_HOST=https://cloud.langfuse.com
```

## Step 4 — Restart
```
python run.py
```
On startup you'll see `Langfuse tracing ENABLED for LLM calls.` in the logs.

## Step 5 — Run a search, then look
Do any search in the app, then open your Langfuse project → **Tracing → Traces**.
You'll see one trace per AI call (intent parsing, each platform's extraction,
insights, recommendation, chat) with token counts and timings.

---

### Notes
- Tracing is **automatic** — it works because all AI calls route through Groq's
  OpenAI-compatible endpoint via Langfuse's OpenAI wrapper (`backend/llm.py`).
- Leave the keys blank to turn it off; the app behaves exactly the same, untraced.
- For deployment, set `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`
  as environment variables in your hosting dashboard.
