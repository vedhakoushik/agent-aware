import json
import logging
import os

from backend.llm import chat
from backend.state import AgentState, Recommendation
from backend.memory.store import get_price_context
from backend.progress import emit

logger = logging.getLogger(__name__)

RECOMMEND_SYSTEM = """You are an expert travel and shopping advisor. Analyze the search results and give a confident recommendation.

Given:
- User query: {query}
- Intent: {intent_type}
- Search parameters: {params}
- Platform comparison data: {comparison}
- Historical price context: {price_context}

Return a JSON object with:
{{
  "winner_platform": "<platform_id of the best option>",
  "winner_result": {{<the specific best result dict to book/use>}},
  "reasoning": "<2-3 sentence explanation of why this is the best choice>",
  "price_analysis": "<one sentence on how the price compares to alternatives and historical data>",
  "alternatives": [
    {{"platform": "<id>", "why": "<brief reason to consider this instead>"}}
  ],
  "confidence": "<high|medium|low>",
  "tips": "<any practical tips: best time to book, watch out for X, etc.>"
}}

Be specific — cite actual prices and platform names. If results are sparse, be honest about it.
"""


def recommend_node(state: AgentState) -> dict:
    """LangGraph node: generate a recommendation using Groq."""
    comparison = state.get("comparison", {})
    intent = state.get("intent", {})
    query = state.get("query", "")

    # Get price history context from ChromaDB
    price_context = get_price_context(
        intent.get("type", "general"),
        intent.get("params", {}),
    )

    ranked = comparison.get("ranked_platforms", [])
    # Only pass top 3 platforms to LLM to keep prompt tight
    top3 = [
        {k: v for k, v in p.items() if k != "best_result"}
        | {"best_result_summary": str(p.get("best_result", {}))[:300]}
        for p in ranked[:3]
    ]

    emit("Picking the best option…", stage="recommend", kind="start")

    try:
        response = chat(
            "recommend",
            messages=[
                {
                    "role": "system",
                    "content": RECOMMEND_SYSTEM.format(
                        query=query,
                        intent_type=intent.get("type", ""),
                        params=json.dumps(intent.get("params", {})),
                        comparison=json.dumps(top3, indent=2),
                        price_context=price_context or "No historical data yet.",
                    ),
                },
                {"role": "user", "content": "Give me the best recommendation."},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        rec = json.loads(response.choices[0].message.content)

        # Fill in the full winner result from comparison data
        winner_pid = rec.get("winner_platform")
        if winner_pid:
            for p in ranked:
                if p["platform_id"] == winner_pid and p.get("best_result"):
                    rec["winner_result"] = p["best_result"]
                    break

        recommendation: Recommendation = {
            "winner_platform": rec.get("winner_platform", ""),
            "winner_result": rec.get("winner_result", {}),
            "reasoning": rec.get("reasoning", ""),
            "price_analysis": rec.get("price_analysis", ""),
            "alternatives": rec.get("alternatives", []),
            "confidence": rec.get("confidence", "medium"),
        }

        logger.info(f"Recommendation: {recommendation['winner_platform']} (confidence: {recommendation['confidence']})")
        emit("Done — results ready.", stage="recommend", kind="done")
        return {"recommendation": recommendation, "status": "done"}

    except Exception as e:
        logger.error(f"Recommendation failed: {e}")
        # Fallback: pick the cheapest platform mechanically
        if ranked and ranked[0].get("best_result"):
            best = ranked[0]
            return {
                "recommendation": Recommendation(
                    winner_platform=best["platform_id"],
                    winner_result=best.get("best_result", {}),
                    reasoning=f"Selected {best['platform_name']} as it has the lowest price found.",
                    price_analysis=f"Min price: {best.get('min_price', 'N/A')}",
                    alternatives=[],
                    confidence="low",
                ),
                "status": "done",
            }
        return {"status": "error", "error": f"Recommendation failed: {e}"}
