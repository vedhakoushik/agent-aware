# Design Notes

Why the system is built this way — the decisions that weren't obvious, and the
trade-offs I made knowingly rather than by accident. See [ARCHITECTURE.md](ARCHITECTURE.md)
for *what* the system is; this is *why*.

---

## RAG search-results cache

**Problem:** the two most expensive things this app does — a browser-use run (30–160s,
one LLM call per navigation step) and the per-platform parse call — both re-run in full
for a search that's effectively the same as one answered five minutes ago. Re-searching
"hotels in Manali, 22–25 Jul" twice in a session was paying the full cost twice for no
new information.

**Decision: cache at the *result* level, gated by embedding similarity, not at the
query-string level.** A naive `dict[query_string] → results` cache would miss "hotels in
Manali this weekend" vs. "hotel Manali 22 Jul – 25 Jul" even though they're the same
search after parsing. Caching *after* intent parsing (keyed on `intent_type` +
structured `params`, embedded as text) catches that equivalence without needing an exact
string match.

**Decision: cache placement is tier 0, before Tavily** — not a wrapper around the whole
platform search, and not just around the parse call. Placing it first means a hit skips
*everything* downstream (Tavily, scrape, browser-use, the parse LLM call) — that's the
whole point, since the parse call and the browser-use run are the two expensive parts.
Placing it only around the parse call would still pay for a fresh browser-use run on
every search, which defeats most of the purpose.

**Decision: exact `(platform_id, intent_type)` match via a `where` filter, semantic
similarity only within that scope.** Embeddings are approximate by nature; without a
hard filter, a search for Airbnb listings could conceivably surface a Booking.com cache
entry with a close-enough embedding. Scoping first, then ranking by similarity inside
that scope, makes a cross-platform leak structurally impossible rather than "unlikely."

**Decision: category-aware TTL (20 min for flight/hotel/train/bus/car_rental, 60 min for
product/event/restaurant/general), not one global TTL.** Prices for travel inventory
move fast and matter for a purchase decision; a product listing or restaurant's
existence doesn't change minute to minute. A single conservative TTL would either be too
short to help the slow-moving categories or too long to be honest for the fast-moving
ones.

**Decision: a cache hit is always labeled, never silently presented as a live result.**
This app's whole selling point (see the Agent Communication feed) is transparency about
what the system actually did. Serving a 20-minute-old hotel price as if it were just
fetched would quietly undermine that. The UI tier label is literally `"⚡ Cached (recent
search)"` (`frontend/app.py`, `_TIER_LABEL`), not folded into "tavily" or hidden.

**Decision: fail open, always.** Every cache read/write is wrapped so a Chroma error,
embedding-model hiccup, or malformed metadata degrades to "cache miss, run the normal
cascade" — never to a broken search. This matches the existing `price_history` store's
philosophy (`backend/memory/store.py`) and this app's general rule that an auxiliary
system's failure must never take down the primary path.

**Open trade-off I'm not fully resolving here:** the similarity threshold
(`SEARCH_CACHE_MAX_DISTANCE=0.15`, cosine distance) is a judgment call tuned by hand, not
learned or validated against a labeled set of "should this count as the same search"
pairs. It's deliberately strict (near-identical params only) to bias toward "cache
misses that fall back to a live search" over "cache hits that serve the wrong trip" —
but the exact number is a guess I'd want more usage data to refine.

---

## Per-platform tier cascade: cheapest first, most expensive last

Tavily (~3s, no LLM-in-the-loop navigation) is tried before the deep-link scrape
(fast but only works on server-rendered pages) before browser-use (30–160s, one LLM
call per step) before the last-resort form-filler and search-engine fallbacks. This
mirrors how production search systems treat an expensive tool call: try the cheap path,
escalate only when it's actually needed. Flipping this — browser-use first "for the best
data" — would make every search minutes-long even when Tavily's snippets were already
sufficient, for no benefit on the majority of queries where cheap sources work fine.

`BROWSER_USE_ON_THIN` exists as an opt-in escalation for the case where Tavily returned
*something* but it's too shallow (e.g., no price) to be useful — off by default because
escalating to the slow tier on "technically got a result but thin" is a much more
aggressive trade than escalating on "got nothing."

---

## Cerebras as the default browser-use driver

