"""
Landing page — a self-contained, scroll-animated homepage rendered inside a
Streamlit component iframe. Visitors see this first; "Launch app" reloads the
top window with ?app=1 which flips main() into the real search UI.

Design: warm "chocolate-truffle" palette (cream / espresso / burnt-orange),
MetaMask-inspired — clean, vibrant-but-subtle. The signature is a scroll-reactive
AI mascot: a rotating multi-agent orb that leads you down the page (our take on
MetaMask's fox), plus a smooth vertical flow (no jarring horizontal pin).

Everything loads from CDN inside the iframe, so nothing here touches the
Streamlit widget tree or session state.
"""
import streamlit as st
import streamlit.components.v1 as components

LANDING_HTML = r"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@400;600;700;800&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#FBF6E9; --bg2:#F3EAD6; --card:#FFFDF6;
  --ink:#2A1A0A; --ink2:#6B5338; --muted:#9A7E58; --line:rgba(42,26,10,.12);
  --orange:#C05800; --flame:#FF6A1A; --gold:#D98A00; --choco:#713600; --rust:#9A3B00;
  --cream:#FDFBD4;
  --grad:linear-gradient(100deg,#C05800,#FF6A1A 55%,#D98A00);
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font-family:'Space Grotesk',sans-serif;overflow-x:hidden}
::selection{background:var(--flame);color:#fff}
::-webkit-scrollbar{width:9px}
::-webkit-scrollbar-track{background:var(--bg2)}
::-webkit-scrollbar-thumb{background:var(--orange);border-radius:5px}
a{color:inherit;text-decoration:none}
.mono{font-family:'JetBrains Mono',monospace}

/* mascot canvas floats ABOVE content (sections have opaque cream backgrounds,
   so a behind-layer would vanish the moment you scroll past the hero). Kept to
   the right so it never sits on the centred headline; never blocks clicks. */
#mascot{position:fixed;inset:0;z-index:40;pointer-events:none}
.layer{position:relative;z-index:2}
nav{z-index:60}

/* ── Nav ── */
nav{position:fixed;inset:0 0 auto;z-index:60;display:flex;align-items:center;justify-content:space-between;
  padding:16px 4vw;backdrop-filter:blur(12px);background:rgba(251,246,233,.72);border-bottom:1px solid var(--line)}
.nav-logo{font-family:'Unbounded';font-weight:800;font-size:1rem;letter-spacing:.01em;display:flex;align-items:center;gap:10px;color:var(--ink)}
.nav-logo .dot{width:11px;height:11px;border-radius:50%;background:var(--grad);box-shadow:0 0 12px rgba(255,106,26,.7)}
.nav-links{display:flex;gap:26px;font-size:.85rem;color:var(--ink2);font-weight:500}
.nav-links a:hover{color:var(--orange)}
.btn{display:inline-flex;align-items:center;gap:8px;font-weight:600;font-size:.9rem;border-radius:999px;
  padding:12px 26px;transition:transform .2s,box-shadow .2s;cursor:pointer;border:none}
.btn-primary{background:var(--grad);color:#FFFDF6;box-shadow:0 6px 22px rgba(192,88,0,.32)}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 12px 30px rgba(255,106,26,.42)}
.btn-ghost{border:1.5px solid var(--ink);color:var(--ink);background:transparent}
.btn-ghost:hover{background:var(--ink);color:var(--bg)}
nav .btn{padding:9px 20px;font-size:.82rem}

/* ── Hero ── */
.hero{position:relative;min-height:100vh;display:flex;flex-direction:column;justify-content:center;
  align-items:center;text-align:center;padding:130px 5vw 70px}
.hero-inner{max-width:1080px}
.eyebrow{font-family:'JetBrains Mono';font-size:.74rem;letter-spacing:.26em;text-transform:uppercase;
  color:var(--orange);margin-bottom:26px;font-weight:600}
.eyebrow b{color:var(--rust)}
h1.hero-title{font-family:'Unbounded';font-weight:800;font-size:clamp(2.4rem,6.6vw,5.6rem);line-height:1.04;
  letter-spacing:-.015em;color:var(--ink)}
h1 .word{display:inline-block;white-space:nowrap}
h1 .ch{display:inline-block}
h1 .hl{background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.hero-sub{margin:30px auto 0;max-width:640px;color:var(--ink2);font-size:1.1rem;line-height:1.66}
.hero-sub b{color:var(--ink);font-weight:600}
.hero-cta{margin-top:42px;display:flex;gap:16px;justify-content:center;flex-wrap:wrap}
.hero-agents{margin-top:56px;display:flex;gap:10px;justify-content:center;flex-wrap:wrap}
.agent-pill{font-family:'JetBrains Mono';font-size:.72rem;padding:7px 15px;border-radius:999px;
  border:1px solid var(--line);color:var(--ink2);display:flex;align-items:center;gap:7px;background:rgba(255,253,246,.6)}
.agent-pill i{width:7px;height:7px;border-radius:50%;display:inline-block}
.scroll-hint{position:absolute;bottom:26px;left:50%;transform:translateX(-50%);font-family:'JetBrains Mono';
  font-size:.68rem;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);display:flex;
  flex-direction:column;align-items:center;gap:8px}
.scroll-hint .bar{width:1.5px;height:34px;background:linear-gradient(var(--orange),transparent);animation:hintpulse 1.8s ease-in-out infinite}
@keyframes hintpulse{0%,100%{opacity:.3;transform:scaleY(.6)}50%{opacity:1;transform:scaleY(1)}}

/* ── Marquee ── */
.marquee{overflow:hidden;border-top:1px solid var(--line);border-bottom:1px solid var(--line);
  padding:18px 0;background:var(--bg2)}
.marquee-track{display:flex;gap:56px;width:max-content;animation:scrollx 26s linear infinite}
.marquee span{font-family:'Unbounded';font-weight:600;font-size:1rem;color:var(--choco);
  display:flex;align-items:center;gap:56px;white-space:nowrap}
.marquee em{font-style:normal;color:var(--flame)}
@keyframes scrollx{to{transform:translateX(-50%)}}

/* ── Section scaffold ── */
section{position:relative}
.sec-pad{padding:120px 6vw}
.sec-eyebrow{font-family:'JetBrains Mono';font-size:.72rem;letter-spacing:.28em;text-transform:uppercase;
  color:var(--orange);margin-bottom:18px;font-weight:600}
.sec-title{font-family:'Unbounded';font-weight:700;font-size:clamp(1.8rem,3.8vw,3rem);line-height:1.14;max-width:840px;color:var(--ink)}
.sec-title .hl{background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}

/* ── Pipeline: smooth vertical timeline (no pin, no break) ── */
#pipeline{background:radial-gradient(1100px 600px at 15% 0%,rgba(255,106,26,.08),transparent 60%)}
.timeline{position:relative;max-width:900px;margin:64px auto 0;padding-left:64px}
.tl-line{position:absolute;left:26px;top:8px;bottom:8px;width:3px;background:var(--line);border-radius:3px;overflow:hidden}
.tl-fill{position:absolute;inset:0 0 auto;height:0;background:var(--grad);border-radius:3px}
.tl-step{position:relative;margin-bottom:34px}
.tl-node{position:absolute;left:-52px;top:2px;width:52px;height:52px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-family:'JetBrains Mono';font-weight:600;
  font-size:.86rem;color:#FFFDF6;background:var(--pc,var(--orange));
  box-shadow:0 6px 18px color-mix(in srgb,var(--pc,var(--orange)) 45%,transparent);border:4px solid var(--bg)}
.tl-card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:26px 30px;
  border-left:4px solid var(--pc,var(--orange));transition:transform .25s,box-shadow .25s}
.tl-card:hover{transform:translateX(6px);box-shadow:0 14px 34px rgba(42,26,10,.1)}
.tl-tag{font-family:'JetBrains Mono';font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;
  color:var(--pc,var(--orange));margin-bottom:10px;font-weight:600}
.tl-card h3{font-family:'Unbounded';font-weight:600;font-size:1.22rem;margin-bottom:10px;color:var(--ink)}
.tl-card p{color:var(--ink2);font-size:.95rem;line-height:1.62}
.tl-card .stack-note{display:inline-block;margin-top:16px;font-family:'JetBrains Mono';font-size:.68rem;
  color:var(--pc,var(--orange));background:color-mix(in srgb,var(--pc,var(--orange)) 10%,transparent);
  border:1px solid color-mix(in srgb,var(--pc,var(--orange)) 26%,transparent);padding:5px 12px;border-radius:999px}

/* ── Kinetic band ── */
.kinetic{padding:120px 0;overflow:hidden;white-space:nowrap;background:var(--bg2)}
.kinetic .row{font-family:'Unbounded';font-weight:800;font-size:clamp(3rem,9vw,8rem);line-height:1.04;
  letter-spacing:-.01em;will-change:transform;color:var(--choco)}
.kinetic .row.outline{color:transparent;-webkit-text-stroke:1.6px var(--orange)}
.kinetic .row .sep{color:var(--flame);padding:0 3vw}

/* ── Features grid ── */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px;margin-top:56px}
.fcard{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:30px 28px;
  transition:transform .25s,border-color .25s,box-shadow .25s;position:relative;overflow:hidden}
