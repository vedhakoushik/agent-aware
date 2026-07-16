# Architecture

This document maps the actual system: the LangGraph pipeline, the per-platform search
cascade, the LLM router, and where state lives. Every node/edge/env-var named here is
real code, not aspirational — see the file path next to each component.

---

## 1. Top-level pipeline (`backend/graph.py`)

A single `LangGraph` `StateGraph` over `AgentState` (`backend/state.py`). Every node is
wrapped by `_timed()`, which records wall-clock time into the run diagnostics **and**
publishes a handoff summary to the Agent Communication bus — this is what the live
"Agent Communication" feed in the UI is built from; it is not a separate logging system.

```
                              ┌────────────────┐
                    query ──► │  parse_intent  │  Intent Agent — LLM classifies
                              └───────┬────────┘  category + extracts params
                                      │
                       clarification  │  needs more info
                       needed / error │──────────────────────────► END
                                      │
                                      ▼
                            ┌──────────────────┐
                            │ search_platforms  │  fans out to N platform workers
                            └─────────┬─────────┘  (see §2 — the per-platform cascade)
                                      │
                     0 results total  │  ≥1 result
                        ┌─────────────┴─────────────┐
                        ▼                            ▼
                 ┌─────────────┐              ┌────────────┐
                 │ no_results  │──► END        │  aggregate │  Aggregator
                 └─────────────┘              └─────┬──────┘
                                                     ▼
                                               ┌────────────┐
                                               │  compare   │  Comparison Agent
                                               └─────┬──────┘
                                                     ▼
                                               ┌────────────┐
                                               │  segregate │  Grouping Agent
                                               └─────┬──────┘
                                                     ▼
                                               ┌────────────┐
                                               │  insights  │  Insights Agent
                                               └─────┬──────┘
                                                     ▼
                                               ┌────────────┐
                                               │  recommend │  Recommendation Agent
                                               └─────┬──────┘
                                                     ▼
                                    ┌────────────────────────────────┐
                                    │            validate             │  Critic:
                                    │  deterministic checks (coverage, │  groundedness,
                                    │  best_choice recompute) + LLM    │  coherence judge
                                    └───────────────┬──────────────────┘
                          plan queued AND            │  no plan, or round
                          round < REMEDIATION_        │  cap reached
                          MAX_ROUNDS (default 3)      │
                                    ▼                 ▼
                          ┌──────────────────┐       END ──► You (final recommendation)
                          │    remediate      │  Actor: re-searches
                          │  (re-search empty  │  empty platforms,
                          │  platforms, queue   │  queues a recommend
                          │  regeneration)      │  regen
                          └─────────┬──────────┘
                                    │
                     data_changed ──┼── regen ──┬── neither
                          ▼         │            ▼          ▼
                    → aggregate     │      → recommend  → validate
                    (re-pipe with   │      (regen only,
                     fresh data)    │       no re-search)
                                    ▼
                              (loop bounded by
                               remediation_round
                               vs REMEDIATION_MAX_ROUNDS)
```

**Why `no_results` is a separate terminal node, not just empty data through the normal
path:** running compare/segregate/insights/recommend on zero results would let the
Recommendation Agent fabricate a "best pick" from nothing. Short-circuiting there is a
deliberate anti-hallucination guard (`_route_after_search` in `graph.py`).

**Why `validate → remediate` is a bounded loop, not a single retry:** a single
remediation round might only get some empty platforms unstuck, or a re-search might
change the data enough that ranking needs to happen again. The loop keeps going —
re-piping through `aggregate` when new data arrived, or straight to `recommend` for a
regen-only fix — until either the plan is empty or `REMEDIATION_MAX_ROUNDS` is hit.

---

## 2. Per-platform search cascade (`backend/nodes/search.py`, `_search_one_platform_sync`)

Each platform in the intent's plan runs this cascade independently (fanned out via
`ThreadPoolExecutor`, capped by `SEARCH_MAX_CONCURRENCY`, default 3). Every tier is
attempted only if the previous one produced nothing — cheapest/fastest first, most
expensive last — and every attempt is timed and recorded for the diagnostics panel.

```
 START (per platform)
   │
   ▼
 0. RAG cache lookup ─────────────────────── HIT ──► done (tier="cache", instant,
   │  (backend/memory/search_cache.py)                labeled "⚡ Cached" in UI)
   │  skipped on a user-driven retry (force_browser)
   ▼ MISS
 1. Tavily search (own-domain, then open) ── results ──► parse (LLM call) ──► done
   │  skipped if force_browser or cache hit
   ▼ still empty
 1b. Deterministic deep-link scrape ──────── results ──► parse (LLM call) ──► done
   │  opt-in per platform (fast_scrape: true in platforms.yaml); no LLM in the
   │  browse loop itself, just the final parse
   ▼ still empty
 2. browser-use agent (real Chrome) ──────── results ──► parse (LLM call) ──► done
   │  an LLM drives the browser step-by-step (see §3); this is the slow,
   │  LLM-hungry tier — one call per navigation step, plus the final parse call
   ▼ still empty
 2b. Universal form-filler (last-resort automation) ──► parse (LLM call) ──► done
   ▼ still empty
 3. Google search (Playwright) ───────────── results ──► parse (LLM call) ──► done
   ▼ still empty
 4. DuckDuckGo (last resort) ─────────────── results ──► parse (LLM call) ──► done
   ▼ still empty
 → roadblock (plain-English reason + monitor agent's diagnosis + suggested hint,
   surfaced in the UI with a "help it along" retry)

 On ANY non-cache success → write-through to the RAG cache for next time.
```

