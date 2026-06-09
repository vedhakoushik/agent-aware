const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, LevelFormat, TableOfContents, HeadingLevel,
  BorderStyle, WidthType, ShadingType, VerticalAlign, PageNumber, PageBreak,
  TabStopType, TabStopPosition,
} = require("docx");

// ── palette ──
const INK = "1E293B";
const MUTE = "64748B";
const ACCENT = "6366F1";
const GREEN = "059669";
const HEADBG = "EEF2FF";
const ROWBG = "F8FAFC";
const BORDER = "E2E8F0";

const CONTENT_W = 9360; // US Letter, 1" margins

const border = { style: BorderStyle.SINGLE, size: 1, color: BORDER };
const cellBorders = { top: border, bottom: border, left: border, right: border };

// ── helpers ──
function h1(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });
}
function h2(text) {
  return new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
}
function p(text, opts = {}) {
  return new Paragraph({
    spacing: { after: 120, line: 276 },
    children: [new TextRun({ text, size: 22, color: opts.color || INK, bold: !!opts.bold, italics: !!opts.italics })],
  });
}
function bullet(text, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 60, line: 270 },
    children: [new TextRun({ text, size: 22, color: INK })],
  });
}
function bulletRich(runs, level = 0) {
  return new Paragraph({
    numbering: { reference: "bullets", level },
    spacing: { after: 60, line: 270 },
    children: runs,
  });
}
function num(text) {
  return new Paragraph({
    numbering: { reference: "numbers", level: 0 },
    spacing: { after: 60, line: 270 },
    children: [new TextRun({ text, size: 22, color: INK })],
  });
}

// table builder: headers[], rows[][], colWidths[]
function buildTable(headers, rows, colWidths) {
  const headerRow = new TableRow({
    tableHeader: true,
    children: headers.map((htext, i) =>
      new TableCell({
        borders: cellBorders,
        width: { size: colWidths[i], type: WidthType.DXA },
        shading: { fill: HEADBG, type: ShadingType.CLEAR },
        margins: { top: 80, bottom: 80, left: 120, right: 120 },
        verticalAlign: VerticalAlign.CENTER,
        children: [new Paragraph({ children: [new TextRun({ text: htext, bold: true, size: 20, color: INK })] })],
      })
    ),
  });
  const bodyRows = rows.map((cells, ri) =>
    new TableRow({
      children: cells.map((c, i) =>
        new TableCell({
          borders: cellBorders,
          width: { size: colWidths[i], type: WidthType.DXA },
          shading: { fill: ri % 2 === 1 ? ROWBG : "FFFFFF", type: ShadingType.CLEAR },
          margins: { top: 70, bottom: 70, left: 120, right: 120 },
          verticalAlign: VerticalAlign.CENTER,
          children: [new Paragraph({ children: [new TextRun({ text: c, size: 20, color: INK })] })],
        })
      ),
    })
  );
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [headerRow, ...bodyRows],
  });
}

function spacer() { return new Paragraph({ spacing: { after: 60 }, children: [new TextRun("")] }); }
function rule() {
  return new Paragraph({
    spacing: { after: 160, before: 40 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: ACCENT, space: 1 } },
    children: [new TextRun("")],
  });
}

// ───────────────────────── document body ─────────────────────────
const children = [];

// Title block
children.push(new Paragraph({
  spacing: { after: 40 },
  children: [new TextRun({ text: "Agent-Aware", bold: true, size: 56, color: ACCENT })],
}));
children.push(new Paragraph({
  spacing: { after: 120 },
  children: [new TextRun({ text: "Product Requirements Document", size: 32, color: INK, bold: true })],
}));
children.push(new Paragraph({
  spacing: { after: 40 },
  children: [new TextRun({ text: "Multi-platform AI search & comparison agent", italics: true, size: 24, color: MUTE })],
}));
children.push(rule());
children.push(new Paragraph({
  spacing: { after: 30 },
  children: [
    new TextRun({ text: "Version 2.0", size: 20, color: MUTE }),
    new TextRun({ text: "\tStatus: Working Prototype", size: 20, color: MUTE }),
  ],
  tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
}));
children.push(new Paragraph({
  spacing: { after: 200 },
  children: [
    new TextRun({ text: "Owner: Bella", size: 20, color: MUTE }),
    new TextRun({ text: "\tLast updated: 2026-06-07", size: 20, color: MUTE }),
  ],
  tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
}));