.fcard:hover{transform:translateY(-6px);border-color:var(--fc,var(--orange));box-shadow:0 16px 40px rgba(42,26,10,.1)}
.fcard .glow{position:absolute;width:220px;height:220px;border-radius:50%;right:-70px;top:-70px;
  background:radial-gradient(circle,color-mix(in srgb,var(--fc,var(--orange)) 20%,transparent),transparent 70%);pointer-events:none}
.fcard .fi{font-size:1.6rem;margin-bottom:16px;display:block}
.fcard h3{font-size:1.05rem;font-weight:600;margin-bottom:10px;color:var(--ink)}
.fcard p{color:var(--ink2);font-size:.88rem;line-height:1.6}
.fcard code{font-family:'JetBrains Mono';font-size:.72rem;color:var(--fc,var(--orange))}

/* ── Live feed — the "fun" section ── */
#live{background:radial-gradient(900px 520px at 82% 18%,rgba(217,138,0,.1),transparent 60%)}
.live-wrap{display:grid;grid-template-columns:1fr 1.08fr;gap:60px;align-items:center;margin-top:26px}
@media(max-width:920px){.live-wrap{grid-template-columns:1fr}}
.live-copy p{color:var(--ink2);font-size:1.02rem;line-height:1.72;margin-top:22px}
.live-copy p b{color:var(--ink)}
.feed{position:relative;border-radius:20px;padding:3px;overflow:hidden;box-shadow:0 34px 80px rgba(42,26,10,.22)}
.feed::before{content:"";position:absolute;inset:-60%;z-index:0;
  background:conic-gradient(from 0deg,#C05800,#FF6A1A,#D98A00,#9A3B00,#C05800);animation:spin 12s linear infinite}
@keyframes spin{to{transform:rotate(1turn)}}
.feed-inner{position:relative;z-index:1;background:#FFFDF6;border-radius:17px;padding:18px;min-height:380px}
.feed-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.feed-head .ttl{font-family:'JetBrains Mono';font-size:.72rem;letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);font-weight:600}
.eq{display:flex;align-items:flex-end;gap:3px;height:16px}
.eq i{width:3px;background:var(--orange);border-radius:2px;animation:eq 1s ease-in-out infinite}
.eq i:nth-child(2){animation-delay:.15s;background:var(--flame)}
.eq i:nth-child(3){animation-delay:.3s;background:var(--gold)}
.eq i:nth-child(4){animation-delay:.45s;background:var(--flame)}
.eq i:nth-child(5){animation-delay:.6s;background:var(--orange)}
@keyframes eq{0%,100%{height:5px}50%{height:16px}}
.fmsg{display:flex;gap:11px;padding:10px 12px;border-radius:12px;background:var(--bg);
  border:1px solid var(--line);margin-bottom:9px;opacity:0;transform:translateY(14px) scale(.98)}
.fmsg .av{flex:none;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-family:'JetBrains Mono';font-weight:600;font-size:.72rem;color:#FFFDF6;background:var(--mc,var(--orange));
  box-shadow:0 3px 10px color-mix(in srgb,var(--mc,var(--orange)) 50%,transparent)}
.fmsg .body{flex:1;min-width:0}
.fmsg .route{font-size:.76rem;font-weight:600;color:var(--ink)}
.fmsg .route b{color:var(--mc,var(--orange))}
.fmsg .route .arr{color:var(--muted);margin:0 5px}
.fmsg .payload{font-family:'JetBrains Mono';font-size:.72rem;color:var(--ink2);margin-top:3px;line-height:1.5}
.feed-typing{display:flex;align-items:center;gap:8px;font-family:'JetBrains Mono';font-size:.72rem;
  color:var(--muted);padding:6px 12px}
.feed-typing .tdots{display:flex;gap:4px}
.feed-typing .tdots i{width:6px;height:6px;border-radius:50%;background:var(--orange);animation:td 1.2s ease-in-out infinite}
.feed-typing .tdots i:nth-child(2){animation-delay:.2s}
.feed-typing .tdots i:nth-child(3){animation-delay:.4s}
@keyframes td{0%,100%{opacity:.3;transform:translateY(0)}50%{opacity:1;transform:translateY(-3px)}}

/* ── Stack strip ── */
.stack{display:flex;flex-wrap:wrap;gap:14px;margin-top:46px}
.stack span{font-family:'JetBrains Mono';font-size:.78rem;border:1px solid var(--line);border-radius:10px;
  padding:10px 18px;color:var(--ink2);background:var(--card);transition:.2s}
.stack span:hover{color:var(--orange);border-color:var(--orange);transform:translateY(-3px)}

/* ── CTA ── */
#cta{text-align:center;padding:150px 6vw 110px;
  background:radial-gradient(820px 440px at 50% 100%,rgba(255,106,26,.18),transparent 70%)}
#cta h2{font-family:'Unbounded';font-weight:800;font-size:clamp(2.2rem,5.8vw,4.6rem);line-height:1.08;color:var(--ink)}
#cta h2 .hl{background:var(--grad);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
#cta p{color:var(--ink2);margin:22px auto 42px;max-width:520px;font-size:1.06rem}
footer{border-top:1px solid var(--line);padding:26px 5vw;display:flex;justify-content:space-between;
  flex-wrap:wrap;gap:12px;color:var(--muted);font-size:.8rem;background:var(--bg2)}
footer a:hover{color:var(--orange)}

.reveal{opacity:0;transform:translateY(40px)}
@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important;transition:none!important}
  .reveal,.fmsg{opacity:1!important;transform:none!important}
}
</style>

