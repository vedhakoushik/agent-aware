"""
Graph-powered follow-up chat (GraphRAG over the CURRENT search's results).

When the user has results and asks a filter/sort question — "only non-stop",
"cheapest under 9000", "not IndiGo", "fastest" — we answer it with a precise Cypher
query over a small in-memory Neo4j graph of the current results, instead of making the
LLM re-reason over JSON. The LLM only writes the Cypher; Neo4j does the exact filtering.

Entirely OPTIONAL: if NEO4J_URL/PASSWORD aren't set or Neo4j isn't reachable, every
function degrades to a no-op and the app behaves exactly as before.
"""
from __future__ import annotations

import os
import re
import logging

logger = logging.getLogger(__name__)


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("₹", "").replace("$", "").strip().split()[0])
    except Exception:
        return None


def _driver():
    """A connected Neo4j driver, or None if not configured/reachable (never raises)."""
    try:
        from neo4j import GraphDatabase
        url = os.getenv("NEO4J_URL", "").strip()
        pw = os.getenv("NEO4J_PASSWORD", "").strip()
        if not url or not pw:
            return None
        d = GraphDatabase.driver(url, auth=(os.getenv("NEO4J_USER", "neo4j"), pw))
        d.verify_connectivity()
        return d
    except Exception as e:
        logger.debug(f"Neo4j unavailable for graph chat: {e}")
        return None


_WRITE_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|CALL\s*\{|LOAD\s+CSV)\b", re.I)


def run_cypher(cypher: str, params: dict | None = None) -> dict:
    """Run a RAW Cypher query directly against the graph and return the rows as plain
    dicts. This is for direct retrieval — you write the query, no LLM involved.

    Read-only by design: write/delete clauses are rejected so the UI can never corrupt
    the graph. Returns {ok, rows, columns, error}."""
    if _WRITE_CLAUSES.search(cypher or ""):
        return {"ok": False, "error": "Only read queries are allowed here (no CREATE/MERGE/DELETE/SET/…).",
                "rows": [], "columns": []}
    d = _driver()
    if not d:
        return {"ok": False, "error": "Neo4j is not reachable (check NEO4J_URL/PASSWORD).",
                "rows": [], "columns": []}
    try:
        with d.session() as s:
            result = s.run(cypher, params or {})
            columns = list(result.keys())
            rows = [rec.data() for rec in result]
        return {"ok": True, "rows": rows, "columns": columns, "error": None}
    except Exception as e:
        return {"ok": False, "error": str(e), "rows": [], "columns": []}
    finally:
        d.close()


def available() -> bool:
    d = _driver()
    if d:
        d.close()
        return True
    return False


def index_search(query: str, platform_results: dict) -> bool:
    """Load the current search's results into Neo4j (label :CUR), replacing the prior
    search. So the follow-up chat always queries exactly what's on screen."""
    d = _driver()
    if not d:
        return False
    try:
        with d.session() as s:
            s.run("MATCH (n:CUR) DETACH DELETE n")          # only this search lives under :CUR
            s.run("CREATE (:CUR:CurSearch {query:$q})", q=query or "")
            for pid, pr in (platform_results or {}).items():
                if not isinstance(pr, dict):
                    continue
                pname = pr.get("platform_name", pid)
                for r in (pr.get("results") or []):
                    s.run(
                        """MATCH (cs:CUR:CurSearch)
                           CREATE (cs)-[:RESULT]->(:CUR:CurResult {
                             name:$name, price:$price, stops:$stops, airline:$airline,
                             cabin:$cabin, duration:$duration, rating:$rating,
                             platform:$platform, url:$url})""",
                        name=str(r.get("name", "")), price=_num(r.get("price")),
                        stops=_num(r.get("stops")), airline=str(r.get("airline") or ""),
                        cabin=str(r.get("cabin_class") or r.get("cabin") or ""),
                        duration=str(r.get("duration") or ""), rating=_num(r.get("rating")),
                        platform=str(pname), url=str(r.get("url") or ""))
        return True
    except Exception as e:
        logger.warning(f"graph index_search failed: {e}")
        return False
    finally:
        d.close()


_SCHEMA = ("(:CurSearch {query})-[:RESULT]->(:CurResult {name, price (number), "
           "stops (number; 0 = non-stop), airline, cabin, duration, rating, platform, url})")


def _format(rows: list) -> str:
    n = len(rows)
    head = f"Here {'is' if n == 1 else 'are'} {n} matching option{'' if n == 1 else 's'} from your current results:"
    lines = []
    for r in rows[:8]:
        p = r.get("price")
        price = f"₹{int(p):,}" if p else ""
        det = []
        if r.get("airline"):
            det.append(r["airline"])
        st = r.get("stops")
        if st == 0:
            det.append("non-stop")
        elif st:
            det.append(f"{int(st)} stop")
        for k in ("cabin", "duration", "platform"):
            if r.get(k):
                det.append(str(r[k]))
        tail = (" · " + " · ".join(str(x) for x in det)) if det else ""
        lines.append(f"• **{r.get('name', '')}** — {price}{tail}")
    return head + "\n" + "\n".join(lines)