browser-use makes **one LLM call per navigation step**, and `max_steps` can be up to a
dozen or more per platform, times however many platforms run per search. Groq and
Gemini's free tiers have low per-minute/per-day caps that this pattern burns through
fast — the original failure mode this app kept hitting. Cerebras's free tier (1M
tokens/day, ~2600 tok/s) absorbs that load without becoming the bottleneck. The trade-off
is Cerebras's ~8k context cap, which is fine for browser-use's per-step DOM-element list
but would truncate a large parse payload — that's why `search_parse` (the heavier call)
still fails over to a big-context provider (Gemini/Groq) when a page's content overflows
Cerebras's window (`_is_retryable()` in `llm.py` matches context-length errors
specifically for this).

---

## Parallel browser-use: a bounded trade, not "more is free"

Each browser-use agent is a full, real Chrome process. Running them in parallel
(`BROWSER_USE_CONCURRENCY > 1`) means more searches finish before the overall timeout —
but it's bounded by three real constraints, not a dial that goes to infinity:

1. **RAM** — each Chrome process is meaningfully heavy; on a machine already low on free
   memory, adding parallel Chromes makes *everything* slower, including the app itself.
2. **LLM throughput** — N parallel agents means N× the call rate to whatever provider
   they share. Past a point this just shifts the bottleneck from "browsers running
   serially" to "LLM calls queuing/rate-limiting," which is why the Cerebras switch
   (above) and the concurrency limit have to be considered together, not separately.
3. **Profile-lock stability** — an earlier version of this app attached to a single
   shared Chrome via CDP, and parallel agents collided on that connection (`Event loop
   is closed`), silently falling back to a headless "Chrome for Testing" instance. The
   fix was launching a **separate real Chrome with its own fresh temp profile per
   agent**, which is what makes >1 concurrency safe *at all* — but it's still more
   processes fighting for the same machine's resources.

Given those, `BROWSER_USE_CONCURRENCY` defaults conservatively and is a `.env` knob
specifically so it can be tuned to the machine it's running on, rather than hard-coded
to a number that's right for nobody.

---

## `no_results` as a hard short-circuit, not a soft empty state

`_route_after_search` in `graph.py` routes straight to a terminal `no_results` node when
zero platforms returned anything, skipping `compare → segregate → insights → recommend`
entirely. The alternative — running the full analysis pipeline on empty data — risks the
Recommendation Agent producing a plausible-sounding "best pick" grounded in nothing, which
is exactly the failure mode this app's `validate` node otherwise works to prevent
downstream. It's cheaper and more honest to stop upstream of the LLM calls that could
fabricate a result than to generate one and hope the critic catches it.

---

## `validate → remediate`: bounded loop, deterministic-first

The validator (`backend/nodes/validate.py`) runs deterministic checks (coverage,
groundedness, a `best_choice` recompute from real data) *before* an LLM-as-judge
coherence pass — and explicitly **skips** the LLM judge when a deterministic fix was
just applied, to avoid the judge second-guessing a fix that's already known-correct
(noise, not signal). The loop is bounded by `REMEDIATION_MAX_ROUNDS` (default 3) because
an unbounded "keep trying to fix it" loop against sites that are genuinely bot-walled or
genuinely out of inventory would just burn time and LLM calls chasing something that
isn't fixable — three rounds is enough to recover from a transient miss, not enough to
loop forever against a hard wall.

When a hard constraint (e.g. a budget) truly can't be satisfied, the validator keeps the
cheapest real option and attaches a `constraint_note` with proof (budget, cheapest found,
results checked, price range) — it never silently relaxes the constraint or fabricates a
result that fits it. This is the same "never fabricate" principle the RAG cache's
labeling and the `no_results` short-circuit both serve, applied at a different point in
the pipeline.

---

## General failure philosophy

Every auxiliary system in this app — the RAG cache, the price-history store, the
reliability ranker, the monitor agent's diagnosis — is wrapped so its own failure
degrades gracefully to "skip this optimization/context" rather than breaking the primary
search. Only the primary path (parse the intent, search the platforms, aggregate, judge)
is allowed to actually fail the run. This is a deliberate asymmetry: it's fine for a
convenience feature to quietly not help on a given run; it's not fine for a convenience
feature to be the reason a search that otherwise would have worked doesn't.