<canvas id="mascot"></canvas>

<nav class="layer">
  <div class="nav-logo"><span class="dot"></span>AGENT‑AWARE</div>
  <div class="nav-links">
    <a href="#pipeline">How it works</a><a href="#features">Engine</a><a href="#live">Live feed</a>
  </div>
  <a class="btn btn-primary" data-launch href="http://localhost:5173/app" target="_top">Launch app ↗</a>
</nav>

<section class="hero layer">
  <div class="hero-inner">
    <div class="eyebrow">MULTI‑AGENT SEARCH · <b>LANGGRAPH × BROWSER‑USE</b></div>
    <h1 class="hero-title" id="heroTitle">One question. Nine agents. Every platform.</h1>
    <p class="hero-sub">Ask in plain English — <b>“cheapest flight Bangalore → Delhi next Friday”</b> — and watch a
      swarm of AI agents fan out across the web, argue over the results, self‑heal when a site blocks them,
      and hand you the <b>provably best pick</b>.</p>
    <div class="hero-cta">
      <a class="btn btn-primary" data-launch href="http://localhost:5173/app" target="_top">Try a live search ↗</a>
      <a class="btn btn-ghost" href="#pipeline">See how it thinks ↓</a>
    </div>
    <div class="hero-agents">
      <span class="agent-pill"><i style="background:var(--orange)"></i>intent</span>
      <span class="agent-pill"><i style="background:var(--flame)"></i>coordinator</span>
      <span class="agent-pill"><i style="background:var(--gold)"></i>browser‑use</span>
      <span class="agent-pill"><i style="background:var(--rust)"></i>validator</span>
      <span class="agent-pill"><i style="background:var(--choco)"></i>monitor</span>
    </div>
  </div>
