import { useEffect, useRef } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "./landing.css";

gsap.registerPlugin(ScrollTrigger);

/**
 * Marketing landing at "/" — warm brand identity, GSAP scroll story, and the
 * scroll-reactive multi-agent orb mascot. "Launch app" navigates to /app (the
 * chat UI) with a plain link, so the whole product lives on one origin.
 * Ported from the Streamlit-embedded frontend/landing.py.
 */
export default function Landing() {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const cleanups: (() => void)[] = [];

    /* ── mascot: rotating multi-agent orb, docks bottom-right on scroll ── */
    const cv = document.getElementById("mascot") as HTMLCanvasElement | null;
    if (cv) {
      const ctx = cv.getContext("2d")!;
      const AGENTS = ["#C05800", "#FF6A1A", "#D98A00", "#9A3B00", "#713600", "#E8630A"];
      const N = 15;
      const PTS: { x: number; y: number; z: number; c: string }[] = [];
      for (let i = 0; i < N; i++) {
        const y = 1 - (i / (N - 1)) * 2;
        const r = Math.sqrt(1 - y * y);
        const th = i * 2.399963;
        PTS.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, c: AGENTS[i % AGENTS.length] });
      }
      const LINKS: [number, number][] = [];
      for (let i = 0; i < N; i++)
        for (let j = i + 1; j < N; j++) {
          const dx = PTS[i].x - PTS[j].x,
            dy = PTS[i].y - PTS[j].y,
            dz = PTS[i].z - PTS[j].z;
          if (dx * dx + dy * dy + dz * dz < 0.9) LINKS.push([i, j]);
        }
      let W = 0,
        H = 0,
        mx = 0,
        my = 0,
        scrollY = 0,
        heroH = 1,
        raf = 0;
      const size = () => {
        const d = devicePixelRatio || 1;
        W = cv.width = innerWidth * d;
        H = cv.height = innerHeight * d;
        ctx.setTransform(d, 0, 0, d, 0, 0);
        const hero = rootRef.current?.querySelector(".hero") as HTMLElement | null;
        heroH = hero ? hero.offsetHeight : innerHeight;
      };
      const onScroll = () => {
        scrollY = window.scrollY;
      };
      const onMouse = (e: MouseEvent) => {
        mx = e.clientX / innerWidth - 0.5;
        my = e.clientY / innerHeight - 0.5;
      };
      size();
      addEventListener("resize", size);
      addEventListener("scroll", onScroll, { passive: true });
      addEventListener("mousemove", onMouse);
      cleanups.push(() => {
        removeEventListener("resize", size);
        removeEventListener("scroll", onScroll);
        removeEventListener("mousemove", onMouse);
        cancelAnimationFrame(raf);
      });
      const ease = (t: number) => 1 - Math.pow(1 - t, 3);
      const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
      let ex = 0,
        ey = 0;
      const draw = (t: number) => {
        const w = innerWidth,
          h = innerHeight;
        ctx.clearRect(0, 0, W, H);
        const p = Math.min(scrollY / Math.max(heroH * 0.85, 1), 1);
        const e = ease(p);
        const cx = lerp(w * 0.78, w * 0.9, e);
        const cy = lerp(h * 0.5, h * 0.85, e);
        const R = lerp(Math.min(w, h) * 0.17, Math.min(w, h) * 0.072, e);
        const op = lerp(0.7, 0.96, e);
        ex += (mx * 0.5 - ex) * 0.06;
        ey += (my * 0.5 - ey) * 0.06;
        const ry = (reduced ? 0 : scrollY * 0.0022 + t * 0.00018) + ex * 1.4;
        const rx = 0.35 + ey * 1.1;
        const ca = Math.cos(ry),
          sa = Math.sin(ry),
          cb = Math.cos(rx),
          sb = Math.sin(rx);
        const proj = PTS.map((pt) => {
          const x = pt.x * ca + pt.z * sa;
          const z = -pt.x * sa + pt.z * ca;
          const y2 = pt.y * cb - z * sb;
          const z2 = pt.y * sb + z * cb;
          const persp = 1.9 / (1.9 - z2);
          return { sx: cx + x * R * persp, sy: cy + y2 * R * persp, depth: (z2 + 1) / 2, sc: persp, c: pt.c };
        });
        const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.9);
        g.addColorStop(0, `rgba(255,106,26,${0.42 * op})`);
        g.addColorStop(1, "rgba(255,106,26,0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(cx, cy, R * 0.9, 0, 7);
        ctx.fill();
        LINKS.forEach(([i, j]) => {
          const a = proj[i],
            b = proj[j];
          ctx.strokeStyle = `rgba(113,54,0,${0.1 * op * (a.depth + b.depth)})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(a.sx, a.sy);
          ctx.lineTo(b.sx, b.sy);
          ctx.stroke();
        });
        proj
          .slice()
          .sort((a, b) => a.depth - b.depth)
          .forEach((nd) => {
            const rad = 2.2 + nd.sc * 2.6;
            ctx.globalAlpha = (0.35 + nd.depth * 0.65) * op;
            ctx.shadowColor = nd.c;
            ctx.shadowBlur = 14 * nd.depth;
            ctx.fillStyle = nd.c;
            ctx.beginPath();
            ctx.arc(nd.sx, nd.sy, rad, 0, 7);
            ctx.fill();
          });
        ctx.globalAlpha = 1;
        ctx.shadowBlur = 0;
        ctx.fillStyle = `rgba(255,253,246,${op})`;
        ctx.beginPath();
        ctx.arc(cx, cy, 3.2 + (1 - e) * 2, 0, 7);
        ctx.fill();
        raf = requestAnimationFrame(draw);
      };
      raf = requestAnimationFrame(draw);
    }

    /* ── GSAP scroll story ── */
    if (!reduced) {
      const hEl = document.getElementById("heroTitle");
      if (hEl && !hEl.dataset.split) {
        hEl.dataset.split = "1";
        const words = (hEl.textContent ?? "").trim().split(" ");
        hEl.innerHTML = words
          .map((w, i) => {
            const hl = i >= 2 && i <= 3 ? " hl" : "";
            return (
              `<span class="word${hl}">` +
              [...w].map((c) => `<span class="ch">${c}</span>`).join("") +
              "</span>"
            );
          })
          .join(" ");
      }
      gsap.from("#heroTitle .ch", {
        yPercent: 115, opacity: 0, rotateX: -55, stagger: 0.02,
        duration: 0.85, ease: "back.out(1.5)", delay: 0.15,
      });
      gsap.from(".landing .eyebrow,.landing .hero-sub,.landing .hero-cta,.landing .hero-agents", {
        y: 24, opacity: 0, stagger: 0.1, duration: 0.75, ease: "power3.out", delay: 0.5,
      });
      gsap.to("#tlFill", {
        height: "100%", ease: "none",
        scrollTrigger: { trigger: ".landing .timeline", start: "top 60%", end: "bottom 75%", scrub: 1 },
      });
      gsap.to("#kin1", {
        xPercent: -16, ease: "none",
        scrollTrigger: { trigger: ".landing .kinetic", start: "top bottom", end: "bottom top", scrub: 1 },
      });
      gsap.to("#kin2", {
        xPercent: 12, ease: "none",
        scrollTrigger: { trigger: ".landing .kinetic", start: "top bottom", end: "bottom top", scrub: 1 },
      });
      const skew = gsap.quickTo(".landing .kinetic .row", "skewX", { duration: 0.4, ease: "power2.out" });
      ScrollTrigger.create({
        onUpdate: (s) => skew(gsap.utils.clamp(-7, 7, s.getVelocity() / -280)),
      });
      gsap.utils.toArray<Element>(".landing .reveal").forEach((el) =>
        gsap.to(el, {
          y: 0, opacity: 1, duration: 0.85, ease: "power3.out",
          scrollTrigger: { trigger: el, start: "top 88%" },
        }),
      );
      const msgs = gsap.utils.toArray<Element>("#feedMsgs .fmsg");
      const typing = document.getElementById("feedTyping");
      const tl = gsap.timeline({
        repeat: -1, repeatDelay: 1.6,
        scrollTrigger: { trigger: "#live", start: "top 72%" },
      });
      tl.set(typing, { opacity: 1 })
        .to(msgs, { opacity: 1, y: 0, scale: 1, duration: 0.5, ease: "back.out(1.4)", stagger: 0.75 })
        .to(typing, { opacity: 0, duration: 0.3 }, "-=.3")
        .to(msgs, { opacity: 0, y: -12, duration: 0.4, stagger: 0.06 }, "+=2.4")
        .set(typing, { opacity: 1 });
      cleanups.push(() => {
        ScrollTrigger.getAll().forEach((t) => t.kill());
        tl.kill();
      });
    } else {
      document.querySelectorAll<HTMLElement>(".landing .reveal,.landing .fmsg").forEach((e) => {
        e.style.opacity = "1";
        e.style.transform = "none";
      });
      const f = document.getElementById("tlFill");
      if (f) f.style.height = "100%";
    }

    return () => cleanups.forEach((fn) => fn());
  }, []);

  return (
    <div className="landing" ref={rootRef}>
      <canvas id="mascot" />

      <nav className="layer">
        <div className="nav-logo">
          <span className="dot" />
          AGENT-AWARE
        </div>
        <div className="nav-links">
          <a href="#pipeline">How it works</a>
          <a href="#features">Engine</a>
          <a href="#live">Live feed</a>
        </div>
        <a className="btn btn-primary" href="/app">
          Launch app ↗
        </a>
      </nav>

      <section className="hero layer">
        <div className="hero-inner">
          <div className="eyebrow">
            MULTI-AGENT SEARCH · <b>LANGGRAPH × BROWSER-USE</b>
          </div>
          <h1 className="hero-title" id="heroTitle">
            One question. Nine agents. Every platform.
          </h1>
          <p className="hero-sub">
            Ask in plain English — <b>“cheapest flight Bangalore → Delhi next Friday”</b> — and
            watch a swarm of AI agents fan out across the web, argue over the results, self-heal
            when a site blocks them, and hand you the <b>provably best pick</b>.
          </p>
          <div className="hero-cta">
            <a className="btn btn-primary" href="/app">
              Try a live search ↗
            </a>
            <a className="btn btn-ghost" href="#pipeline">
              See how it thinks ↓
            </a>
          </div>
          <div className="hero-agents">
            <span className="agent-pill"><i style={{ background: "var(--orange)" }} />intent</span>
            <span className="agent-pill"><i style={{ background: "var(--flame)" }} />coordinator</span>
            <span className="agent-pill"><i style={{ background: "var(--gold)" }} />browser-use</span>
            <span className="agent-pill"><i style={{ background: "var(--rust)" }} />validator</span>
            <span className="agent-pill"><i style={{ background: "var(--choco)" }} />monitor</span>
          </div>
        </div>
      </section>

      <div className="marquee layer">
        <div className="marquee-track">
          <span>FLIGHTS <em>✦</em> HOTELS <em>✦</em> TRAINS <em>✦</em> BUSES <em>✦</em> PRODUCTS <em>✦</em> EVENTS <em>✦</em> RESTAURANTS <em>✦</em> CARS <em>✦</em></span>
          <span>FLIGHTS <em>✦</em> HOTELS <em>✦</em> TRAINS <em>✦</em> BUSES <em>✦</em> PRODUCTS <em>✦</em> EVENTS <em>✦</em> RESTAURANTS <em>✦</em> CARS <em>✦</em></span>
        </div>
      </div>

      <section id="pipeline" className="sec-pad layer">
        <div className="sec-eyebrow reveal">01 — THE PIPELINE</div>
        <h2 className="sec-title reveal">
          A relay race of specialists, <span className="hl">not one giant prompt.</span>
        </h2>
        <div className="timeline">
          <div className="tl-line"><div className="tl-fill" id="tlFill" /></div>

          <div className="tl-step reveal" style={{ "--pc": "var(--orange)" } as React.CSSProperties}>
            <div className="tl-node">01</div>
            <div className="tl-card">
              <div className="tl-tag">Intent agent</div>
              <h3>Understand the ask</h3>
              <p>Classifies the category — flight, hotel, gadget, gig — extracts dates, routes, budgets, and plans exactly which platforms are worth hitting.</p>
              <span className="stack-note">LLM router · Groq → Gemini → Cerebras</span>
            </div>
          </div>

          <div className="tl-step reveal" style={{ "--pc": "var(--flame)" } as React.CSSProperties}>
            <div className="tl-node">02</div>
            <div className="tl-card">
              <div className="tl-tag">Search coordinator</div>
              <h3>Fan out in parallel</h3>
              <p>Dispatches one agent per platform simultaneously. Flights take the fast lane (SerpApi, ~1s real fares); everything else cascades Tavily → deep-link → live browser.</p>
              <span className="stack-note">parallel dispatch · per-platform agents</span>
            </div>
          </div>

          <div className="tl-step reveal" style={{ "--pc": "var(--gold)" } as React.CSSProperties}>
            <div className="tl-node">03</div>
            <div className="tl-card">
              <div className="tl-tag">Browser-use agents</div>
              <h3>Drive real Chrome</h3>
              <p>Where no API exists, an LLM literally pilots a browser — clicking, scrolling, reading prices off the live page. You watch its screen stream into the app.</p>
              <span className="stack-note">Playwright · Cerebras 1M tok/day · Ollama fallback</span>
            </div>
          </div>

          <div className="tl-step reveal" style={{ "--pc": "var(--rust)" } as React.CSSProperties}>
            <div className="tl-node">04</div>
            <div className="tl-card">
              <div className="tl-tag">Validate ⟲ remediate</div>
              <h3>Critic checks the actor</h3>
              <p>A validation agent audits coverage and groundedness, recomputes the true cheapest from raw data, and re-searches thin platforms — up to 3 self-healing rounds.</p>
              <span className="stack-note">critic + actor loop · bounded retries</span>
            </div>
          </div>

          <div className="tl-step reveal" style={{ "--pc": "var(--choco)" } as React.CSSProperties}>
            <div className="tl-node">05</div>
            <div className="tl-card">
              <div className="tl-tag">Recommend</div>
              <h3>Best pick, with receipts</h3>
              <p>Like-for-like comparison across platforms, grouped and ranked, with a justification grounded in real scraped evidence — never fabricated.</p>
              <span className="stack-note">grounded · transparent · yours to verify</span>
            </div>
          </div>
        </div>
      </section>

      <div className="kinetic layer">
        <div className="row" id="kin1">
          SEARCH EVERYTHING <span className="sep">✦</span> SEARCH EVERYTHING <span className="sep">✦</span> SEARCH EVERYTHING
        </div>
        <div className="row outline" id="kin2">
          EVERYWHERE AT ONCE <span className="sep">✦</span> EVERYWHERE AT ONCE <span className="sep">✦</span> EVERYWHERE AT ONCE
        </div>
      </div>

      <section id="features" className="sec-pad layer">
        <div className="sec-eyebrow reveal">02 — THE ENGINE ROOM</div>
        <h2 className="sec-title reveal">
          Built like a system, <span className="hl">not a demo.</span>
        </h2>
        <div className="grid">
          <div className="fcard reveal" style={{ "--fc": "var(--orange)" } as React.CSSProperties}>
            <span className="glow" /><span className="fi">🧭</span>
            <h3>Multi-provider LLM router</h3>
            <p>Every agent is pinned to a provider:model and auto-fails-over down the chain when a free tier rate-limits. No single quota can kill a run. <code>backend/llm.py</code></p>
          </div>
          <div className="fcard reveal" style={{ "--fc": "var(--flame)" } as React.CSSProperties}>
            <span className="glow" /><span className="fi">🧠</span>
            <h3>RAG search cache</h3>
            <p>ChromaDB embedding match on past searches — a hit skips Tavily, the parse LLM call, and the whole browser run. Cuts wall-clock and tokens at once. <code>memory/search_cache.py</code></p>
          </div>
          <div className="fcard reveal" style={{ "--fc": "var(--gold)" } as React.CSSProperties}>
            <span className="glow" /><span className="fi">🖥️</span>
            <h3>Live computer-use stage</h3>
            <p>The agent’s Chrome screen streams into the app while it works — per-platform tabs, fresh profiles, up to N sites driven in parallel.</p>
          </div>
          <div className="fcard reveal" style={{ "--fc": "var(--rust)" } as React.CSSProperties}>
            <span className="glow" /><span className="fi">🩹</span>
            <h3>Self-healing loop</h3>
            <p>Validate → remediate cycle re-checks coverage, recomputes the real cheapest option, and re-searches empty platforms. Bounded to 3 rounds, never infinite.</p>
          </div>
          <div className="fcard reveal" style={{ "--fc": "var(--choco)" } as React.CSSProperties}>
            <span className="glow" /><span className="fi">🛰️</span>
            <h3>Agent communication bus</h3>
            <p>Every inter-agent message — payloads included — streams to a live feed. The reasoning is a glass box, not a black box.</p>
          </div>
          <div className="fcard reveal" style={{ "--fc": "var(--orange)" } as React.CSSProperties}>
            <span className="glow" /><span className="fi">🚨</span>
            <h3>Monitor / supervisor agent</h3>
            <p>When a tab hits a CAPTCHA or bot-block, it diagnoses the cause and proposes a concrete fix you can run with one click.</p>
          </div>
        </div>
      </section>

      <section id="live" className="sec-pad layer">
        <div className="sec-eyebrow reveal">03 — GLASS BOX</div>
        <h2 className="sec-title reveal">
          Watch the agents <span className="hl">talk to each other.</span>
        </h2>
        <div className="live-wrap">
          <div className="live-copy reveal">
            <p>Most AI tools hand you an answer and ask for faith. Agent-Aware streams the <b>actual messages</b> — the intent agent’s plan, the params dispatched to every website agent, the results they send back, the validator’s verdict, and the monitor’s diagnosis when something breaks.</p>
            <p>Expand any message and read the <b>exact payload</b>. If the recommendation is wrong, you can see precisely which agent to blame.</p>
          </div>
          <div className="feed">
            <div className="feed-inner">
              <div className="feed-head">
                <span className="ttl">🛰 Agent communication</span>
                <span className="eq"><i /><i /><i /><i /><i /></span>
              </div>
              <div id="feedMsgs">
                <div className="fmsg" style={{ "--mc": "var(--orange)" } as React.CSSProperties}>
                  <div className="av">IN</div>
                  <div className="body">
                    <div className="route"><b>intent</b><span className="arr">→</span><b>coordinator</b></div>
                    <div className="payload">category=flight · BLR→DEL · fri · plan:[skyscanner, cleartrip, ixigo]</div>
                  </div>
                </div>
                <div className="fmsg" style={{ "--mc": "var(--flame)" } as React.CSSProperties}>
                  <div className="av">CO</div>
                  <div className="body">
                    <div className="route"><b>coordinator</b><span className="arr">→</span><b>skyscanner</b></div>
                    <div className="payload">dispatch {"{"}from:"BLR", to:"DEL", date:"2026-07-24"{"}"}</div>
                  </div>
                </div>
                <div className="fmsg" style={{ "--mc": "var(--gold)" } as React.CSSProperties}>
                  <div className="av">BU</div>
                  <div className="body">
                    <div className="route"><b>browser-use</b><span className="arr">→</span><b>coordinator</b></div>
                    <div className="payload">cleartrip: 14 fares scraped · min ₹3,214 IndiGo 06:10</div>
                  </div>
                </div>
                <div className="fmsg" style={{ "--mc": "var(--rust)" } as React.CSSProperties}>
                  <div className="av">MO</div>
                  <div className="body">
                    <div className="route"><b>monitor</b><span className="arr">→</span><b>coordinator</b></div>
                    <div className="payload">ixigo blocked: CAPTCHA → retry w/ fresh profile</div>
                  </div>
                </div>
                <div className="fmsg" style={{ "--mc": "var(--choco)" } as React.CSSProperties}>
                  <div className="av">VA</div>
                  <div className="body">
                    <div className="route"><b>validator</b><span className="arr">→</span><b>recommender</b></div>
                    <div className="payload">coverage 3/3 ✓ grounded ✓ cheapest re-verified: ₹3,214</div>
                  </div>
                </div>
                <div className="fmsg" style={{ "--mc": "var(--orange)" } as React.CSSProperties}>
                  <div className="av">✦</div>
                  <div className="body">
                    <div className="route"><b>recommender</b><span className="arr">→</span><b>you</b></div>
                    <div className="payload">Best pick: IndiGo 6E-204 · ₹3,214 · proof attached</div>
                  </div>
                </div>
              </div>
              <div className="feed-typing" id="feedTyping">
                <span className="tdots"><i /><i /><i /></span> agents working…
              </div>
            </div>
          </div>
        </div>
        <div className="sec-eyebrow" style={{ marginTop: 90 }}>STACK</div>
        <div className="stack reveal">
          <span>LangGraph</span><span>FastAPI</span><span>React</span><span>browser-use</span><span>Playwright</span>
          <span>Groq</span><span>Gemini</span><span>Cerebras</span><span>Ollama</span><span>ChromaDB</span>
          <span>SerpApi</span><span>Tavily</span><span>Neo4j</span>
        </div>
      </section>

      <section id="cta" className="layer">
        <h2 className="reveal">
          Ask it <span className="hl">anything.</span>
        </h2>
        <p className="reveal">Flights, hotels, gadgets, gigs — one query, every platform, agents you can watch think.</p>
        <a className="btn btn-primary reveal" style={{ fontSize: "1.05rem", padding: "16px 38px" }} href="/app">
          Launch Agent-Aware ↗
        </a>
      </section>

      <footer className="layer">
        <span>Agent-Aware — a learning / portfolio build. Real-time scraping is fragile by nature; see Known Issues in the README.</span>
        <a href="https://github.com/vedhakoushik" target="_blank" rel="noopener">
          github.com/vedhakoushik ↗
        </a>
      </footer>
    </div>
  );
}
