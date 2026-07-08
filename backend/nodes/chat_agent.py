"""
Persistent chat agent — maintains conversation context across refinements.
Understands current search results and can trigger new searches.
"""
import json
import logging
import os

from backend.llm import chat

logger = logging.getLogger(__name__)

CHAT_SYSTEM = """You are a helpful AI search assistant embedded in Agent-Aware, a multi-platform price comparison tool.

The user has searched for something and you have live results. Your job is to:
- Answer questions about the current results (prices, platforms, availability)
- Refine the search when the user wants to narrow, filter, or change criteria
- Remember the full conversation — don't repeat yourself
- Be concise and direct

=== CURRENT SEARCH CONTEXT ===
Original query: {original_query}
Intent type: {intent_type}
Parameters: {params}
Platforms searched: {platforms}
Results summary:
{results_summary}
Best recommendation: {recommendation_summary}

=== INSTRUCTIONS ===
Reply ONLY with valid JSON:
{{
  "message": "<your conversational reply — 1-3 sentences, be helpful and specific>",
  "should_search": <true if the user wants to change/refine/repeat the search, false if just answering>,
  "refined_query": "<full standalone search query if should_search=true, else null>"
}}

Rules for refined_query:
- Must be a COMPLETE query, not just the change (e.g. "non-stop flights Delhi to Manali Saturday under ₹4000")
- Include all original constraints PLUS the new ones
- If user says "only non-stop" → add "non-stop" to flight query
- If user says "next Saturday" → update the date
- If user asks to compare a different platform → add it explicitly
- Never return null for refined_query if should_search is true
"""


def _build_results_summary(platform_results: dict, comparison: dict) -> str:
    """Build a compact text summary of current results for the LLM context."""
    lines = []
    for pid, presult in platform_results.items():
        if not isinstance(presult, dict):
            continue
        name    = presult.get("platform_name", pid)
        results = presult.get("results", [])
        if not results:
            lines.append(f"- {name}: No results found")
            continue
        prices = []
        for r in results:
            for pf in ("price", "price_per_night", "price_per_day", "total_price", "fare"):
                if r.get(pf):
                    prices.append(r[pf])
                    break
        price_str = f"₹{min(prices)}–₹{max(prices)}" if prices else "prices unknown"
        lines.append(f"- {name}: {len(results)} results, {price_str}")
    return "\n".join(lines) if lines else "No results available."


def _build_recommendation_summary(recommendation: dict) -> str:
    if not recommendation:
        return "No recommendation generated."
    return (
        f"Winner: {recommendation.get('winner_platform', '?')} — "
        f"Reasoning: {recommendation.get('reasoning', '')[:200]}"
    )


def chat_response(
    user_message: str,
    chat_history: list,
    platform_results: dict,
    comparison: dict,
    recommendation: dict,
    intent: dict,
    original_query: str,
) -> dict:
    """
    Given a user follow-up message and full search context, return:
    {message, should_search, refined_query}
    """
    results_summary     = _build_results_summary(platform_results, comparison)
    recommendation_sum  = _build_recommendation_summary(recommendation)
    intent_type         = (intent or {}).get("type", "general")
    params              = json.dumps((intent or {}).get("params", {}))
    platforms           = list(platform_results.keys())

    # Build conversation history string
    history_lines = []
    for msg in (chat_history or [])[:-1]:  # exclude current message (last item)
        role = "User" if msg["role"] == "user" else "Assistant"
        history_lines.append(f"{role}: {msg['content']}")
    history_str = "\n".join(history_lines) if history_lines else "No prior messages."

    system = CHAT_SYSTEM.format(
        original_query=original_query,
        intent_type=intent_type,
        params=params,
        platforms=", ".join(platforms),
        results_summary=results_summary,
        recommendation_summary=recommendation_sum,
    )

    # Build messages including full history
    messages = [{"role": "system", "content": system}]
    for msg in (chat_history or []):
        messages.append({"role": msg["role"], "content": msg["content"]})

    try:
        resp = chat(
            "chat",
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=512,
        )
        result = json.loads(resp.choices[0].message.content)
        return {
            "message": result.get("message", "I couldn't generate a response."),
            "should_search": result.get("should_search", False),
            "refined_query": result.get("refined_query"),
        }
    except Exception as e:
        logger.error(f"Chat agent error: {e}")
        return {
            "message": "Sorry, I had trouble processing that. Please try rephrasing.",
            "should_search": False,
            "refined_query": None,
        }