def _deterministic_cypher(q: str) -> str | None:
    """Build Cypher WITHOUT an LLM for the common filter/sort follow-ups — fast and
    100% reliable even when every LLM is rate-limited. Returns None if no rule matches
    (then we fall back to the LLM text-to-Cypher path)."""
    ql = q.lower()
    where, order = [], ""
    if re.search(r"non[- ]?stop|nonstop|direct", ql):
        where.append("r.stops = 0")
    m = re.search(r"(?:under|below|less than|cheaper than|<|upto|up to)\s*(?:rs\.?|₹|inr)?\s*([0-9][0-9,]*)", ql)
    if m:
        where.append(f"r.price < {int(m.group(1).replace(',', ''))}")
    m = re.search(r"(?:over|above|more than|>|at least)\s*(?:rs\.?|₹|inr)?\s*([0-9][0-9,]*)", ql)
    if m:
        where.append(f"r.price > {int(m.group(1).replace(',', ''))}")
    for air in ("indigo", "akasa", "air india", "vistara", "spicejet", "go first", "goair", "emirates"):
        if air in ql:
            neg = re.search(r"(?:not|except|no|other than|avoid|without)\b[^.]*" + re.escape(air), ql)
            where.append(f"toLower(r.airline) {'<>' if neg else 'CONTAINS'} '{air}'")
    if re.search(r"\b(business|first class|premium)\b", ql):
        cls = "business" if "business" in ql else ("first" if "first" in ql else "premium")
        where.append(f"toLower(r.cabin) CONTAINS '{cls}'")
    if re.search(r"cheap|lowest price|least expensive|best price|low.?cost", ql):
        order = "ORDER BY r.price ASC"
    elif re.search(r"expensive|highest price|costliest|most expensive", ql):
        order = "ORDER BY r.price DESC"
    if not where and not order:
        return None
    w = ("WHERE " + " AND ".join(where)) if where else ""
    return f"MATCH (r:CurResult) {w} RETURN r {order}".strip()


def _llm_cypher(question: str) -> str | None:
    from backend.llm import chat
    sys = ("Translate the user's follow-up into ONE read-only Cypher over the CURRENT search "
           "results they're already looking at. Schema: " + _SCHEMA + ". They are filtering or "
           "sorting what they already have. Match a CurResult as `r` and RETURN r. Output ONLY the "
           "Cypher. If the question needs a brand-NEW search (different route/date/product) or isn't "
           "about the current results, output exactly: NEW_SEARCH")
    resp = chat("chat", messages=[{"role": "system", "content": sys},
                                  {"role": "user", "content": question}], temperature=0, max_tokens=300)
    cy = re.sub(r"^```(cypher)?|```$", "", resp.choices[0].message.content.strip(), flags=re.I | re.M).strip()
    if "MATCH" not in cy.upper() or "NEW_SEARCH" in cy.upper():
        return None
    return cy


def ask_llm(question: str) -> dict:
    """Always use the LLM to translate the question into Cypher (skips the
    deterministic rules in _deterministic_cypher, which would otherwise intercept
    common phrasings) — so you can SEE exactly what the LLM writes for any question,
    every time. Runs the generated query and returns it alongside the results.
    Returns {ok, cypher, rows, columns, error}."""
    d = _driver()
    if not d:
        return {"ok": False, "cypher": None, "rows": [], "columns": [],
                "error": "Neo4j is not reachable (check NEO4J_URL/PASSWORD)."}
    cy = None
    try:
        cy = _llm_cypher(question)
        if not cy:
            return {"ok": False, "cypher": None, "rows": [], "columns": [],
                    "error": "The LLM couldn't turn that into a graph query about the "
                             "current results — try rephrasing, or it may need a new search."}
        with d.session() as s:
            result = s.run(cy)
            columns = list(result.keys())
            rows = [rec.data() for rec in result]
        return {"ok": True, "cypher": cy, "rows": rows, "columns": columns, "error": None}
    except Exception as e:
        return {"ok": False, "cypher": cy, "rows": [], "columns": [], "error": str(e)}
    finally:
        d.close()


def answer_followup(question: str) -> dict:
    """Try to answer a follow-up by querying the current-results graph (deterministic
    rules first, LLM text-to-Cypher as fallback). Returns {answered, message, results,
    cypher, via}. answered=False → the caller falls back to normal chat / refined-search."""
    d = _driver()
    if not d:
        return {"answered": False}
    try:
        cy = _deterministic_cypher(question)
        via = "rule"
        if not cy:
            cy = _llm_cypher(question)
            via = "llm"
        if not cy:
            return {"answered": False}
        with d.session() as s:
            rows = []
            for rec in s.run(cy):
                dd = rec.data()                       # Nodes auto-convert to dicts
                if isinstance(dd.get("r"), dict):     # RETURN r  → the node
                    rows.append(dd["r"])
                else:                                  # RETURN r.name, r.price …  or a node under another alias
                    node = next((v for v in dd.values() if isinstance(v, dict)), None)
                    rows.append(node if node else dd)
        if not rows:
            return {"answered": True, "results": [], "cypher": cy, "via": via,
                    "message": "None of the current results match that — want me to run a wider search?"}
        return {"answered": True, "results": rows, "cypher": cy, "via": via, "message": _format(rows)}
    except Exception as e:
        logger.info(f"graph follow-up fell back to normal chat: {e}")
        return {"answered": False}
    finally:
        d.close()
