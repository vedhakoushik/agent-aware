import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up the graph on startup
    from backend.graph import get_graph
    get_graph()
    logger.info("LangGraph compiled and ready")
    yield


app = FastAPI(title="Agent-Aware API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str


class SearchResponse(BaseModel):
    query: str
    status: str
    intent: dict | None = None
    platform_results: dict = {}
    comparison: dict | None = None
    insights: dict | None = None
    recommendation: dict | None = None
    error: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        from backend.graph import run_search
        state = run_search(req.query.strip())
        return SearchResponse(
            query=state["query"],
            status=state.get("status", "done"),
            intent=state.get("intent"),
            platform_results={
                pid: {
                    "platform_name": r.get("platform_name", pid),
                    "icon": r.get("icon", "🔍"),
                    "results": r.get("results", []),
                    "error": r.get("error"),
                    "elapsed_seconds": r.get("elapsed_seconds", 0),
                }
                for pid, r in state.get("platform_results", {}).items()
            },
            comparison=state.get("comparison"),
            insights=state.get("insights"),
            recommendation=state.get("recommendation"),
            error=state.get("error"),
        )
    except Exception as e:
        logger.exception(f"Search failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/platforms")
def get_platforms():
    """Return all platform configs from YAML."""
    import yaml, os
    config_path = os.path.join(os.path.dirname(__file__), "../config/platforms.yaml")
    with open(os.path.normpath(config_path), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config


# ═══════════════════════════════════════════════════════════════════════════
# Live job API — powers the React frontend (web/).
#
# The in-process trackers (progress / agent bus / diagnostics / browser) are
# process-global singletons, so exactly ONE search runs at a time; /search/start
# rejects a second concurrent run with 409. The frontend polls /search/live
# while the job runs, then fetches the full result from /search/result/{id}.
# ═══════════════════════════════════════════════════════════════════════════
import threading
import uuid

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _serialize_state(s: dict) -> dict:
    """Full pipeline state → JSON, same shape the Streamlit UI consumed."""
    return {
        "query": s.get("query"),
        "status": s.get("status", "done"),
        "intent": s.get("intent"),
        "platform_results": {
            pid: {
                "platform_name": r.get("platform_name", pid),
                "icon": r.get("icon", "🔍"),
                "results": r.get("results", []),
                "error": r.get("error"),
                "elapsed_seconds": r.get("elapsed_seconds", 0),
                "tier": r.get("tier", ""),
                "roadblock": r.get("roadblock"),
            }
            for pid, r in (s.get("platform_results") or {}).items()
        },
        "comparison": s.get("comparison"),
        "segments": s.get("segments"),
        "insights": s.get("insights"),
        "recommendation": s.get("recommendation"),
        "diagnostics": s.get("diagnostics"),
        "browser_runs": s.get("browser_runs"),
        "agent_comms": s.get("agent_comms"),
        "validation": s.get("validation"),
        "remediation_log": s.get("remediation_log"),
        "error": s.get("error"),
    }


@app.post("/search/start")
def search_start(req: SearchRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    with _jobs_lock:
        if any(j["status"] == "running" for j in _jobs.values()):
            raise HTTPException(status_code=409, detail="A search is already running")
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"status": "running", "query": query, "result": None, "error": None}

    def _worker():
        try:
            from backend.graph import run_search
            state = run_search(query)
            _jobs[job_id]["result"] = _serialize_state(state)
            _jobs[job_id]["status"] = "done"
            # Best-effort: index results into Neo4j so /graph/* reflects this run.
            try:
                from backend import graph_chat as gc
                if gc.available():
                    gc.index_search(query, state.get("platform_results") or {})
            except Exception:
                pass
        except Exception as e:  # surfaced via /search/result
            logger.exception(f"Search job {job_id} failed: {e}")
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["status"] = "error"

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id}


@app.get("/search/live")
def search_live():
    """One poll = every live surface: progress events, agent messages,
    the eval checklist, per-platform browser steps, and the latest screenshot."""
    from backend.progress import get_tracker
    from backend.agent_bus import get_bus
    from backend.diagnostics import get_diagnostics
    from backend.browser_tracker import get_browser_tracker

    running = any(j["status"] == "running" for j in _jobs.values())
    shot = None
    try:
        latest = get_browser_tracker().latest_screenshot()
        if latest:
            shot = {"platform": latest[0], "image_b64": latest[1]}
    except Exception:
        shot = None
    return {
        "running": running,
        "events": get_tracker().snapshot(),
        "comms": get_bus().snapshot(),
        "diagnostics": get_diagnostics().snapshot(),
        "browser_runs": get_browser_tracker().snapshot(),
        "screenshot": shot,
    }


@app.get("/search/result/{job_id}")
def search_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Unknown job id")
    return {"status": job["status"], "query": job["query"],
            "result": job["result"], "error": job["error"]}


@app.post("/search/cancel")
def search_cancel():
    from backend.progress import request_cancel
    request_cancel()
    return {"ok": True}


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    original_query: str = ""
    platform_results: dict = {}
    comparison: dict = {}
    recommendation: dict = {}
    intent: dict = {}


@app.post("/chat")
def chat(req: ChatRequest):
    """Follow-up chat over the current results. Tries the Neo4j graph first
    (exact filter/sort answers), then falls back to the LLM chat agent which
    may also request a refined re-search."""
    try:
        from backend import graph_chat as gc
        if gc.available():
            g = gc.answer_followup(req.message)
            if g and g.get("answered"):
                return {"message": g["message"], "source": "graph",
                        "should_search": False, "refined_query": None}
    except Exception:
        pass
    from backend.nodes.chat_agent import chat_response
    reply = chat_response(
        user_message=req.message,
        chat_history=req.history,
        platform_results=req.platform_results,
        comparison=req.comparison,
        recommendation=req.recommendation,
        intent=req.intent,
        original_query=req.original_query,
    )
    return {"message": reply.get("message", ""), "source": "llm",
            "should_search": bool(reply.get("should_search")),
            "refined_query": reply.get("refined_query")}


class CypherRequest(BaseModel):
    query: str


class AskRequest(BaseModel):
    question: str


@app.get("/graph/available")
def graph_available():
    try:
        from backend import graph_chat as gc
        return {"available": gc.available()}
    except Exception:
        return {"available": False}


@app.post("/graph/cypher")
def graph_cypher(req: CypherRequest):
    from backend import graph_chat as gc
    return gc.run_cypher(req.query)


@app.post("/graph/ask")
def graph_ask(req: AskRequest):
    from backend import graph_chat as gc
    return gc.ask_llm(req.question)
