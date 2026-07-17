"""
Entry point — starts the FastAPI backend that powers the React frontend (web/).

Usage:
    python run.py               # starts the API server (port 8000)
    python run.py --test        # runs a quick test query in the terminal

Frontend (separate terminal):
    cd web && npm run dev       # http://localhost:5173
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


def start_api():
    check_env()
    print("🚀 Starting Agent-Aware API at http://localhost:8000")
    print("   Frontend: cd web && npm run dev  →  http://localhost:5173")
    subprocess.run([
        sys.executable, "-m", "uvicorn", "api.main:app",
        "--host", "0.0.0.0", "--port", "8000",
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

    if mode == "--test":
        run_test()
    else:
        start_api()