</section>

<div class="marquee layer"><div class="marquee-track">
  <span>FLIGHTS <em>✦</em> HOTELS <em>✦</em> TRAINS <em>✦</em> BUSES <em>✦</em> PRODUCTS <em>✦</em> EVENTS <em>✦</em> RESTAURANTS <em>✦</em> CARS <em>✦</em></span>
  <span>FLIGHTS <em>✦</em> HOTELS <em>✦</em> TRAINS <em>✦</em> BUSES <em>✦</em> PRODUCTS <em>✦</em> EVENTS <em>✦</em> RESTAURANTS <em>✦</em> CARS <em>✦</em></span>
</div></div>

<section id="pipeline" class="sec-pad layer">
  <div class="sec-eyebrow reveal">01 — THE PIPELINE</div>
  <h2 class="sec-title reveal">A relay race of specialists, <span class="hl">not one giant prompt.</span></h2>
  <div class="timeline">
    <div class="tl-line"><div class="tl-fill" id="tlFill"></div></div>

    <div class="tl-step reveal" style="--pc:var(--orange)">
      <div class="tl-node">01</div>
      <div class="tl-card">
        <div class="tl-tag">Intent agent</div>
        <h3>Understand the ask</h3>
        <p>Classifies the category — flight, hotel, gadget, gig — extracts dates, routes, budgets, and plans exactly which platforms are worth hitting.</p>
        <span class="stack-note">LLM router · Groq → Gemini → Cerebras</span>
      </div>
    </div>

    <div class="tl-step reveal" style="--pc:var(--flame)">
      <div class="tl-node">02</div>
      <div class="tl-card">
        <div class="tl-tag">Search coordinator</div>
        <h3>Fan out in parallel</h3>
        <p>Dispatches one agent per platform simultaneously. Flights take the fast lane (SerpApi, ~1s real fares); everything else cascades Tavily → deep‑link → live browser.</p>
        <span class="stack-note">parallel dispatch · per‑platform agents</span>
      </div>
    </div>

    <div class="tl-step reveal" style="--pc:var(--gold)">
      <div class="tl-node">03</div>
      <div class="tl-card">
        <div class="tl-tag">Browser‑use agents</div>
        <h3>Drive real Chrome</h3>
        <p>Where no API exists, an LLM literally pilots a browser — clicking, scrolling, reading prices off the live page. You watch its screen stream into the app.</p>
        <span class="stack-note">Playwright · Cerebras 1M tok/day · Ollama fallback</span>
      </div>
    </div>

    <div class="tl-step reveal" style="--pc:var(--rust)">
      <div class="tl-node">04</div>
      <div class="tl-card">
        <div class="tl-tag">Validate ⟲ remediate</div>
        <h3>Critic checks the actor</h3>
        <p>A validation agent audits coverage and groundedness, recomputes the true cheapest from raw data, and re‑searches thin platforms — up to 3 self‑healing rounds.</p>
        <span class="stack-note">critic + actor loop · bounded retries</span>
      </div>
    </div>

    <div class="tl-step reveal" style="--pc:var(--choco)">
      <div class="tl-node">05</div>
      <div class="tl-card">
        <div class="tl-tag">Recommend</div>
        <h3>Best pick, with receipts</h3>
        <p>Like‑for‑like comparison across platforms, grouped and ranked, with a justification grounded in real scraped evidence — never fabricated.</p>
        <span class="stack-note">grounded · transparent · yours to verify</span>
      </div>
    </div>
  </div>
