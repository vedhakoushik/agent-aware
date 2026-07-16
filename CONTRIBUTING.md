# Contributing to Agent-Aware

Thanks for your interest in contributing!

## Getting Started

1. Fork the repo and clone your fork
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your API keys

## Making Changes

- Branch from `main` using a descriptive name: `feat/my-feature`, `fix/search-timeout`, `docs/update-readme`
- Keep commits focused — one logical change per commit
- Run the app locally and verify your change works before submitting

## Pull Requests

- Open a PR against `main`
- Describe **what** changed and **why** — link to an issue if one exists
- Include a short demo or screenshot for UI changes

## Adding a New Search Provider

1. Create `backend/agents/<provider>_agent.py` following the pattern in existing agents
2. Register it in `backend/agents/coordinator.py`
3. Add the required env vars to `.env.example` with a comment explaining where to get them

## Reporting Issues

Open a GitHub issue with:
- What you searched for
- What you expected vs. what actually happened
- Which LLM provider was active (shown in the sidebar)
- Any error output from the terminal

## Code Style

- Python: follow PEP 8, use type hints where practical
- Keep agent logic self-contained — agents should not import from each other directly, use the message bus