// TOC
children.push(h1("Table of Contents"));
children.push(new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }));
children.push(new Paragraph({ children: [new PageBreak()] }));

// 1. Overview
children.push(h1("1. Overview"));
children.push(p("Agent-Aware is an agentic AI meta-search engine. A user types one natural-language query — for example “book a flight to Hyderabad tomorrow under ₹5000” or “budget hotels in Manali this weekend” — and the system searches multiple booking platforms simultaneously, normalizes the results, compares them on the factors that actually matter for that category, and recommends the single best option with reasoning."));
children.push(p("It replaces the manual ritual of opening five browser tabs, eyeballing prices, and mentally weighing trade-offs."));
children.push(p("One-line pitch: Search everything, everywhere, at once — AI compares so you don’t have to.", { italics: true, color: ACCENT, bold: true }));

// 2. Problem
children.push(h1("2. Problem Statement"));
children.push(p("Booking anything online today requires the user to:"));
children.push(bullet("Open and re-enter the same search across many platforms"));
children.push(bullet("Compare prices that aren’t like-for-like (a Standard Room on one site vs a Suite on another)"));
children.push(bullet("Mentally juggle price against amenities, ratings, cancellation policy, baggage, and more"));
children.push(bullet("Make a decision with no single source of truth for “which is actually the best deal”"));
children.push(p("Existing meta-search tools are vertical-locked (flights only, hotels only) and present a flat price list without explaining why one option wins."));

// 3. Goals
children.push(h1("3. Goals & Non-Goals"));
children.push(h2("Goals"));
children.push(bullet("G1 — One query searches N platforms in parallel across any vertical: flights, hotels, events, restaurants, products, trains, buses, car rentals"));
children.push(bullet("G2 — Produce a like-for-like comparison (compare Standard Rooms to Standard Rooms)"));
children.push(bullet("G3 — Auto-recommend the best option with transparent, data-cited reasoning"));
children.push(bullet("G4 — Surface trade-offs, not just price (amenities, ratings, cancellation, speed)"));
children.push(bullet("G5 — Be fully config-driven — adding a platform requires zero code changes"));
children.push(bullet("G6 — Run on a free / open-source stack with no per-query cost barrier"));
children.push(h2("Non-Goals (v2)"));
children.push(bullet("Completing a booking or payment — we deep-link the user to the platform to transact"));
children.push(bullet("User accounts, saved trips, or login"));
children.push(bullet("A mobile-native app (web-first)"));
children.push(bullet("Real-time inventory accuracy guarantees — data is best-effort"));

// 4. Users
children.push(h1("4. Target Users"));
children.push(buildTable(
  ["Persona", "Primary Need"],
  [
    ["Budget traveler (primary)", "Cheapest like-for-like option fast, across Indian booking sites"],
    ["Time-poor planner", "One screen that decides for them with a clear best pick"],
    ["Comparison shopper", "The full side-by-side matrix to make their own call"],
  ],
  [2600, 6760]
));

// 5. User stories
children.push(h1("5. User Stories"));
children.push(bullet("As a traveler, I type a free-text query and get results from multiple platforms on one screen, so I don’t open five tabs."));
children.push(bullet("As a shopper, I see a comparison table where the winner of each factor is marked, so I understand trade-offs at a glance."));
children.push(bullet("As a decisive user, I get a single Best Pick with reasoning and a one-click link to that listing."));
children.push(bullet("As an explorer, I can chat with the results (“show only non-stop”, “what about next Saturday?”) and the agent refines the search with full context."));
children.push(bullet("As a careful buyer, I trust the prices — impossible values are filtered out and currencies are normalized to ₹."));

children.push(new Paragraph({ children: [new PageBreak()] }));

// 6. Functional requirements
children.push(h1("6. Functional Requirements"));

children.push(h2("6.1 Intent Understanding"));
children.push(bullet("FR-1 — Parse free-text into structured intent: type, params (origin/destination/dates/budget/location), and target platforms, resolving relative dates (“this Friday” → real date)"));
children.push(bullet("FR-2 — Detect explicitly named platforms (“search on Zoomcar”) and prioritize them"));
children.push(bullet("FR-3 — Ask a clarifying question when critical info is missing, with an inline answer box"));