</section>

<div class="kinetic layer">
  <div class="row" id="kin1">SEARCH EVERYTHING <span class="sep">✦</span> SEARCH EVERYTHING <span class="sep">✦</span> SEARCH EVERYTHING</div>
  <div class="row outline" id="kin2">EVERYWHERE AT ONCE <span class="sep">✦</span> EVERYWHERE AT ONCE <span class="sep">✦</span> EVERYWHERE AT ONCE</div>
</div>

<section id="features" class="sec-pad layer">
  <div class="sec-eyebrow reveal">02 — THE ENGINE ROOM</div>
  <h2 class="sec-title reveal">Built like a system, <span class="hl">not a demo.</span></h2>
  <div class="grid">
    <div class="fcard reveal" style="--fc:var(--orange)"><span class="glow"></span><span class="fi">🧭</span>
      <h3>Multi‑provider LLM router</h3>
      <p>Every agent is pinned to a provider:model and auto‑fails‑over down the chain when a free tier rate‑limits. No single quota can kill a run. <code>backend/llm.py</code></p></div>
    <div class="fcard reveal" style="--fc:var(--flame)"><span class="glow"></span><span class="fi">🧠</span>
      <h3>RAG search cache</h3>
      <p>ChromaDB embedding match on past searches — a hit skips Tavily, the parse LLM call, and the whole browser run. Cuts wall‑clock and tokens at once. <code>memory/search_cache.py</code></p></div>
    <div class="fcard reveal" style="--fc:var(--gold)"><span class="glow"></span><span class="fi">🖥️</span>
      <h3>Live computer‑use stage</h3>
      <p>The agent’s Chrome screen streams into the app while it works — per‑platform tabs, fresh profiles, up to N sites driven in parallel.</p></div>
    <div class="fcard reveal" style="--fc:var(--rust)"><span class="glow"></span><span class="fi">🩹</span>
      <h3>Self‑healing loop</h3>
      <p>Validate → remediate cycle re‑checks coverage, recomputes the real cheapest option, and re‑searches empty platforms. Bounded to 3 rounds, never infinite.</p></div>
    <div class="fcard reveal" style="--fc:var(--choco)"><span class="glow"></span><span class="fi">🛰️</span>
      <h3>Agent communication bus</h3>
      <p>Every inter‑agent message — payloads included — streams to a live feed. The reasoning is a glass box, not a black box.</p></div>
    <div class="fcard reveal" style="--fc:var(--orange)"><span class="glow"></span><span class="fi">🚨</span>
      <h3>Monitor / supervisor agent</h3>
      <p>When a tab hits a CAPTCHA or bot‑block, it diagnoses the cause and proposes a concrete fix you can run with one click.</p></div>
  </div>
</section>

