"""
Central LLM router — multiple providers, task-based load splitting, auto-failover.

Why this exists
---------------
The whole pipeline used to hit ONE provider (Groq). Groq's free tier is generous on
total tokens but tight on tokens-per-minute (12k TPM), so when several nodes fire at
once — especially the per-platform result parser — we'd get 429s and lose platforms.

This module spreads the work across providers so no single key gets hammered:
  • each TASK is routed to a primary provider (e.g. the heavy result-parser → Gemini
    2.5 Flash, the reasoning steps → Groq Llama-3.3-70b),
  • if the primary is rate-limited or errors, the call automatically FAILS OVER to the
    other provider, so one provider running dry never breaks a search.

Both Groq and Gemini are reached through their OpenAI-COMPATIBLE endpoints, so the same
`client.chat.completions.create(...)` interface (and Langfuse's OpenAI tracing wrapper)
works for both — only the base_url, key, and model name differ.

Configure in `.env`:
  GROQ_API_KEY        (required)   — console.groq.com
  GEMINI_API_KEY      (optional)   — aistudio.google.com/apikey  (enables the split;
                                     without it, every task just uses Groq)
  LANGFUSE_*          (optional)   — tracing (see SETUP_LANGFUSE.md)

Override routing per task without code edits, e.g.:
  ROUTE_SEARCH_PARSE=groq:llama-3.3-70b-versatile
  ROUTE_INSIGHTS=gemini:gemini-2.5-flash
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ── Provider definitions ──────────────────────────────────────
# Each provider is an OpenAI-compatible endpoint: (base_url, env-var-for-key).
# Ollama runs LOCALLY (no key, no rate limits) and exposes the same OpenAI API at
# localhost:11434 — so it drops into the same router. Its key-env is "" (none).
PROVIDERS = {
    "ollama":   ("http://localhost:11434/v1", ""),
    "groq":     ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini":   ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY"),
    # Cerebras — free tier: 1M tokens/day, ~2600 tok/s, OpenAI-compatible. NOTE its
    # free tier caps context at ~8k tokens, so it suits short-input tasks; oversized
    # parses fail over to a big-context provider (see _is_retryable). Serves
    # gpt-oss-120b + zai-glm-4.7 (NOT Qwen).
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    # OpenRouter — optional 4th provider; one key unlocks many :free models
    # (deepseek-r1:free, etc.). Inert until OPENROUTER_API_KEY is set.
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}

# Default model per provider when a route doesn't name one explicitly.
DEFAULT_MODEL = {
    "ollama":     os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    "groq":       "llama-3.3-70b-versatile",
    "gemini":     "gemini-2.5-flash",
    "cerebras":   "gpt-oss-120b",
    "openrouter": "deepseek/deepseek-r1:free",
}

# Task → (primary provider, model-or-None). The OTHER provider is the automatic
# failover. Strategy: use GROQ first for every task (its Llama-3.3-70b is strong and
# fast), and only spill over to GEMINI when Groq is rate-limited / quota-exhausted.
# Override any task with ROUTE_<TASK> in .env (e.g. ROUTE_SEARCH_PARSE=gemini).
# Per-agent assignment — spreads load so Groq's daily cap is no longer the single
# bottleneck. Cerebras (1M tokens/day free) takes the high-frequency tasks; Groq
# keeps the two quality-reasoning tasks; Gemini stays in reserve as the big-context
# failover. Override any of these with ROUTE_<TASK>=provider:model in .env.
TASK_ROUTING = {
    "intent":       ("cerebras", "gpt-oss-120b"),
    "search_parse": ("cerebras", "gpt-oss-120b"),   # HEAVY — overflow fails over to Gemini/Groq (big ctx)
    "universal":    ("cerebras", "gpt-oss-120b"),
    "monitor":      ("cerebras", "gpt-oss-120b"),
    "insights":     ("groq", "llama-3.3-70b-versatile"),
    "recommend":    ("groq", "llama-3.3-70b-versatile"),
    "validate":     ("groq", "llama-3.3-70b-versatile"),   # the LLM-as-judge relevance/coherence check
    "segregate":    ("groq", None),
    "chat":         ("groq", None),
}

GROQ_OPENAI_BASE = PROVIDERS["groq"][0]  # kept for backward references

# Friendly agent names per task — so the Agent Communication view reads like a
# conversation ("Intent Agent → LLM", not "intent → groq").
TASK_AGENT = {
    "intent":       "Intent Agent",
    "search_parse": "Result Parser",
    "segregate":    "Grouping Agent",
    "insights":     "Insights Agent",
    "recommend":    "Recommendation Agent",
    "chat":         "Chat Agent",
    "universal":    "Form-Filler Agent",
    "monitor":      "Monitor Agent",
    "validate":     "Validation Agent",
}


def _agent_name(task: str) -> str:
    return TASK_AGENT.get(task, f"{task} agent")


def _last_user_msg(messages: list) -> str:
    """The actual ask in a messages list — the last user turn (the system prompt is
    the static instruction template; the user turn is what changes per call)."""
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return ""


_langfuse_logged = False
_client_cache: dict[str, object] = {}


def langfuse_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def provider_available(provider: str) -> bool:
    spec = PROVIDERS.get(provider)
    if not spec:
        return False
    # Ollama is local + keyless — it's "available" when explicitly enabled in .env.
    if provider == "ollama":
        return os.getenv("OLLAMA_ENABLED", "").lower() in ("1", "true", "yes")
    return bool(os.getenv(spec[1]))


# ── Client factory ────────────────────────────────────────────

def get_client(provider: str = "groq"):
    """Return a cached OpenAI-compatible client for a provider (Langfuse-traced when
    keys are set), or None if that provider has no API key configured."""
    global _langfuse_logged
    spec = PROVIDERS.get(provider)
    if not spec:
        return None
    base_url, key_env = spec
    # Ollama needs no real key, but the OpenAI client requires a non-empty string.
    api_key = "ollama" if provider == "ollama" else os.getenv(key_env, "")
    if not api_key:
        return None

    cache_key = f"{provider}:{'lf' if langfuse_enabled() else 'plain'}"
    if cache_key in _client_cache:
        return _client_cache[cache_key]

    client = None
    if langfuse_enabled():
        try:
            from langfuse.openai import OpenAI as TracedOpenAI
            if not _langfuse_logged:
                logger.info("Langfuse tracing ENABLED for LLM calls.")
                _langfuse_logged = True
            client = TracedOpenAI(api_key=api_key, base_url=base_url)
        except Exception as e:
            logger.warning(f"Langfuse wrapper unavailable ({e}); using plain client.")
    if client is None:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)

    _client_cache[cache_key] = client
    return client


def get_chat_client():
    """Backward-compatible single-client accessor (Groq, or whatever's available)."""
    return get_client("groq") or get_client("gemini")


# ── Routing + failover ────────────────────────────────────────

def _route(task: str) -> tuple[str, str | None]:
    """Resolve a task to (provider, model), honoring a ROUTE_<TASK> env override."""
    override = os.getenv(f"ROUTE_{task.upper()}", "").strip()
    if override:
        prov, _, model = override.partition(":")
        return prov.strip(), (model.strip() or None)
    return TASK_ROUTING.get(task, ("groq", None))


def _provider_chain(task: str) -> list[tuple[str, str]]:
    """Ordered [(provider, model)] to try: first choice first, the rest as failover,
    keeping only providers that are actually configured/available.

    When Ollama is enabled it goes FIRST for every task (local = unlimited, no quota),
    with the cloud providers as automatic failover — UNLESS the user pinned a specific
    provider for this task via ROUTE_<TASK>, which always wins."""
    primary, model = _route(task)

    # Cloud first (fast + accurate), local LAST as a free safety-net failover. The
    # local 7B is great for not hitting a hard quota wall, but it's slow and lossy on
    # the heavy extraction/reasoning — so it only runs when the cloud providers are
    # rate-limited, never as the default. Flip with OLLAMA_FIRST=true if you'd rather
    # save quota at the cost of speed/accuracy.
    order = [primary]
    ollama_first = os.getenv("OLLAMA_FIRST", "").lower() in ("1", "true", "yes")
    if ollama_first and provider_available("ollama") and primary != "ollama":
        order.insert(0, "ollama")
    order += [p for p in PROVIDERS if p not in order and p != "ollama"]
    if "ollama" not in order:
        order.append("ollama")     # always the final free fallback

    chain: list[tuple[str, str]] = []
    seen = set()
    for prov in order:
        if prov in seen or not provider_available(prov):
            continue
        seen.add(prov)
        m = model if (prov == primary and model) else DEFAULT_MODEL.get(prov, model or "")
        if m:
            chain.append((prov, m))
    return chain


def _is_retryable(err: Exception) -> bool:
    """Rate-limit / quota / transient server errors that warrant a failover — PLUS
    context-length overflows, so a payload too big for a small-context provider
    (e.g. Cerebras's 8k free-tier cap) spills to a big-context one (Gemini/Groq)
    instead of dropping the platform."""
    s = str(err).lower()
    return any(k in s for k in (
        "rate limit", "rate_limit", "429", "quota", "resource_exhausted",
        "overloaded", "503", "502", "500", "timeout", "temporarily",
        # context-length overflow → fail over to a larger-context provider
        "context length", "context_length", "maximum context", "too long",
        "reduce the length", "exceeds the maximum", "context window",
    ))


def chat(task: str, *, messages: list, **kwargs):
    """Run a chat completion for a named task, routed to the right provider with
    automatic failover. Drop-in for `client.chat.completions.create(...)` — returns
    the same response object, so callers use `resp.choices[0].message.content`.

    Example:
        resp = chat("search_parse", messages=[...],
                    response_format={"type": "json_object"},
                    temperature=0.0, max_tokens=2500)
    """
    chain = _provider_chain(task)
    if not chain:
        raise RuntimeError("No LLM provider configured — set GROQ_API_KEY (and optionally "
                           "GEMINI_API_KEY) in .env")

    # Publish this agent's request to the LLM on the Agent Communication bus so the
    # user can SEE the actual ask each agent sends (e.g. the Intent agent asking the
    # LLM which websites to search). Never let instrumentation break the real call.
    from backend.agent_bus import send as _bus_send
    agent = _agent_name(task)
    _bus_send(frm=agent, to="LLM", kind="request",
              title=f"{agent} asks the LLM",
              content=_last_user_msg(messages),
              meta={"task": task})

    last_err = None
    for i, (provider, model) in enumerate(chain):
        client = get_client(provider)
        if client is None:
            continue
        try:
            resp = client.chat.completions.create(model=model, messages=messages, **kwargs)
            try:
                _bus_send(frm=f"LLM · {provider}", to=agent, kind="response",
                          title=f"LLM ({model}) replies to {agent}",
                          content=resp.choices[0].message.content,
                          meta={"task": task, "provider": provider, "model": model})
            except Exception:
                pass
            return resp
        except Exception as e:
            last_err = e
            more = i < len(chain) - 1
            if more and _is_retryable(e):
                logger.warning(f"[{task}] {provider}:{model} failed ({str(e)[:80]}); "
                               f"failing over to {chain[i+1][0]}")
                _bus_send(frm=f"LLM · {provider}", to=agent, kind="error",
                          title=f"{provider} rate-limited — failing over to {chain[i+1][0]}",
                          content=str(e)[:300],
                          meta={"task": task, "provider": provider})
                continue
            raise
    raise last_err or RuntimeError(f"All providers failed for task '{task}'")


def flush_traces() -> None:
    """Flush any buffered Langfuse traces (call after a search completes)."""
    if not langfuse_enabled():
        return
    try:
        from langfuse import get_client as _lf_client
        _lf_client().flush()
    except Exception as e:
        logger.debug(f"Langfuse flush skipped: {e}")