children.push(h2("6.2 Multi-Platform Search (parallel)"));
children.push(bullet("FR-4 — Search all selected platforms concurrently (one thread each)"));
children.push(bullet("FR-5 — Per-platform fallback chain for reliability: Tavily → universal browser automation → Google → DuckDuckGo"));
children.push(bullet("FR-6 — Universal browser automation: Playwright opens the real site; an LLM reads the live DOM, plans the form-fill (handling autocomplete dropdowns), submits, and scrapes the results page — no hardcoded selectors"));

children.push(h2("6.3 Data Integrity"));
children.push(bullet("FR-7 — Currency normalization to INR (deterministic Python conversion, not LLM math)"));
children.push(bullet("FR-8 — Domain-bounded price validation — reject physically impossible prices (e.g. ₹39 flight)"));
children.push(bullet("FR-9 — Reject hallucinated or generic results (“Option 1” with no real name)"));
children.push(bullet("FR-10 — Like-for-like anchoring: tag each result with a booking type (room type / cabin class / seat class) and anchor the comparison on the dominant type, so platforms are compared on the same tier"));

children.push(h2("6.4 Comparison Intelligence"));
children.push(bullet("FR-11 — Per-category comparison dimensions defined in config (hotels: price, rating, free-cancellation, breakfast, wifi, pool; flights: price, stops, duration, baggage, refundable)"));
children.push(bullet("FR-12 — Side-by-side comparison matrix — factors as rows, platforms as columns, winner of each factor marked"));
children.push(bullet("FR-13 — Smart badges auto-assigned per platform: Best Value, Cheapest, Top Rated, Fastest, Most Amenities"));
children.push(bullet("FR-14 — Value score (0–100 composite) per platform’s best option"));
children.push(bullet("FR-15 — AI trade-off takeaways plus a bottom-line verdict"));

children.push(h2("6.5 Recommendation & Action"));
children.push(bullet("FR-16 — Single Best Pick banner with reasoning, price analysis, confidence level, and alternatives"));
children.push(bullet("FR-17 — Deep-link buttons that open the platform pre-filled with the user’s search params (not the homepage)"));

children.push(h2("6.6 Conversational Refinement"));
children.push(bullet("FR-18 — Persistent, context-aware chat that knows all current results and either answers in-place or triggers a refined search"));

children.push(h2("6.7 Memory"));
children.push(bullet("FR-19 — Store results in ChromaDB to provide historical price context (“₹3,950 is 10% below the average we’ve seen”)"));

children.push(new Paragraph({ children: [new PageBreak()] }));

// 7. Architecture
children.push(h1("7. System Architecture"));
children.push(p("The system is a LangGraph pipeline of six nodes. The search node fans out to all platforms in parallel; each platform runs an independent fallback chain and an LLM extracts structured data from whatever source returns first."));
children.push(spacer());
children.push(p("Pipeline:", { bold: true }));
children.push(p("parse_intent  →  search_platforms  →  aggregate  →  compare  →  insights  →  recommend", { color: ACCENT, bold: true }));
children.push(spacer());
children.push(p("Per-platform search (inside search_platforms, one thread each):", { bold: true }));
children.push(num("Tavily — fast, reliable real listing data (primary)"));
children.push(num("Universal browser automation — Playwright + LLM form-filling for sites Tavily can’t cover"));
children.push(num("Google search via Playwright"));
children.push(num("DuckDuckGo — last resort"));
children.push(spacer());
children.push(p("Config-driven core: config/platforms.yaml holds all platforms, categories, and per-category comparison dimensions. Adding a platform or a comparison factor is a YAML edit — no code change.", { italics: true, color: MUTE }));

// 8. Tech stack
children.push(h1("8. Technology Stack"));
children.push(buildTable(
  ["Layer", "Choice", "Rationale"],
  [
    ["Orchestration", "LangGraph", "Stateful fan-out / fan-in multi-agent graph"],
    ["LLM", "Groq (Llama 3.3 70B / 3.1 8B)", "Free tier, very fast for parallel agents"],
    ["Browser", "Playwright (headed Chrome)", "Real automation on any site"],
    ["Search", "Tavily + DuckDuckGo", "Reliable AI-ready snippets, free"],
    ["Travel data", "RapidAPI (flights/hotels)", "ToS-compliant structured source"],
    ["Memory", "ChromaDB", "Local vector store, zero setup"],
    ["UI", "Streamlit", "Fast split-screen, real-time"],
    ["Hosting (planned)", "Hugging Face / Railway", "Free deployment"],
  ],
  [2200, 3400, 3760]
));