<section id="live" class="sec-pad layer">
  <div class="sec-eyebrow reveal">03 — GLASS BOX</div>
  <h2 class="sec-title reveal">Watch the agents <span class="hl">talk to each other.</span></h2>
  <div class="live-wrap">
    <div class="live-copy reveal">
      <p>Most AI tools hand you an answer and ask for faith. Agent‑Aware streams the <b>actual messages</b> —
      the intent agent’s plan, the params dispatched to every website agent, the results they send back,
      the validator’s verdict, and the monitor’s diagnosis when something breaks.</p>
      <p>Expand any message and read the <b>exact payload</b>. If the recommendation is wrong, you can see precisely which agent to blame.</p>
    </div>
    <div class="feed"><div class="feed-inner">
      <div class="feed-head">
        <span class="ttl">🛰 Agent communication</span>
        <span class="eq"><i></i><i></i><i></i><i></i><i></i></span>
      </div>
      <div id="feedMsgs">
        <div class="fmsg" style="--mc:var(--orange)"><div class="av">IN</div><div class="body">
          <div class="route"><b>intent</b><span class="arr">→</span><b>coordinator</b></div>
          <div class="payload">category=flight · BLR→DEL · fri · plan:[skyscanner, cleartrip, ixigo]</div></div></div>
        <div class="fmsg" style="--mc:var(--flame)"><div class="av">CO</div><div class="body">
          <div class="route"><b>coordinator</b><span class="arr">→</span><b>skyscanner</b></div>
          <div class="payload">dispatch {from:"BLR", to:"DEL", date:"2026‑07‑24"}</div></div></div>
        <div class="fmsg" style="--mc:var(--gold)"><div class="av">BU</div><div class="body">
          <div class="route"><b>browser‑use</b><span class="arr">→</span><b>coordinator</b></div>
          <div class="payload">cleartrip: 14 fares scraped · min ₹3,214 IndiGo 06:10</div></div></div>
        <div class="fmsg" style="--mc:var(--rust)"><div class="av">MO</div><div class="body">
          <div class="route"><b>monitor</b><span class="arr">→</span><b>coordinator</b></div>
          <div class="payload">ixigo blocked: CAPTCHA → retry w/ fresh profile</div></div></div>
        <div class="fmsg" style="--mc:var(--choco)"><div class="av">VA</div><div class="body">
          <div class="route"><b>validator</b><span class="arr">→</span><b>recommender</b></div>
          <div class="payload">coverage 3/3 ✓ grounded ✓ cheapest re‑verified: ₹3,214</div></div></div>
        <div class="fmsg" style="--mc:var(--orange)"><div class="av">✦</div><div class="body">
          <div class="route"><b>recommender</b><span class="arr">→</span><b>you</b></div>
          <div class="payload">Best pick: IndiGo 6E‑204 · ₹3,214 · proof attached</div></div></div>
      </div>
      <div class="feed-typing" id="feedTyping"><span class="tdots"><i></i><i></i><i></i></span> agents working…</div>
    </div></div>
  </div>
  <div class="sec-eyebrow" style="margin-top:90px">STACK</div>
  <div class="stack reveal">
    <span>LangGraph</span><span>Streamlit</span><span>browser‑use</span><span>Playwright</span><span>Groq</span>
    <span>Gemini</span><span>Cerebras</span><span>Ollama</span><span>ChromaDB</span><span>SerpApi</span><span>Tavily</span><span>Langfuse</span>
  </div>
</section>

<section id="cta" class="layer">
  <h2 class="reveal">Ask it <span class="hl">anything.</span></h2>
  <p class="reveal">Flights, hotels, gadgets, gigs — one query, every platform, agents you can watch think.</p>
  <a class="btn btn-primary reveal" style="font-size:1.05rem;padding:16px 38px" data-launch href="http://localhost:5173/app" target="_top">Launch Agent‑Aware ↗</a>
</section>

<footer class="layer">
  <span>Agent‑Aware — a learning / portfolio build. Real‑time scraping is fragile by nature; see Known Issues in the README.</span>
  <a href="https://github.com/vedhakoushik" target="_blank" rel="noopener">github.com/vedhakoushik ↗</a>
</footer>

<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js"></script>
<script>
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

/* ─────────────────────────────────────────────────────────────────────────
   THE MASCOT — our "fox". A rotating multi-agent orb: a bright AI core with
   satellite agents on a sphere. It rotates as you scroll and, once the hero
   scrolls away, shrinks and docks to the corner so it leads you down the page
   (MetaMask-fox behaviour, adapted to a swarm of agents). Pure canvas, warm
   palette, never blocks clicks.
   ───────────────────────────────────────────────────────────────────────── */
