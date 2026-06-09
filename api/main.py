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