// 9. NFR
children.push(h1("9. Non-Functional Requirements"));
children.push(bulletRich([new TextRun({ text: "Performance: ", bold: true, size: 22 }), new TextRun({ text: "parallel search completes within a ~150s budget; Tavily-first keeps typical runs far faster", size: 22 })]));
children.push(bulletRich([new TextRun({ text: "Reliability: ", bold: true, size: 22 }), new TextRun({ text: "four-layer fallback per platform; graceful “no verified results” instead of crashing", size: 22 })]));
children.push(bulletRich([new TextRun({ text: "Accuracy / Trust: ", bold: true, size: 22 }), new TextRun({ text: "no fabricated prices; currency-normalized; like-for-like comparison", size: 22 })]));
children.push(bulletRich([new TextRun({ text: "Extensibility: ", bold: true, size: 22 }), new TextRun({ text: "new platform / category / factor via config only", size: 22 })]));
children.push(bulletRich([new TextRun({ text: "Transparency: ", bold: true, size: 22 }), new TextRun({ text: "raw agent state inspectable; every recommendation cites real numbers", size: 22 })]));

// 10. Success metrics
children.push(h1("10. Success Metrics"));
children.push(buildTable(
  ["Metric", "Definition", "Target"],
  [
    ["Coverage", "% of platforms returning ≥1 verified result per search", "≥ 80%"],
    ["Price accuracy", "% of displayed prices matching the live platform", "Spot-check"],
    ["Decision speed", "Time-to-best-pick vs manual multi-tab baseline", "Faster"],
    ["Engagement", "% of sessions using the refine-chat", "Track"],
    ["Click-through", "% of best-pick “Book” links followed", "Track"],
  ],
  [2400, 5160, 1800]
));

// 11. Risks
children.push(h1("11. Risks & Limitations"));
children.push(buildTable(
  ["Risk", "Mitigation"],
  [
    ["JS-heavy / anti-bot sites (Airbnb, Agoda) return 0 results", "Tavily-first; automation fallback; honest “no verified results”"],
    ["Scraped data can be stale or approximate", "Always deep-link to the live platform; show elapsed time"],
    ["LLM extraction errors", "Strict validation, domain bounds, generic-name rejection"],
    ["Exact-listing URL not always available", "Fall back to pre-filled search-results URL"],
    ["Streamlit caches the compiled graph", "Documented hard-restart; v2 badge + stale-process banner"],
  ],
  [4000, 5360]
));

// 12. Roadmap
children.push(h1("12. Roadmap"));
children.push(p("Now (v2):", { bold: true }));
children.push(bullet("Parallel multi-platform search, like-for-like comparison matrix, smart badges, value scores, AI takeaways, persistent chat, deep-links"));
children.push(p("Next:", { bold: true }));
children.push(bullet("“Filter by must-haves” toggle (e.g. only free-cancellation hotels)"));
children.push(bullet("Price-drop tracking using the existing ChromaDB history"));
children.push(bullet("Expose Agent-Aware as an MCP server so Claude Desktop can call it as a tool"));
children.push(bullet("Deploy to a public URL"));
children.push(bullet("Remove dead form_filler.py (superseded by universal_filler.py)"));

// ───────────────────────── assemble ─────────────────────────
const doc = new Document({
  creator: "Bella",
  title: "Agent-Aware PRD",
  styles: {
    default: { document: { run: { font: "Calibri", size: 22, color: INK } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 30, bold: true, color: ACCENT, font: "Calibri" },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 24, bold: true, color: INK, font: "Calibri" },
        paragraph: { spacing: { before: 200, after: 100 }, outlineLevel: 1 } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 280 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1080, hanging: 280 } } } },
      ]},
      { reference: "numbers", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 540, hanging: 280 } } } },
      ]},
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        spacing: { after: 0 },
        border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: BORDER, space: 4 } },
        children: [
          new TextRun({ text: "Agent-Aware", bold: true, size: 16, color: ACCENT }),
          new TextRun({ text: "\tProduct Requirements Document", size: 16, color: MUTE }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      })] }),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          new TextRun({ text: "Page ", size: 16, color: MUTE }),
          new TextRun({ children: [PageNumber.CURRENT], size: 16, color: MUTE }),
          new TextRun({ text: " of ", size: 16, color: MUTE }),
          new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: MUTE }),
        ],
      })] }),
    },
    children,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("docs/Agent-Aware-PRD.docx", buf);
  console.log("Wrote docs/Agent-Aware-PRD.docx");
});