(function(){
  const cv=document.getElementById('mascot'),ctx=cv.getContext('2d');
  const AGENTS=['#C05800','#FF6A1A','#D98A00','#9A3B00','#713600','#E8630A'];
  const N=15, PTS=[];
  for(let i=0;i<N;i++){                       // fibonacci sphere
    const y=1-(i/(N-1))*2, r=Math.sqrt(1-y*y), th=i*2.399963;
    PTS.push({x:Math.cos(th)*r,y:y,z:Math.sin(th)*r,c:AGENTS[i%AGENTS.length]});
  }
  const LINKS=[];                              // connect near neighbours
  for(let i=0;i<N;i++)for(let j=i+1;j<N;j++){
    const dx=PTS[i].x-PTS[j].x,dy=PTS[i].y-PTS[j].y,dz=PTS[i].z-PTS[j].z;
    if(dx*dx+dy*dy+dz*dz<0.9)LINKS.push([i,j]);
  }
  let W,H,mx=0,my=0,scrollY=0,heroH=1;
  function size(){const d=devicePixelRatio||1;W=cv.width=innerWidth*d;H=cv.height=innerHeight*d;ctx.setTransform(d,0,0,d,0,0);
    const hero=document.querySelector('.hero');heroH=hero?hero.offsetHeight:innerHeight;}
  size();addEventListener('resize',size);
  addEventListener('scroll',()=>{scrollY=window.scrollY||window.pageYOffset;},{passive:true});
  addEventListener('mousemove',e=>{mx=(e.clientX/innerWidth-.5);my=(e.clientY/innerHeight-.5);});
  const ease=t=>1-Math.pow(1-t,3);
  const lerp=(a,b,t)=>a+(b-a)*t;
  let ex=0,ey=0;                               // eased cursor follow

  function draw(t){
    const w=innerWidth,h=innerHeight;
    ctx.clearRect(0,0,W,H);
    const p=Math.min(scrollY/Math.max(heroH*0.85,1),1), e=ease(p);
    // hero: large, on the right so it clears the centred headline. After the hero
    // scrolls away: shrinks and docks to the bottom-right corner, floating over
    // the page like a companion — always spinning, always visible.
    const cx=lerp(w*0.78, w*0.90, e);
    const cy=lerp(h*0.50, h*0.85, e);
    const R =lerp(Math.min(w,h)*0.17, Math.min(w,h)*0.072, e);
    const op=lerp(0.7, 0.96, e);
    ex+=((mx*0.5)-ex)*0.06; ey+=((my*0.5)-ey)*0.06;
    const ry=(reduced?0:scrollY*0.0022 + t*0.00018) + ex*1.4;
    const rx=0.35 + ey*1.1;
    const ca=Math.cos(ry),sa=Math.sin(ry),cb=Math.cos(rx),sb=Math.sin(rx);
    const proj=PTS.map(pt=>{
      let x=pt.x*ca+pt.z*sa, z=-pt.x*sa+pt.z*ca, y=pt.y;
      let y2=y*cb-z*sb, z2=y*sb+z*cb;
      const persp=1.9/(1.9 - z2);              // depth → scale
      return {sx:cx+x*R*persp, sy:cy+y2*R*persp, depth:(z2+1)/2, sc:persp, c:pt.c};
    });
    // core glow
    const g=ctx.createRadialGradient(cx,cy,0,cx,cy,R*0.9);
    g.addColorStop(0,`rgba(255,106,26,${0.42*op})`);g.addColorStop(1,'rgba(255,106,26,0)');
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,R*0.9,0,7);ctx.fill();
    // links
    LINKS.forEach(([i,j])=>{
      const a=proj[i],b=proj[j];
      ctx.strokeStyle=`rgba(113,54,0,${0.10*op*(a.depth+b.depth)})`;
      ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(a.sx,a.sy);ctx.lineTo(b.sx,b.sy);ctx.stroke();
    });
    // nodes back-to-front
    proj.slice().sort((a,b)=>a.depth-b.depth).forEach(nd=>{
      const rad=(2.2+nd.sc*2.6)*(reduced?1:1);
      ctx.globalAlpha=(0.35+nd.depth*0.65)*op;
      ctx.shadowColor=nd.c;ctx.shadowBlur=14*nd.depth;
      ctx.fillStyle=nd.c;ctx.beginPath();ctx.arc(nd.sx,nd.sy,rad,0,7);ctx.fill();
    });
    ctx.globalAlpha=1;ctx.shadowBlur=0;
    // bright center dot (the "mind")
    ctx.fillStyle=`rgba(255,253,246,${op})`;ctx.beginPath();ctx.arc(cx,cy,3.2+ (1-e)*2,0,7);ctx.fill();
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
})();

