"""
Entry point — starts the Streamlit frontend directly (no separate API server needed).
The frontend calls the LangGraph backend inline when the API server isn't running.

Usage:
    python run.py               # starts Streamlit UI
    python run.py --api         # starts FastAPI server only (port 8000)
    python run.py --both        # starts both (requires two terminals)
    python run.py --test        # runs a quick test query in the terminal
"""
import os
import sys
import subprocess
from dotenv import load_dotenv

load_dotenv()


def check_env():
    if not os.getenv("GROQ_API_KEY"):
        print("❌  GROQ_API_KEY not set. Copy .env.example to .env and add your key.")
        print("    Get a free key at: https://console.groq.com/")
        sys.exit(1)


def start_streamlit():
    check_env()
    print("🚀 Starting Agent-Aware UI at http://localhost:8501")
    subprocess.run([
        sys.executable, "-m", "streamlit", "run",
        os.path.join(os.path.dirname(__file__), "frontend/app.py"),
        "--server.port", "8501",
        "--server.headless", "true",
    ])


def start_api():
    check_env()
    print("🚀 Starting Agent-Aware API at http://localhost:8000")
    subprocess.run([
        sys.executable, "-m", "uvicorn", "api.main:app",
        "--host", "0.0.0.0", "--port", "8000", "--reload",
    ])


def run_test():
    check_env()
    from backend.graph import run_search
    import json

    queries = [
        "flights from Delhi to Mumbai tomorrow cheapest",
        "budget hotels in Goa this weekend",
        "best biryani restaurants in Hyderabad",
    ]
    query = queries[0]
    if len(sys.argv) > 2:
        query = " ".join(sys.argv[2:])

    print(f"\n🔍 Testing query: {query}\n")
    state = run_search(query)

    print(f"Status: {state['status']}")
    print(f"Intent: {json.dumps(state.get('intent'), indent=2)}")
    print(f"\nPlatforms searched: {list(state.get('platform_results', {}).keys())}")
    print(f"Total results: {len(state.get('normalized', []))}")

    rec = state.get("recommendation")
    if rec:
        print(f"\n🏆 Recommendation: {rec.get('winner_platform')}")
        print(f"   Reasoning: {rec.get('reasoning')}")
        print(f"   Confidence: {rec.get('confidence')}")

    if state.get("error"):
        print(f"\n❌ Error: {state['error']}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""

    if mode == "--api":
        start_api()
    elif mode == "--test":
        run_test()
    elif mode == "--both":
        print("Start two terminals:")
        print("  Terminal 1: python run.py --api")
        print("  Terminal 2: python run.py")
    else:
        start_streamlit()