**Why this order:** production search engines escalate to the expensive tier rarely —
cheap retrieval first, the slow LLM-driven browser only when everything cheaper came up
empty. Tavily is ~3s and free of browser overhead; browser-use is 30–160s and makes one
LLM call per step. Putting the cache at tier 0 means a repeated/similar query never even
reaches Tavily.

---

## 3. Inside the browser-use tier (`backend/tools/browser_agent.py`)

```
 _run_agent(platform, entry_url, params, max_steps)
   │
   ├─ pick LLM driver: _pick_browser_llm()
   │     BROWSER_USE_PROVIDER=cerebras (default) → gpt-oss-120b, 1M tok/day,
   │     no rate-limit wall under one-call-per-step load
   │     · groq / gemini / ollama also selectable; auto-mode tries
   │       cerebras → groq (if serving) → gemini → ollama
   │     · fallback_llm = local Ollama, so a mid-run 429 degrades to
   │       "slower" instead of "dead"
   │
   ├─ launch REAL Chrome (never the bundled "Chrome for Testing")
   │     fresh temp profile per run by default; headless=False always
   │     up to BROWSER_USE_CONCURRENCY agents run in parallel, each in
   │     its own Chrome process — bounded by free RAM, not just steps
   │
   └─ loop, up to max_steps (BROWSER_USE_MAX_STEPS):
        1. PERCEIVE — browser-use reads the DOM, indexes every clickable
           element (no vision on Cerebras/Groq/Ollama — text-only)
        2. REASON   — the LLM picks ONE action: click(i) / input_text(i,…) /
           scroll / go_to_url / extract_content / done
        3. ACT      — browser-use executes it via Playwright, waits for the
           page to settle (BROWSER_USE_IDLE_WAIT / MIN_WAIT / ACTION_WAIT)
        → repeat, or stop early after max_failures consecutive errors
```

Every step is mirrored to the Agent Communication bus (`register_new_step_callback`),
which is what the live "agent step" strip under the screenshot panel shows.

---

## 4. LLM router (`backend/llm.py`)

```
 chat(task, messages, …)
   │
   ├─ look up TASK_ROUTING[task] → (primary_provider, model)
   │     intent/search_parse/universal/monitor → cerebras : gpt-oss-120b
   │     insights/recommend/validate            → groq    : llama-3.3-70b
   │     segregate/chat                         → groq default
   │
   ├─ build the failover chain for that task: primary → the other
   │     registered providers (ollama, groq, gemini, cerebras,
   │     openrouter) in a fixed order, skipping any without a key
   │
   └─ try each in order; on a rate-limit/quota/context-length error,
      fail over to the next; return the first success
```

**Why Cerebras is the default for `search_parse` and browser-use both:** it's the
heaviest LLM consumer in the app (one call per platform for parsing, one call per
navigation step for browsing) and Cerebras's 1M-tokens/day free tier absorbs that load
without hitting the daily/per-minute walls that Groq and Gemini's free tiers hit under
the same load. Its ~8k context cap is the trade-off — oversized parses fail over to a
big-context provider automatically.

---

## 5. State & memory (where things are stored)

| Store | Location | Holds | Lifetime |
|---|---|---|---|
| `AgentState` | in-process (LangGraph) | the current run's query, intent, results, recommendation | one search |
| Agent Communication bus | `backend/agent_bus.py`, in-process singleton | every inter-agent message this run | one search (snapshotted into the result after) |
| Browser tracker | `backend/browser_tracker.py`, in-process | live browser-use steps + errors, per platform | one search |
| Reliability history | `backend/memory/reliability.py` | which platforms actually return usable results, to reorder future plans | across runs, local file |
| Price history | `backend/memory/store.py` → ChromaDB `price_history` | past result snippets, used as LLM context for `recommend` | across runs, persisted to `data/chroma/` |
| **RAG search cache** | `backend/memory/search_cache.py` → ChromaDB `search_cache` | full parsed results per (platform, category, params), for cascade short-circuit | across runs, TTL-bounded (20–60 min), persisted to `data/chroma/` |

The last two are separate ChromaDB collections in the same store — `price_history` is
read-only *context* fed to the recommender's prompt (it never skips a search),
`search_cache` is a read-write *result cache* that can skip the search entirely. Keeping
them separate means changing one's TTL/similarity behavior can't accidentally change the
other's.

---

## 6. Frontend (`frontend/app.py`)

Single Streamlit process. Three live panels stream from the backend during a run
(polled each tick from the in-process trackers above, not pushed):
- **Progress checklist** — stage-by-stage status (`backend/progress.py` events)
- **Live browser panel** — full-width screenshot stream (`st.image`, base64 PNG) of
  whichever page is being controlled, plus a text "current step" strip per agent
- **Agent Communication feed** — every inter-agent message with its payload, expandable

Per-platform result tabs, the roadblock/retry UI, and the validation report render from
the final `AgentState` once the graph run completes.