/* ── Scroll-driven UI (GSAP) ───────────────────────────────────────────── */
if(!reduced && window.gsap){
  gsap.registerPlugin(ScrollTrigger);

  // hero headline: char stagger, highlight middle clause
  const hEl=document.getElementById('heroTitle');
  const words=hEl.textContent.trim().split(' ');
  hEl.innerHTML=words.map((w,i)=>{
    const hl=(i>=2 && i<=3)?' hl':'';        // "Nine agents."
    return '<span class="word'+hl+'">'+[...w].map(c=>'<span class="ch">'+c+'</span>').join('')+'</span>';
  }).join(' ');
  gsap.from('#heroTitle .ch',{yPercent:115,opacity:0,rotateX:-55,stagger:.02,duration:.85,ease:'back.out(1.5)',delay:.15});
  gsap.from('.eyebrow,.hero-sub,.hero-cta,.hero-agents',{y:24,opacity:0,stagger:.1,duration:.75,ease:'power3.out',delay:.5});

  // pipeline timeline: line fills as you scroll through the steps
  gsap.to('#tlFill',{height:'100%',ease:'none',scrollTrigger:{
    trigger:'.timeline',start:'top 60%',end:'bottom 75%',scrub:1}});

  // kinetic band: counter-drift + velocity skew
  gsap.to('#kin1',{xPercent:-16,ease:'none',scrollTrigger:{trigger:'.kinetic',start:'top bottom',end:'bottom top',scrub:1}});
  gsap.to('#kin2',{xPercent:12,ease:'none',scrollTrigger:{trigger:'.kinetic',start:'top bottom',end:'bottom top',scrub:1}});
  const skew=gsap.quickTo('.kinetic .row','skewX',{duration:.4,ease:'power2.out'});
  ScrollTrigger.create({onUpdate:s=>skew(gsap.utils.clamp(-7,7,s.getVelocity()/-280))});

  // generic reveals
  gsap.utils.toArray('.reveal').forEach(el=>gsap.to(el,{y:0,opacity:1,duration:.85,ease:'power3.out',
    scrollTrigger:{trigger:el,start:'top 88%'}}));

  // live feed: messages spring in, loop forever once section is in view
  const msgs=gsap.utils.toArray('#feedMsgs .fmsg');
  const typing=document.getElementById('feedTyping');
  const tl=gsap.timeline({repeat:-1,repeatDelay:1.6,scrollTrigger:{trigger:'#live',start:'top 72%'}});
  tl.set(typing,{opacity:1})
    .to(msgs,{opacity:1,y:0,scale:1,duration:.5,ease:'back.out(1.4)',stagger:.75})
    .to(typing,{opacity:0,duration:.3},'-=.3')
    .to(msgs,{opacity:0,y:-12,duration:.4,stagger:.06},'+=2.4')
    .set(typing,{opacity:1});
}else{
  document.querySelectorAll('.reveal,.fmsg').forEach(e=>{e.style.opacity=1;e.style.transform='none'});
  const f=document.getElementById('tlFill');if(f)f.style.height='100%';
}

/* Enter the app. A plain target="_top" link can be swallowed by the component
   iframe's sandbox, so drive the top-level navigation from script (with fallbacks)
   → main() reads ?app=1 and hands over to the backend-connected search UI. */
document.querySelectorAll('a[data-launch]').forEach(a=>{
  a.addEventListener('click',ev=>{
    ev.preventDefault();
    const url='http://localhost:5173/app';
    try{ if(window.top && window.top!==window.self){ window.top.location.href=url; return; } }catch(_){}
    try{ window.parent.location.href=url; return; }catch(_){}
    window.open(url,'_blank');
  });
});
</script>
"""


def render_landing() -> None:
    """Full-viewport landing page. Kills Streamlit chrome/padding, stretches the
    component iframe to the viewport, and scrolls inside it (ScrollTrigger + the
    mascot both read the iframe's own scroll)."""
    st.markdown("""
    <style>
      .stApp { background:#FBF6E9 !important; }
      .block-container { padding:0 !important; max-width:100% !important; }
      div[data-testid="stVerticalBlock"] { gap:0 !important; }
      iframe[title="st.iframe"], div[data-testid="stIFrame"] iframe {
        height:100vh !important; width:100% !important; display:block; border:none;
      }
    </style>
    """, unsafe_allow_html=True)
    components.html(LANDING_HTML, height=900, scrolling=True)
