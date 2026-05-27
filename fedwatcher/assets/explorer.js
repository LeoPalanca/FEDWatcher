/*
 * FedWatcher v2 — explorer
 * Adapted from the v1 explorer:
 *  - hero hydration writes the v2 tile IDs
 *  - table picker renders as pill buttons (.pill)
 *  - tone gauge, next-move histogram, breakdown table + summary,
 *    signal-divergence chart (statements only, green/red fill by tone vs market),
 *    tile info popovers, sparkline updates,
 *    §03 chart toggles (releases markers, show table),
 *    highlighted series legend chip + explanatory note
 */
(() => {
  "use strict";

  /* ===== mock data URLs (static preview) ===== */
  const URL_SNAPSHOT  = "/api/snapshot";
  const URL_DOCUMENTS = "/api/documents?limit=1000";
  const URL_FAKEFED   = "/assets/fakefed-documents.json";

  /* ===== explorer config (carried from v1) ===== */
  // Only these tables surface in the v2 explorer
  const ALLOWED_TABLES = ["macro_data"];
  const TABLE_OVERRIDES = {
    macro_data:  { label: "Macro",   numeric: ["us2y_yield", "core_cpi_yoy", "unemployment_rate"], xField: "observation_month", title: "2Y Yield · Core CPI · Unemployment", sub: "Monthly observations · FRED", displayColumns: ["observation_month", "us2y_yield", "core_cpi_yoy", "unemployment_rate", "interpolated_fields"], highlight: "us2y_yield", highlightTable: true },
    market_data: { label: "Markets", numeric: ["us2y_yield", "sofr_rate", "ois_1m", "ois_3m", "ois_6m", "ois_1y", "ois_2y"], xField: "timestamp", title: "2Y Yield · SOFR · OIS curve", sub: "Market implied rates", displayColumns: ["timestamp", "us2y_yield", "sofr_rate", "ois_1m", "ois_3m", "ois_6m", "ois_1y", "ois_2y"], highlight: "us2y_yield", highlightTable: false },
  };
  const PALETTE = [
    "oklch(0.45 0.08 230)", "oklch(0.52 0.13 25)", "oklch(0.50 0.09 155)",
    "oklch(0.62 0.10 75)",  "oklch(0.42 0.012 80)", "oklch(0.55 0.10 290)", "oklch(0.55 0.10 200)",
  ];
  const RANGES = [
    { key: "1Y", label: "1Y", months: 12 },
    { key: "5Y", label: "5Y", months: 60 },
    { key: "10Y", label: "10Y", months: 120 },
    { key: "MAX", label: "Max", months: null },
  ];

  /* ===== bucket config (v2) ===== */
  const BUCKETS = [
    { key: "-50", bps: -50, label: "−50 bps", kind: "cut",   pct: 4 },
    { key: "-25", bps: -25, label: "−25 bps", kind: "cut",   pct: 14 },
    { key: "0",   bps: 0,   label: "0 bps",   kind: "hold",  pct: 55 },
    { key: "+25", bps: 25,  label: "+25 bps", kind: "hike",  pct: 22 },
    { key: "+50", bps: 50,  label: "+50 bps", kind: "hike",  pct: 5 },
  ];
  const BUCKET_EXPLANATIONS = {
    "-50": "A 50-bp insurance cut would require a sharp deterioration in either employment or financial conditions. The agent sees almost no language in recent communication supporting this scenario.",
    "-25": "A measured 25-bp cut becomes plausible if the next two CPI prints show clear softening AND payrolls slow meaningfully. The dovish wing of the Committee has consistently flagged this risk.",
    "0":   "Modal outcome. The May statement's new \"inflation has been somewhat elevated\" line aligns with continued patience. Market-implied OIS curve agrees: hold is the consensus.",
    "+25": "An insurance hike re-enters the conversation if shelter inflation re-accelerates or services PMI surprises to the upside. Hawkish dissents have referenced this as a credible option.",
    "+50": "Tail risk. Would require a CPI shock above 4% or a disorderly weakening of dollar/yields combo. No participant has openly endorsed this path.",
  };

  /* ===== shared state ===== */
  const $ = (id) => document.getElementById(id);
  const fmt = (v) => v === null || v === undefined || v === "" ? "—" : (typeof v === "number" ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v));
  const isNumLike = (v) => v !== null && v !== undefined && v !== "" && !isNaN(Number(v));

  let DATA = null;
  let TABLES = [];
  let activeKey = "macro_data";
  let activeRange = "10Y";
  let divRange = "10Y";
  let scaleMode = "multi";
  let chart = null;
  let divChart = null;
  let hiddenSeries = new Set();
  let hiddenDivSeries = new Set();

  let DOCS = [];
  let OFFICIAL_DOCS = [];
  let FAKEFED_DOCS = [];
  let docFilter = "all";
  let docQuery = "";
  let fakeFedLoaded = false;
  let fakeFedEnabled = false;

  let activeBucket = "0";

  // v2 §03 toggles
  let showReleases = false;
  let showTable = false;

  // Defensive: drop rows where every non-id/non-date column is null/empty.
  // Prevents partial-month FRED placeholders (e.g. unpublished current month)
  // from breaking the latest tile, sparklines, and chart endpoints.
  function stripTrailingEmptyRows(data) {
    if (!data || typeof data !== "object") return;
    const skip = new Set(["id", "observation_month", "timestamp", "release_date", "created_at", "updated_at", "interpolated_fields", "source"]);
    for (const key of Object.keys(data)) {
      const tbl = data[key];
      if (!tbl || !Array.isArray(tbl.rows) || !Array.isArray(tbl.columns)) continue;
      const cols = tbl.columns.filter(c => !skip.has(c));
      if (!cols.length) continue;
      tbl.rows = tbl.rows.filter(row => cols.some(c => {
        const v = row[c];
        return v !== null && v !== undefined && v !== "";
      }));
    }
  }

  async function load() {
    try {
      const [d, docs] = await Promise.all([
        fetchJson(URL_SNAPSHOT, {}),
        fetchJson(URL_DOCUMENTS, []),
      ]);
      DATA = d;
      stripTrailingEmptyRows(DATA);
      OFFICIAL_DOCS = normalizeDocumentsPayload(docs);
      DOCS = OFFICIAL_DOCS.slice();
    } catch (e) {
      DATA = {}; OFFICIAL_DOCS = []; DOCS = [];
      console.error("data load failed", e);
    }
    TABLES = buildTableDefinitions(DATA);
    if (!TABLES.some(t => t.key === activeKey && (DATA[t.key]?.rows ?? []).length)) {
      activeKey = TABLES.find(t => (DATA[t.key]?.rows ?? []).length)?.key ?? TABLES[0]?.key ?? activeKey;
    }
    // v2 new renders
    renderToneGauge(0);
    hydrateTone();
    hydrateBuckets();
    renderBucketMini();
    renderBreakdown();
    renderDivergenceChart();
    wireDivergenceRange();
    wireDivergenceLegend();
    wirePopovers();
    // shared with v1
    renderTablePicker();
    renderRangePicker();
    wireScalePicker();
    wireV2ChartToggles();
    renderActive();
    wireModal();
    hydrateHero();
    renderFeed();
    wireFeed();
    wireSourceSwitch();
  }

  async function fetchJson(url, fallbackValue) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) return await response.json();
    } catch (e) {
      console.error(`fetch failed for ${url}.`, e);
    }
    return fallbackValue;
  }

  function normalizeDocumentsPayload(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.rows)) return payload.rows;
    return [];
  }

  function buildTableDefinitions(data) {
    return Object.keys(data || {}).filter(k => ALLOWED_TABLES.includes(k)).sort((a, b) => {
      const ai = ALLOWED_TABLES.indexOf(a);
      const bi = ALLOWED_TABLES.indexOf(b);
      return ai - bi;
    }).map(key => {
      const tbl = data[key] ?? { columns: [], rows: [] };
      const override = TABLE_OVERRIDES[key] ?? {};
      const numeric = override.numeric ?? inferNumericColumns(tbl);
      const xField = override.xField ?? inferXField(tbl);
      return {
        key,
        label: override.label ?? humanizeTableName(key),
        numeric, xField,
        displayColumns: override.displayColumns,
        highlight: override.highlight,
        highlightTable: override.highlightTable === true,
        title: override.title ?? humanizeTableName(key),
        sub: override.sub ?? `${(tbl.rows ?? []).length.toLocaleString()} rows · ${(tbl.columns ?? []).length.toLocaleString()} columns`,
      };
    });
  }

  function inferNumericColumns(tbl) {
    const rows = tbl.rows ?? [];
    return (tbl.columns ?? []).filter(col => {
      if (col === "id" || col.endsWith("_id")) return false;
      const sample = rows.map(row => row[col]).filter(v => v !== null && v !== undefined && v !== "").slice(0, 25);
      return sample.length > 0 && sample.every(isNumLike);
    });
  }

  function inferXField(tbl) {
    const columns = tbl.columns ?? [];
    const preferred = ["observation_month", "meeting_date", "release_date", "created_at", "updated_at", "timestamp", "run_at", "id"];
    return preferred.find(col => columns.includes(col)) ?? columns[0] ?? "id";
  }

  function humanizeTableName(key) {
    return key.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
  }

  /* =========================================================
   *  V2 — TONE GAUGE
   * ========================================================= */
  function renderToneGauge(value, opts = {}) {
    const v = Math.max(-1, Math.min(1, value));
    // Map -1..+1 to -90deg..+90deg
    const deg = v * 90;
    const needle = $("tone-needle"); // <g> wrapping the line + tip
    if (needle) needle.style.transform = `rotate(${deg}deg)`;
    const out = $("tone-value");
    if (out) {
      if (typeof opts.divergencePp === "number" && isFinite(opts.divergencePp)) {
        const d = opts.divergencePp;
        out.textContent = `${d >= 0 ? "+" : ""}${d.toFixed(2)} pp`;
      } else {
        out.textContent = (v >= 0 ? "+" : "") + v.toFixed(2);
      }
    }
  }

  function latestStatementSignal() {
    const docs = DATA?.documents?.rows || [];
    const sigs = DATA?.signals?.rows || [];
    if (!docs.length || !sigs.length) return null;
    const stmt = new Map();
    for (const d of docs) {
      if (d.doc_type === "statement") stmt.set(d.id, d);
    }
    const joined = [];
    for (const s of sigs) {
      const doc = stmt.get(s.document_id);
      if (doc) joined.push({ sig: s, doc });
    }
    if (!joined.length) return null;
    joined.sort((a, b) => String(b.doc.release_date).localeCompare(String(a.doc.release_date)));
    return { latest: joined[0], previous: joined[1] || null };
  }

  function hydrateTone() {
    const result = latestStatementSignal();
    if (!result) { renderToneGauge(0); return; }
    const { latest, previous } = result;
    const tone = Number(latest.sig.smoothed_tone);
    const toneRate = Number(latest.sig.tone_implied_next_rate);
    const mktRate = Number(latest.sig.market_implied_next_rate);
    const divFromDb = Number(latest.sig.divergence);
    // Canonical convention: divergence = market − tone (pp)
    const divergence = isFinite(divFromDb)
      ? divFromDb
      : (isFinite(mktRate) && isFinite(toneRate) ? mktRate - toneRate : NaN);

    renderToneGauge(isFinite(tone) ? tone : 0, {
      divergencePp: isFinite(divergence) ? divergence : undefined,
    });

    const deltaEl = document.querySelector('#overview [data-tile="tone"] .delta');
    if (deltaEl && previous && isFinite(divergence)) {
      const prevDiv = Number(previous.sig.divergence);
      const prevTone = Number(previous.sig.tone_implied_next_rate);
      const prevMkt = Number(previous.sig.market_implied_next_rate);
      const prev = isFinite(prevDiv)
        ? prevDiv
        : (isFinite(prevMkt) && isFinite(prevTone) ? prevMkt - prevTone : NaN);
      if (isFinite(prev)) {
        const d = divergence - prev;
        deltaEl.textContent = `${d >= 0 ? "+" : ""}${d.toFixed(2)} pp vs prior`;
      }
    }

    // Refresh tone popover body with live numbers
    const pop = POPOVERS.tone;
    if (pop) {
      const fmt = (v, suffix = "%") => isFinite(v) ? `${v.toFixed(3)}${suffix}` : "—";
      const tonePart = isFinite(tone) ? `${tone >= 0 ? "+" : ""}${tone.toFixed(2)}` : "—";
      pop.body = `<p>The agent reads the latest FOMC statement and produces a smoothed tone score (−1 dovish → +1 hawkish), then maps it to a tone-implied next-meeting rate. The tile value is the divergence between what the OIS market is pricing and what the tone implies.</p>
             <dl class="kv">
               <dt>Smoothed tone</dt><dd>${tonePart}</dd>
               <dt>Tone-implied rate</dt><dd>${fmt(toneRate)}</dd>
               <dt>Market-implied rate (OIS)</dt><dd>${fmt(mktRate)}</dd>
               <dt>Divergence (market − tone)</dt><dd>${isFinite(divergence) ? `${divergence >= 0 ? "+" : ""}${divergence.toFixed(3)} pp` : "—"}</dd>
               <dt>Source</dt><dd>${latest.doc.release_date || "—"} · ${latest.doc.central_bank || "FED"}</dd>
             </dl>`;
    }
  }

  /* =========================================================
   *  V2 — NEXT-MOVE BUCKET (mini histogram on tile)
   * ========================================================= */
  function latestPolicyRate() {
    const rows = (DATA?.macro_data?.rows || [])
      .filter(r => r.policy_rate !== null && r.policy_rate !== undefined && r.policy_rate !== "")
      .sort((a, b) => String(a.observation_month).localeCompare(String(b.observation_month)));
    return rows.length ? Number(rows[rows.length - 1].policy_rate) : NaN;
  }

  function hydrateBuckets() {
    const result = latestStatementSignal();
    if (!result) return;
    const sig = result.latest.sig;
    // signals.prob_* columns are 0..1; align with BUCKETS order [-50, -25, 0, +25, +50]
    const probCols = ["prob_cut_50", "prob_cut_25", "prob_hold", "prob_hike_25", "prob_hike_50"];
    const raw = probCols.map(c => Number(sig[c]));
    if (raw.some(v => !isFinite(v))) return;
    const sum = raw.reduce((a, b) => a + b, 0) || 1;
    BUCKETS.forEach((b, i) => { b.pct = Math.round(raw[i] / sum * 1000) / 10; });

    // Modal bucket = argmax
    let modalIdx = 0;
    BUCKETS.forEach((b, i) => { if (b.pct > BUCKETS[modalIdx].pct) modalIdx = i; });

    // Refresh popover body with live numbers
    const modal = BUCKETS[modalIdx];
    const pop = POPOVERS.bucket;
    if (pop) {
      const probRows = BUCKETS.map(b => `<dt>${b.label}</dt><dd>${b.pct.toFixed(1)}%</dd>`).join("");
      pop.body = `<p>Probabilities over the next-meeting outcome, read directly from the latest <code>signals</code> row's <code>prob_cut_50 … prob_hike_50</code> columns. The modal bucket is the argmax.</p>
             <dl class="kv">${probRows}
               <dt>Modal bucket</dt><dd>${modal.label} · ${modal.pct.toFixed(1)}%</dd>
               <dt>Source</dt><dd>${result.latest.doc.release_date || "—"} · signals row #${sig.id ?? "—"}</dd>
             </dl>`;
    }

    // Re-flag the modal label in §01 mini histogram
    const labels = $("bucket-mini-labels");
    if (labels) {
      labels.querySelectorAll("span").forEach((sp, i) => {
        sp.classList.toggle("active", i === modalIdx);
      });
    }
  }

  function renderBucketMini() {
    const root = $("bucket-mini");
    if (!root) return;
    const max = Math.max(...BUCKETS.map(b => b.pct));
    root.innerHTML = "";
    for (const b of BUCKETS) {
      const cell = document.createElement("div");
      const isModal = b.pct === max;
      cell.className = "bucket-mini-bar" + (isModal ? " modal" : "");
      cell.title = `${b.label} · ${b.pct}%`;
      if (isModal) cell.setAttribute("data-pct", `${b.pct}%`);
      const fill = document.createElement("div");
      fill.className = "fill";
      requestAnimationFrame(() => { fill.style.height = `${b.pct}%`; });
      cell.appendChild(fill);
      root.appendChild(cell);
    }
    // Headline values
    const modal = BUCKETS.reduce((a, b) => b.pct > a.pct ? b : a);
    const headPct = document.querySelector("#overview .bucket-mini-headline .pct");
    const headLabel = document.querySelector("#overview .bucket-mini-headline .label");
    if (headPct) headPct.innerHTML = `${modal.pct}<span class="unit" style="font-size:12px;color:var(--c-ink-3);font-weight:400;margin-left:2px;">%</span>`;
    if (headLabel) {
      const word = modal.kind === "hold" ? "Hold" : modal.kind === "cut" ? "Cut" : "Hike";
      headLabel.textContent = `${word} · ${modal.label}`;
    }
  }

  /* =========================================================
   *  V2 — BREAKDOWN: bucket table only (single summary lives in HTML)
   * ========================================================= */
  function renderBreakdown() {
    const table = $("bucket-table");
    if (!table) return;
    // table rows (header already in HTML)
    while (table.children.length > 1) table.removeChild(table.lastChild);

    // Rank buckets by probability (highest = darkest accent shade)
    const ranked = BUCKETS.slice().sort((a, b) => b.pct - a.pct);
    const shadeByKey = new Map();
    ranked.forEach((b, i) => shadeByKey.set(b.key, `shade-${Math.min(i, 4)}`));

    for (const b of BUCKETS) {
      const row = document.createElement("div");
      row.className = "bucket-row";
      row.dataset.bucket = b.key;
      const shadeClass = shadeByKey.get(b.key);
      row.innerHTML = `
        <div class="name">${b.label}<small>${b.kind}</small></div>
        <div class="track"><div class="fill ${shadeClass}" style="width:0%"></div></div>
        <div class="pct">${b.pct}%</div>
      `;
      requestAnimationFrame(() => {
        row.querySelector(".fill").style.width = `${b.pct}%`;
      });
      row.addEventListener("mouseenter", () => setActiveBucket(b.key));
      row.addEventListener("click", () => setActiveBucket(b.key));
      table.appendChild(row);
    }
    setActiveBucket(BUCKETS.reduce((a, c) => c.pct > a.pct ? c : a).key);
  }

  function setActiveBucket(key) {
    activeBucket = key;
    document.querySelectorAll(".bucket-row").forEach(r => r.classList.toggle("active", r.dataset.bucket === key));
  }

  /* =========================================================
   *  V2 — SIGNAL DIVERGENCE CHART
   *  Statements only. Fill is green where tone>market, red where tone<market.
   *  Range picker (1Y / 5Y / 10Y / Max) filters the visible window.
   * ========================================================= */
  function divergenceCutoff(rangeKey) {
    if (rangeKey === "MAX") return null;
    const months = { "1Y": 12, "5Y": 60, "10Y": 120 }[rangeKey];
    if (!months) return null;
    const d = new Date();
    d.setMonth(d.getMonth() - months);
    return d.toISOString().slice(0, 10);
  }

  function wireDivergenceRange() {
    const root = $("divergence-range");
    if (!root) return;
    root.querySelectorAll("button").forEach(b => {
      b.addEventListener("click", () => {
        divRange = b.dataset.divrange || "10Y";
        root.querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
        renderDivergenceChart();
      });
    });
  }

  function wireDivergenceLegend() {
    document.querySelectorAll("[data-divseries]").forEach(btn => {
      const series = btn.getAttribute("data-divseries");
      btn.addEventListener("click", () => {
        if (hiddenDivSeries.has(series)) hiddenDivSeries.delete(series);
        else hiddenDivSeries.add(series);
        btn.classList.toggle("muted", hiddenDivSeries.has(series));
        renderDivergenceChart();
      });
    });
  }

  function renderDivergenceChart() {
    const ctx = $("divergence-chart");
    if (!ctx) return;
    if (divChart) { divChart.destroy(); divChart = null; }

    // Statements only, sorted ascending by release_date
    const allDocs = (DOCS || []).slice()
      .filter(d => d.doc_type === "statement")
      .sort((a, b) => String(a.release_date).localeCompare(String(b.release_date)));
    const signalsByDoc = {};
    (DATA?.signals?.rows || []).forEach(s => { signalsByDoc[s.document_id] = s; });

    // Build policy_rate lookup keyed by YYYY-MM (latest non-null per month carried forward)
    const macroRows = (DATA?.macro_data?.rows || [])
      .slice()
      .sort((a, b) => String(a.observation_month).localeCompare(String(b.observation_month)));
    const policyByMonth = new Map();
    let carry = null;
    for (const r of macroRows) {
      if (r.policy_rate !== null && r.policy_rate !== undefined && r.policy_rate !== "") {
        carry = Number(r.policy_rate);
      }
      if (isFinite(carry)) policyByMonth.set(String(r.observation_month), carry);
    }
    const policyForDate = (dateStr) => {
      const key = String(dateStr || "").slice(0, 7);
      if (!key) return null;
      if (policyByMonth.has(key)) return policyByMonth.get(key);
      // fallback: walk back month-by-month up to 12 months
      let [y, m] = key.split("-").map(Number);
      for (let i = 0; i < 12 && y > 0; i++) {
        m -= 1;
        if (m === 0) { m = 12; y -= 1; }
        const k = `${y}-${String(m).padStart(2, "0")}`;
        if (policyByMonth.has(k)) return policyByMonth.get(k);
      }
      return null;
    };

    let lastTone = 5.25, lastMarket = 5.25, lastPolicy = null;
    let seenAnySignal = false;
    const allPoints = [];
    for (const doc of allDocs) {
      const sig = signalsByDoc[doc.id];
      let tone, market;
      if (sig) {
        tone   = Number(sig.tone_implied_next_rate);
        market = Number(sig.market_implied_next_rate);
        seenAnySignal = true;
      } else {
        tone = lastTone; market = lastMarket;
      }
      lastTone = tone; lastMarket = market;
      const policy = policyForDate(doc.release_date);
      if (policy !== null && isFinite(policy)) lastPolicy = policy;
      allPoints.push({
        date: doc.release_date,
        type: "statement",
        tone, market, policy: policy !== null ? policy : lastPolicy, doc,
      });
    }

    // Append two forward-projected points (no markers, never trimmed by range)
    if (allPoints.length) {
      const last = allPoints[allPoints.length - 1];
      allPoints.push({ date: "t+1", type: "projected", tone: last.tone + 0.06, market: last.market + 0.02, policy: last.policy });
      allPoints.push({ date: "E",   type: "projected", tone: last.tone + 0.16, market: last.market + 0.05, policy: last.policy });
    }

    // Apply range filter (keep all projected; keep historical >= cutoff)
    const cutoff = divergenceCutoff(divRange);
    const points = cutoff
      ? allPoints.filter(p => p.type === "projected" || (p.date && p.date >= cutoff))
      : allPoints;

    if (!points.length) {
      ctx.style.display = "none";
      const meta = $("divergence-meta");
      if (meta) meta.textContent = "No release data yet";
      return;
    }
    ctx.style.display = "";

    const monthShort = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const formatLabel = (p) => {
      if (p.type === "projected") return p.date === "t+1" ? "t+1" : "E";
      const d = new Date(p.date + "T12:00:00");
      if (isNaN(d)) return p.date;
      return `${monthShort[d.getMonth()]} '${String(d.getFullYear()).slice(-2)}`;
    };

    const labels = points.map(formatLabel);
    const tone   = points.map(p => p.tone);
    const market = points.map(p => p.market);
    const policy = points.map(p => (p.policy === null || p.policy === undefined || !isFinite(p.policy)) ? null : p.policy);

    // Per-point styling for the tone line
    const pointStyle  = points.map(p => p.type === "statement" ? "rect" : "circle");
    const pointRadius = points.map(p => p.type === "projected" ? 0 : 5);
    const pointHover  = points.map(p => p.type === "projected" ? 0 : 8);
    const pointBg     = "oklch(0.45 0.08 230)";

    const grid  = { color: "oklch(0.910 0.005 80)", drawTicks: false };
    const ticks = { color: "oklch(0.460 0.010 80)", font: { family: "JetBrains Mono", size: 10 } };

    const POS_FILL = "oklch(0.70 0.16 145 / 0.28)"; // green: tone > market
    const NEG_FILL = "oklch(0.62 0.18 25 / 0.28)";  // red:   tone < market

    const marketHidden = hiddenDivSeries.has("Market-implied (OIS)");
    const toneHidden   = hiddenDivSeries.has("Tone-implied");
    const policyHidden = hiddenDivSeries.has("Policy rate (FEDFUNDS)");

    divChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          // Index 0 — Market-implied (target for fill)
          {
            hidden: marketHidden,
            label: "Market-implied (OIS)", data: market,
            borderColor: "oklch(0.42 0.012 80)",
            backgroundColor: "oklch(0.42 0.012 80)",
            borderWidth: 1.75, borderDash: [4, 3],
            pointRadius: 0, pointHoverRadius: 4,
            tension: 0.32, fill: false,
          },
          // Index 1 — Tone-implied; fill toward dataset 0 with green/red split
          {
            hidden: toneHidden,
            label: "Tone-implied", data: tone,
            borderColor: "oklch(0.45 0.08 230)",
            backgroundColor: pointBg,
            borderWidth: 1.75,
            pointStyle, pointRadius, pointHoverRadius: pointHover,
            pointBackgroundColor: pointBg,
            pointBorderColor: pointBg,
            pointBorderWidth: 1,
            pointHoverBackgroundColor: pointBg,
            pointHoverBorderColor: pointBg,
            tension: 0.32,
            fill: marketHidden ? false : { target: 0, above: POS_FILL, below: NEG_FILL },
          },
          // Index 2 — Policy rate (FEDFUNDS, monthly average) as step line
          {
            hidden: policyHidden,
            label: "Policy rate (FEDFUNDS)", data: policy,
            borderColor: "oklch(0.55 0.12 75)",
            backgroundColor: "oklch(0.55 0.12 75)",
            borderWidth: 1.5,
            pointRadius: 0, pointHoverRadius: 4,
            stepped: "before",
            spanGaps: true,
            tension: 0, fill: false,
          },
        ],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "oklch(0.15 0.012 80 / 0.96)",
            titleFont: { family: "JetBrains Mono", size: 11 },
            bodyFont:  { family: "JetBrains Mono", size: 12 },
            padding: 10,
            borderColor: "oklch(0.45 0.08 230)", borderWidth: 1,
            callbacks: {
              title: (items) => {
                if (!items?.length) return "";
                const p = points[items[0].dataIndex];
                if (p.type === "projected") return p.date === "t+1" ? "Projected · next meeting (t+1)" : "Projected · horizon (E)";
                const d = new Date(p.date + "T12:00:00");
                return `■ FOMC Statement · ${monthShort[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
              },
              label: (item) => {
                if (item.dataset.label === "Tone-implied") return `Tone-implied: ${item.parsed.y.toFixed(3)}%`;
                if (item.dataset.label === "Market-implied (OIS)") return `Market-implied: ${item.parsed.y.toFixed(3)}%`;
                if (item.dataset.label === "Policy rate (FEDFUNDS)") return `Policy rate: ${item.parsed.y.toFixed(3)}%`;
                return null;
              },
              afterBody: (items) => {
                if (!items?.length) return "";
                const i = items[0].dataIndex;
                const diff = (market[i] - tone[i]) * 100;
                return `Δ ${diff >= 0 ? "+" : ""}${diff.toFixed(0)} bps (market − tone)`;
              },
            },
          },
        },
        scales: {
          x: { ticks: { ...ticks, maxRotation: 0, autoSkipPadding: 24 }, grid },
          y: { ticks: { ...ticks, callback: (v) => `${v.toFixed(2)}%` }, grid },
        },
      },
    });

    const lastDiff = (market[market.length - 1] - tone[tone.length - 1]) * 100;
    const statementCount = points.filter(p => p.type === "statement").length;
    const meta = $("divergence-meta");
    if (meta) meta.textContent = `Current divergence · ${lastDiff >= 0 ? "+" : ""}${lastDiff.toFixed(0)} bps (market − tone) · ■ ${statementCount} statements · ${divRange}`;
  }

  /* =========================================================
   *  V2 — TILE INFO POPOVERS (personalization)
   * ========================================================= */
  const POPOVERS = {
    tone: {
      eyebrow: "Tone score",
      title: "Hawkish/dovish, in one number.",
      body: `<p>The agent assigns each sentence in the latest release a tone score from −1 (most dovish) to +1 (most hawkish), then aggregates by the section weights shown on the Overview.</p>
             <dl class="kv">
               <dt>Current value</dt><dd>+0.31</dd>
               <dt>vs previous</dt><dd>+0.04</dd>
               <dt>Range</dt><dd>−1.00 to +1.00</dd>
               <dt>Source</dt><dd>FOMC statement · May 7, 2026</dd>
             </dl>`,
    },
    bucket: {
      eyebrow: "Next-move bucket",
      title: "Probability distribution over the next decision.",
      body: `<p>Five plausible Committee outcomes for the next meeting. The modal bucket is the one with the highest probability; ties are broken by the agent's confidence score.</p>
             <dl class="kv">
               <dt>Modal bucket</dt><dd>0 bps (Hold)</dd>
               <dt>Modal probability</dt><dd>55%</dd>
               <dt>Next meeting</dt><dd>Jun 17, 2026</dd>
               <dt>Model</dt><dd>Tone + macro joint logit</dd>
             </dl>`,
    },
    cpi: {
      eyebrow: "Core CPI · YoY",
      title: "Inflation excluding food and energy.",
      body: `<p>Year-over-year percent change in the Bureau of Labor Statistics Consumer Price Index for All Urban Consumers, Less Food and Energy.</p>
             <dl class="kv">
               <dt>Series</dt><dd>CPILFESL</dd>
               <dt>Frequency</dt><dd>Monthly</dd>
               <dt>Source</dt><dd>FRED / BLS</dd>
             </dl>`,
    },
    un: {
      eyebrow: "Unemployment rate",
      title: "U-3 headline unemployment.",
      body: `<p>Civilian unemployment rate, seasonally adjusted, age 16 and over.</p>
             <dl class="kv">
               <dt>Series</dt><dd>UNRATE</dd>
               <dt>Frequency</dt><dd>Monthly</dd>
               <dt>Source</dt><dd>FRED / BLS</dd>
             </dl>`,
    },
  };

  function wirePopovers() {
    const overlay = $("popover");
    if (!overlay) return;
    document.querySelectorAll(".tile-info").forEach(btn => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const key = btn.dataset.info;
        const config = POPOVERS[key];
        if (!config) return;
        $("popover-eyebrow").textContent = config.eyebrow;
        $("popover-title").textContent = config.title;
        $("popover-body").innerHTML = config.body;
        overlay.hidden = false;
        document.body.style.overflow = "hidden";
      });
    });
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay || e.target.dataset.popoverClose !== undefined) closePopover();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !overlay.hidden) closePopover();
    });
  }
  function closePopover() {
    const overlay = $("popover");
    if (!overlay) return;
    overlay.hidden = true;
    document.body.style.overflow = "";
  }

  /* =========================================================
   *  EXPLORER (DB overview, table picker as PILLS, etc.)
   * ========================================================= */
  function renderDbOverview() {
    const root = $("db-overview");
    if (!root) return;
    const tables = TABLES.map(t => DATA[t.key] ?? { columns: [], rows: [] });
    const rowTotal = tables.reduce((sum, tbl) => sum + (tbl.rows?.length ?? 0), 0);
    const populated = TABLES.filter(t => (DATA[t.key]?.rows ?? []).length > 0).length;
    const cells = [
      ["Tables", TABLES.length],
      ["Populated", populated],
      ["Rows", rowTotal],
      ["Snapshot", new Date().toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" })],
    ];
    root.innerHTML = cells.map(([label, value]) => `
      <div class="db-stat">
        <span>${label}</span>
        <strong>${typeof value === "number" ? value.toLocaleString() : value}</strong>
      </div>
    `).join("");
  }

  function hydrateHero() {
    const macro = DATA?.macro_data;
    if (!macro?.rows?.length) return;
    const rows = macro.rows.slice().sort((a, b) => String(a.observation_month).localeCompare(String(b.observation_month)));
    const lastWith = (col) => { for (let i = rows.length - 1; i >= 0; i--) if (isNumLike(rows[i][col])) return rows[i]; return null; };
    const cpiRow = lastWith("core_cpi_yoy") || lastWith("core_cpi_index");
    const unRow  = lastWith("unemployment_rate");
    if (cpiRow) {
      const yoy = isNumLike(cpiRow.core_cpi_yoy) ? Number(cpiRow.core_cpi_yoy) : null;
      const idx = isNumLike(cpiRow.core_cpi_index) ? Number(cpiRow.core_cpi_index) : null;
      const cpiText = yoy !== null ? `${yoy.toFixed(1)}` : (idx !== null ? idx.toFixed(2) : "—");
      const cpiUnit = yoy !== null ? "%" : "·idx";
      const m = $("metric-cpi-v2");
      if (m) m.innerHTML = `${cpiText}<span class="unit">${cpiUnit}</span>`;
      const dEl = $("metric-cpi-d-v2");
      if (dEl) {
        const prevIdx = rows.findIndex(r => r === cpiRow) - 1;
        if (prevIdx >= 0 && isNumLike(rows[prevIdx].core_cpi_yoy) && yoy !== null) {
          const d = yoy - Number(rows[prevIdx].core_cpi_yoy);
          dEl.textContent = `${d >= 0 ? "+" : ""}${d.toFixed(2)} pp · vs prior month`;
          dEl.classList.remove("neg", "pos");
          dEl.style.color = "var(--c-ink-2)";
        }
      }
      const cpiWindow = rows.filter(r => isFinite(Number(r.core_cpi_yoy)));
      drawSpark($("cpi-spark"), cpiWindow.slice(-36).map(r => Number(r.core_cpi_yoy)), "var(--c-ink-strong)");
      const sparkMeta = $("cpi-spark-meta");
      if (sparkMeta && cpiWindow.length) {
        const start = cpiWindow.slice(-36)[0]?.observation_month;
        const end = cpiWindow[cpiWindow.length - 1]?.observation_month;
        if (start && end) sparkMeta.textContent = `${formatMonth(start)} → ${formatMonth(end)}`;
      }
    }
    if (unRow) {
      const u = Number(unRow.unemployment_rate);
      const m = $("metric-un-v2");
      if (m) m.innerHTML = `${u.toFixed(1)}<span class="unit">%</span>`;
      const dEl = $("metric-un-d-v2");
      if (dEl) {
        const prevIdx = rows.findIndex(r => r === unRow) - 1;
        if (prevIdx >= 0 && isNumLike(rows[prevIdx].unemployment_rate)) {
          const d = u - Number(rows[prevIdx].unemployment_rate);
          dEl.textContent = `${d >= 0 ? "+" : ""}${d.toFixed(1)} pp · vs prior month`;
          dEl.classList.remove("neg", "pos");
          dEl.style.color = "var(--c-ink-2)";
        }
      }
      const unWindow = rows.filter(r => isFinite(Number(r.unemployment_rate)));
      drawSpark($("un-spark"), unWindow.slice(-36).map(r => Number(r.unemployment_rate)), "var(--c-ink-strong)");
      const sparkMeta = $("un-spark-meta");
      if (sparkMeta && unWindow.length) {
        const start = unWindow.slice(-36)[0]?.observation_month;
        const end = unWindow[unWindow.length - 1]?.observation_month;
        if (start && end) sparkMeta.textContent = `${formatMonth(start)} → ${formatMonth(end)}`;
      }
    }
  }

  function formatMonth(ym) {
    if (!ym) return "";
    const [y, m] = String(ym).split("-");
    const names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const idx = parseInt(m, 10) - 1;
    return (idx >= 0 && idx < 12) ? `${names[idx]} ${y}` : String(ym);
  }

  function drawSpark(svg, values, color) {
    if (!svg || !values.length) return;
    const w = 200, h = 36, pad = 2;
    const min = Math.min(...values), max = Math.max(...values);
    const range = max - min || 1;
    const xs = values.map((_, i) => pad + (i / Math.max(1, values.length - 1)) * (w - 2 * pad));
    const ys = values.map(v => pad + (1 - (v - min) / range) * (h - 2 * pad));
    const d = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
    svg.innerHTML = `<path d="${d}" stroke="${color}" stroke-width="1.5" fill="none" stroke-linejoin="round" stroke-linecap="round"/>`;
  }

  /* === TABLE PICKER as pills === */
  function renderTablePicker() {
    const root = $("table-picker");
    root.innerHTML = "";
    for (const t of TABLES) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pill";
      const rows = (DATA[t.key]?.rows ?? []).length;
      b.innerHTML = `<span>${escapeHtml(t.label)}</span><small>${rows.toLocaleString()}</small>`;
      b.dataset.key = t.key;
      if (rows === 0) b.classList.add("empty");
      if (t.key === activeKey) b.classList.add("active");
      b.addEventListener("click", () => { activeKey = t.key; hiddenSeries = new Set(); renderActive(); });
      root.appendChild(b);
    }
  }

  function renderRangePicker() {
    const root = $("range-picker");
    root.innerHTML = "";
    for (const r of RANGES) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.range = r.key;
      b.textContent = r.label;
      if (r.key === activeRange) b.classList.add("active");
      b.addEventListener("click", () => { activeRange = r.key; renderActive(); });
      root.appendChild(b);
    }
  }

  function wireScalePicker() {
    $("scale-picker").querySelectorAll("button").forEach(b => {
      b.addEventListener("click", () => {
        scaleMode = b.dataset.scale;
        $("scale-picker").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
        renderActive();
      });
    });
  }

  function wireV2ChartToggles() {
    const tr = $("toggle-releases");
    if (tr) {
      tr.addEventListener("click", () => {
        showReleases = !showReleases;
        tr.classList.toggle("active", showReleases);
        renderActive();
      });
    }
    const tt = $("toggle-table");
    const wrap = $("data-table-wrap");
    if (tt && wrap) {
      tt.addEventListener("click", () => {
        showTable = !showTable;
        tt.classList.toggle("active", showTable);
        tt.textContent = showTable ? "▤ Hide table" : "▤ Show table";
        wrap.hidden = !showTable;
      });
    }
  }

  function renderActive() {
    document.querySelectorAll("#table-picker .pill").forEach(b => b.classList.toggle("active", b.dataset.key === activeKey));
    document.querySelectorAll("#range-picker button").forEach(b => b.classList.toggle("active", b.dataset.range === activeRange));
    const def = TABLES.find(t => t.key === activeKey);
    if (!def) return;
    const tbl = DATA[activeKey] ?? { columns: [], rows: [] };
    $("chart-title").textContent = def.title;
    $("chart-sub").textContent = def.sub;
    const sortedRows = tbl.rows.slice().sort((a, b) => String(a[def.xField]).localeCompare(String(b[def.xField])));
    const rangedRows = applyRange(sortedRows, def);
    renderChart(def, tbl, rangedRows);
    renderTable(def, tbl);
    renderFootMeta(def, sortedRows, rangedRows);
  }

  function applyRange(rows, def) {
    if (!rows.length) return rows;
    const r = RANGES.find(x => x.key === activeRange);
    if (!r || !r.months) return rows;
    const last = String(rows[rows.length - 1][def.xField] ?? "");
    const lastDate = parseDateLoose(last);
    if (!lastDate) return rows;
    const cutoff = new Date(lastDate.getFullYear(), lastDate.getMonth() - r.months + 1, 1);
    return rows.filter(row => {
      const d = parseDateLoose(String(row[def.xField] ?? ""));
      return d && d >= cutoff;
    });
  }

  function parseDateLoose(s) {
    if (!s) return null;
    if (/^\d{4}-\d{2}$/.test(s)) return new Date(s + "-01T00:00:00");
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function renderFootMeta(def, all, ranged) {
    const a = all[0]?.[def.xField] ?? "—";
    const b = all[all.length - 1]?.[def.xField] ?? "—";
    const ra = ranged[0]?.[def.xField] ?? "—";
    const rb = ranged[ranged.length - 1]?.[def.xField] ?? "—";
    $("range-meta").textContent = `Range · ${ra} → ${rb} (${ranged.length} of ${all.length})`;
    $("series-meta").textContent = `Source · ${def.key} · full ${a} → ${b}`;
  }

  function renderChart(def, tbl, rows) {
    const ctx = $("series-chart");
    if (chart) { chart.destroy(); chart = null; }
    const labels = rows.map(r => r[def.xField] ?? "");
    const cols = def.numeric.filter(col => tbl.columns.includes(col));

    // Build a per-label map of release types for the "Releases" toggle
    const releaseMap = showReleases ? buildReleaseMap(labels) : null;

    const allDatasets = cols.map((col, i) => {
      const color = PALETTE[i % PALETTE.length];
      const raw = rows.map(r => isNumLike(r[col]) ? Number(r[col]) : null);
      const data = transformSeries(raw, scaleMode);
      const ds = {
        label: col, column: col, data, rawData: raw,
        borderColor: color, backgroundColor: color,
        borderWidth: 1.75, pointRadius: 0, pointHoverRadius: 4,
        spanGaps: true, tension: 0.25,
        hidden: hiddenSeries.has(col),
        yAxisID: yAxisIdFor(scaleMode, i),
      };
      // Only the first (highlighted) series gets release markers
      if (releaseMap && i === 0) {
        ds.pointStyle = labels.map((_, j) => {
          const types = releaseMap.get(j);
          if (!types) return "circle";
          if (types.has("statement")) return "rect";
          return "circle";
        });
        ds.pointRadius = labels.map((_, j) => releaseMap.has(j) ? 5 : 0);
        ds.pointHoverRadius = labels.map((_, j) => releaseMap.has(j) ? 8 : 4);
        ds.pointBackgroundColor = labels.map((_, j) => {
          const types = releaseMap.get(j);
          if (!types) return color;
          return types.has("statement") ? color : "white";
        });
        ds.pointBorderColor = color;
        ds.pointBorderWidth = 1.5;
      }
      return ds;
    });

    renderLegend(allDatasets, def);

    if (!allDatasets.length || !rows.length) {
      ctx.style.display = "none";
      $("chart-card").classList.add("is-empty");
      return;
    }
    ctx.style.display = "";
    $("chart-card").classList.remove("is-empty");

    const scales = buildScales(scaleMode, allDatasets);

    chart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets: allDatasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "oklch(0.15 0.012 80 / 0.96)",
            titleFont: { family: "JetBrains Mono", size: 11 },
            bodyFont: { family: "JetBrains Mono", size: 12 },
            padding: 10,
            borderColor: "oklch(0.45 0.08 230)", borderWidth: 1,
            callbacks: {
              afterTitle: (items) => {
                if (!releaseMap || !items?.length) return "";
                const types = releaseMap.get(items[0].dataIndex);
                if (!types) return "";
                const tags = [];
                if (types.has("statement")) tags.push("■ FOMC Statement");
                return tags.join(" · ");
              },
              label: (item) => {
                const ds = allDatasets[item.datasetIndex];
                const raw = ds.rawData[item.dataIndex];
                const shown = item.parsed.y;
                if (scaleMode === "indexed" && raw !== null) return `${ds.label}: ${fmt(shown)} (raw ${fmt(raw)})`;
                return `${ds.label}: ${fmt(shown)}`;
              },
            },
          },
        },
        scales,
      },
    });
  }

  // Map each chart label (YYYY-MM-... etc.) to the set of release types in that month
  function buildReleaseMap(labels) {
    const map = new Map();
    for (let i = 0; i < labels.length; i++) {
      const lbl = String(labels[i] ?? "");
      if (lbl.length < 7) continue;
      const monthKey = lbl.slice(0, 7);
      for (const doc of (DOCS || [])) {
        if (doc.doc_type !== "statement") continue;
        if ((doc.release_date || "").slice(0, 7) === monthKey) {
          if (!map.has(i)) map.set(i, new Set());
          map.get(i).add(doc.doc_type);
        }
      }
    }
    return map;
  }

  function transformSeries(raw, mode) {
    if (mode !== "indexed") return raw;
    const first = raw.find(v => v !== null);
    if (first === undefined || first === 0) return raw;
    return raw.map(v => v === null ? null : (v / first) * 100);
  }

  function yAxisIdFor(mode, i) {
    if (mode === "shared" || mode === "indexed") return "y";
    return `y${i}`;
  }

  function buildScales(mode, datasets) {
    const baseTicks = { color: "oklch(0.460 0.010 80)", font: { family: "JetBrains Mono", size: 10 } };
    const baseGrid  = { color: "oklch(0.910 0.005 80)", drawTicks: false };
    const x = { ticks: { ...baseTicks, maxRotation: 0, autoSkipPadding: 24 }, grid: baseGrid };
    if (mode === "shared") return { x, y: { ticks: baseTicks, grid: baseGrid } };
    if (mode === "indexed") {
      return { x, y: { ticks: { ...baseTicks, callback: (v) => `${v.toFixed(0)}` }, grid: baseGrid,
        title: { display: true, text: "Index = 100 at range start", color: "oklch(0.620 0.008 80)", font: { family: "JetBrains Mono", size: 10 } } } };
    }
    const out = { x };
    let leftCount = 0, rightCount = 0;
    datasets.forEach((ds, i) => {
      const visible = !ds.hidden;
      const side = i % 2 === 0 ? "left" : "right";
      if (visible) (side === "left" ? leftCount++ : rightCount++);
      out[ds.yAxisID] = {
        type: "linear", position: side,
        display: visible && (side === "left" ? leftCount <= 1 : rightCount <= 1),
        ticks: { ...baseTicks, color: ds.borderColor },
        grid: i === 0 ? baseGrid : { drawOnChartArea: false },
      };
    });
    return out;
  }

  function renderLegend(datasets, def) {
    const root = $("chart-legend");
    root.innerHTML = "";
    // Explanatory note for the highlighted series
    const note = $("series-note");
    if (note) {
      if (def && def.highlight && datasets.some(d => d.column === def.highlight)) {
        note.hidden = false;
        note.innerHTML = `
          <span class="ico">i</span>
          <span><strong>${def.highlight}</strong> — the 2-year Treasury yield, used as FedWatcher's market-implied policy signal. The Signal Divergence in §02 compares this rate against the tone-implied path extracted from FOMC language.</span>
        `;
      } else {
        note.hidden = true;
        note.innerHTML = "";
      }
    }
    if (!datasets.length) {
      root.innerHTML = '<span class="series-chip muted"><span class="swatch" style="background:var(--c-line-2)"></span>no numeric series</span>';
      return;
    }
    datasets.forEach((ds) => {
      const last = lastNonNull(ds.rawData);
      const chip = document.createElement("span");
      const isHighlight = def && def.highlight === ds.column;
      chip.className = "series-chip" + (ds.hidden ? " muted" : "") + (isHighlight ? " highlight" : "");
      chip.innerHTML = `<span class="swatch" style="background:${ds.borderColor}"></span>${ds.label}<span class="v">${last === null ? "—" : fmt(last)}</span>`;
      chip.addEventListener("click", () => {
        if (hiddenSeries.has(ds.label)) hiddenSeries.delete(ds.label); else hiddenSeries.add(ds.label);
        renderActive();
      });
      root.appendChild(chip);
    });
  }

  function lastNonNull(arr) {
    for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null && arr[i] !== undefined) return arr[i];
    return null;
  }

  function renderTable(def, tbl) {
    const head = $("table-head"); const body = $("table-body");
    head.innerHTML = ""; body.innerHTML = "";
    if (!tbl.columns.length) {
      body.innerHTML = `<tr><td class="empty" colspan="1">No schema yet.</td></tr>`;
      setRowCount(0); return;
    }
    // Pick the columns to display (def.displayColumns wins) and intersect with what the table actually has
    const displayCols = (def.displayColumns || tbl.columns).filter(c => tbl.columns.includes(c));
    for (const c of displayCols) {
      const th = document.createElement("th");
      th.textContent = c;
      if (def.numeric.includes(c) || c === "id") th.classList.add("num");
      if (def.highlightTable && def.highlight === c) th.classList.add("col-highlight");
      head.appendChild(th);
    }
    const filter = ($("table-filter").value || "").toLowerCase();
    const rows = tbl.rows.slice().reverse()
      .filter(r => !filter || displayCols.some(c => String(r[c] ?? "").toLowerCase().includes(filter)));
    setRowCount(rows.length);
    setTableTitle(def.key);
    if (!rows.length) {
      body.innerHTML = `<tr><td class="empty" colspan="${displayCols.length}">No rows ${filter ? "match filter" : "yet"}.</td></tr>`;
      return;
    }
    for (const r of rows) {
      const tr = document.createElement("tr");
      for (const c of displayCols) {
        const td = document.createElement("td");
        const v = r[c];
        td.textContent = fmt(v);
        if (def.numeric.includes(c) || c === "id" || c.endsWith("_id")) td.classList.add("num");
        if (def.highlightTable && def.highlight === c) td.classList.add("col-highlight");
        tr.appendChild(td);
      }
      tr.addEventListener("click", () => openModal(def, tbl, r));
      body.appendChild(tr);
    }
  }
  function setRowCount(n) {
    const t = $("row-count-text"); if (t) t.textContent = String(n);
    const b = $("row-count-badge"); if (b) b.textContent = `${n} rows`;
  }
  function setTableTitle(key) {
    const t = $("table-title"); if (t) t.textContent = key;
  }

  function openModal(def, tbl, row) {
    $("modal-eyebrow").textContent = def.key;
    const id = row[def.xField] ?? row.id ?? "row";
    $("modal-title").textContent = `${def.label} · ${id}`;
    const body = $("modal-body"); body.innerHTML = "";
    const dl = document.createElement("dl"); dl.className = "kv-grid";
    for (const c of tbl.columns) {
      const dt = document.createElement("dt"); dt.textContent = c;
      const dd = document.createElement("dd");
      const v = row[c];
      if (v === null || v === undefined || v === "") { dd.textContent = "null"; dd.classList.add("null"); }
      else dd.textContent = fmt(v);
      dl.appendChild(dt); dl.appendChild(dd);
    }
    body.appendChild(dl);
    $("row-modal").hidden = false;
    document.body.style.overflow = "hidden";
  }
  function wireModal() {
    const m = $("row-modal");
    m.addEventListener("click", (e) => { if (e.target.dataset.close !== undefined) closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !m.hidden) closeModal(); });
    const filter = $("table-filter");
    if (filter) filter.addEventListener("input", () => renderActive());
  }
  function closeModal() {
    const m = $("row-modal"); m.hidden = true;
    const panel = m.querySelector(".modal-panel"); if (panel) panel.classList.remove("modal-doc");
    document.body.style.overflow = "";
  }

  /* === DOCUMENT FEED (from v1) === */
  const DOC_TYPE_LABEL = { statement: "FOMC Statement" };
  const DOC_TYPE_SHORT = { statement: "Statement" };

  function wireFeed() {
    const ftr = $("feed-filter");
    if (ftr) ftr.querySelectorAll("button").forEach(b => {
      b.addEventListener("click", () => {
        docFilter = b.dataset.feed;
        ftr.querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
        renderFeed();
      });
    });
    const s = $("feed-search");
    if (s) s.addEventListener("input", () => { docQuery = s.value.toLowerCase(); renderFeed(); });
  }

  function wireSourceSwitch() {
    const root = $("source-switch"); if (!root) return;
    const fakeBtn = root.querySelector('[data-source="fakefed"]');
    if (!fakeBtn) return;
    fakeBtn.addEventListener("click", async () => {
      if (fakeFedEnabled) {
        fakeFedEnabled = false;
        DOCS = OFFICIAL_DOCS.slice();
        setSourceSwitchState(); renderFeed(); return;
      }
      fakeBtn.classList.add("loading"); fakeBtn.textContent = "Loading...";
      try {
        if (!fakeFedLoaded) {
          const response = await fetch(URL_FAKEFED, { cache: "no-store" });
          FAKEFED_DOCS = response.ok ? await response.json() : [];
          fakeFedLoaded = true;
        }
        fakeFedEnabled = true;
        DOCS = mergeDocuments(OFFICIAL_DOCS, FAKEFED_DOCS);
        setSourceSwitchState();
        docFilter = "all"; docQuery = "";
        const search = $("feed-search"); if (search) search.value = "";
        const filter = $("feed-filter");
        if (filter) filter.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.feed === "all"));
        renderFeed();
      } finally { fakeBtn.classList.remove("loading"); fakeBtn.textContent = "FakeFed"; }
    });
  }

  function renderFeed() {
    const root = $("doc-feed"); const count = $("feed-count");
    if (!root) return;
    root.innerHTML = "";
    const filtered = DOCS.filter(d => {
      if (d.doc_type === "minutes") return false;
      if (docFilter !== "all" && d.doc_type !== docFilter) return false;
      if (!docQuery) return true;
      const hay = `${d.release_date} ${d.doc_type} ${d.central_bank} ${d.raw_text || ""}`.toLowerCase();
      return hay.includes(docQuery);
    });
    if (count) count.textContent = `${filtered.length} doc${filtered.length === 1 ? "" : "s"}`;
    if (!filtered.length) { root.innerHTML = `<div class="doc-empty">No documents ${docQuery ? "match search" : "yet"}.</div>`; return; }
    for (const d of filtered) {
      const row = document.createElement("div");
      row.className = "doc-row";
      const dateLabel = formatReleaseDate(d.release_date);
      const typeLabel = DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type;
      const typeShort = DOC_TYPE_SHORT[d.doc_type] ?? (d.doc_type || "Document");
      const sourceLabel = sourceLabelFor(d);
      const sourceClass = sourceLabel === "FakeFed" ? "fakefed" : "fed";
      const preview = (d.raw_text || "").replace(/\s+/g, " ").trim().slice(0, 360);
      const sizeKb = d.raw_text ? `${Math.max(1, Math.round(d.raw_text.length / 1024))} KB` : "—";
      row.innerHTML = `
        <div class="date">${dateLabel.short}<small>${dateLabel.year}</small></div>
        <div class="doc-main">
          <div class="doc-title-line">
            <strong>${typeLabel}</strong>
            <span class="doc-chip doc-chip-${escapeHtml(d.doc_type || "document")}">${escapeHtml(typeShort)}</span>
            <span class="doc-chip doc-chip-${sourceClass}">${sourceLabel}</span>
          </div>
          <div class="preview">${escapeHtml(preview) || "<em>no text captured</em>"}</div>
        </div>
        <div class="meta"><span>${sizeKb}</span><span>${escapeHtml(sourceLabel)}</span></div>
        <div class="ext" title="View detail">→</div>
      `;
      row.addEventListener("click", () => openDocModal(d));
      root.appendChild(row);
    }
  }

  function openDocModal(d) {
    const m = $("row-modal");
    const panel = m.querySelector(".modal-panel"); panel.classList.add("modal-doc");
    $("modal-eyebrow").textContent = `${d.central_bank ?? "FED"} · ${(d.doc_type || "").toUpperCase()}`;
    const dateLabel = formatReleaseDate(d.release_date);
    $("modal-title").textContent = `${DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type} — ${dateLabel.long}`;
    const body = $("modal-body"); body.innerHTML = "";
    const meta = document.createElement("dl"); meta.className = "kv-grid";
    const fields = [
      ["release date", d.release_date ?? "—"],
      ["type", DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type ?? "—"],
      ["central bank", d.central_bank ?? "—"],
      ["raw text length", d.raw_text ? `${d.raw_text.length.toLocaleString()} chars` : "—"],
      ["processed", d.processed ? "yes" : "no"],
      ["source id", d.source_id ?? "—"],
    ];
    for (const [k, v] of fields) {
      const dt = document.createElement("dt"); dt.textContent = k;
      const dd = document.createElement("dd"); dd.textContent = v;
      meta.appendChild(dt); meta.appendChild(dd);
    }
    body.appendChild(meta);
    const actions = document.createElement("div"); actions.className = "doc-actions";
    if (d.url) {
      const a = document.createElement("a");
      a.className = "btn btn-accent"; a.href = d.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.innerHTML = `Open on federalreserve.gov <span class="arrow">↗</span>`;
      actions.appendChild(a);
    }
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn btn-secondary btn-sm"; copyBtn.type = "button"; copyBtn.textContent = "Copy text";
    copyBtn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(d.raw_text || ""); copyBtn.textContent = "Copied"; setTimeout(() => (copyBtn.textContent = "Copy text"), 1200); }
      catch (_) { copyBtn.textContent = "Copy failed"; }
    });
    actions.appendChild(copyBtn);
    body.appendChild(actions);
    if (d.raw_text) {
      const pre = document.createElement("div"); pre.className = "doc-text"; pre.textContent = d.raw_text;
      body.appendChild(pre);
    }
    m.hidden = false; document.body.style.overflow = "hidden";
  }

  function formatReleaseDate(iso) {
    if (!iso) return { short: "—", year: "", long: "—" };
    const d = new Date(iso + "T12:00:00");
    if (isNaN(d.getTime())) return { short: iso, year: "", long: iso };
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const short = `${months[d.getMonth()]} ${d.getDate()}`;
    const long = `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
    return { short, year: String(d.getFullYear()), long };
  }
  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }
  function mergeDocuments(primary, secondary) {
    const byKey = new Map();
    for (const d of [...primary, ...secondary]) {
      const key = d.url || d.source_id || `${d.central_bank}-${d.doc_type}-${d.release_date}`;
      byKey.set(key, d);
    }
    return [...byKey.values()].sort((a, b) => String(b.release_date || "").localeCompare(String(a.release_date || "")));
  }
  function setSourceSwitchState() {
    const root = $("source-switch"); if (!root) return;
    const fakeBtn = root.querySelector('[data-source="fakefed"]'); if (!fakeBtn) return;
    fakeBtn.classList.toggle("active", fakeFedEnabled);
    fakeBtn.classList.toggle("badge-neg", fakeFedEnabled);
    fakeBtn.classList.toggle("badge-outline", !fakeFedEnabled);
    fakeBtn.setAttribute("aria-pressed", fakeFedEnabled ? "true" : "false");
  }
  function sourceLabelFor(d) {
    const url = String(d.url || d.source_id || "").toLowerCase();
    const bank = String(d.central_bank || "").toLowerCase();
    if (url.includes("fakefed") || bank.includes("fakefed")) return "FakeFed";
    return d.central_bank || "FED";
  }

  load();
})();
