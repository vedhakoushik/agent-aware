"""
Central LLM client + Langfuse observability.

Every node in the pipeline used to build its own `groq.Groq(...)` client. They now
all go through `get_chat_client()` here, which returns an OpenAI-compatible client
pointed at Groq's endpoint (`https://api.groq.com/openai/v1`). The `.chat.completions
.create(...)` interface is identical to the Groq SDK, so node code is unchanged
apart from how the client is created.

Why route through one place:
  • Observability — when Langfuse keys are present, we hand back Langfuse's OpenAI
    drop-in (`langfuse.openai.OpenAI`), which automatically traces EVERY call: the
    prompt, the response, latency, and token usage, viewable at cloud.langfuse.com.
    This is how you'll finally see which node burns the most of your daily Groq
    tokens. When no Langfuse keys are set, it falls back to the plain OpenAI client —
    same behavior, just untraced. Tracing adds no extra LLM tokens; it only observes.
  • One swap point — model routing / fallbacks can live here later.

Set these in `.env` to turn tracing on (see SETUP_LANGFUSE.md):
  LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Groq's OpenAI-compatible endpoint — lets us use the OpenAI SDK (and therefore
# Langfuse's OpenAI drop-in) while still hitting Groq's free Llama models.
GROQ_OPENAI_BASE = "https://api.groq.com/openai/v1"

_langfuse_logged = False


def langfuse_enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def get_chat_client():
    """Return an OpenAI-compatible chat client pointed at Groq.

    Traced via Langfuse when keys are configured, otherwise a plain OpenAI client.
    """
    global _langfuse_logged
    api_key = os.getenv("GROQ_API_KEY", "")

    if langfuse_enabled():
        try:
            from langfuse.openai import OpenAI as TracedOpenAI
            if not _langfuse_logged:
                logger.info("Langfuse tracing ENABLED for LLM calls.")
                _langfuse_logged = True
            return TracedOpenAI(api_key=api_key, base_url=GROQ_OPENAI_BASE)
        except Exception as e:
            logger.warning(f"Langfuse OpenAI wrapper unavailable ({e}); using plain client.")

    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=GROQ_OPENAI_BASE)


def flush_traces() -> None:
    """Flush any buffered Langfuse traces (call after a search completes)."""
    if not langfuse_enabled():
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception as e:
        logger.debug(f"Langfuse flush skipped: {e}")
