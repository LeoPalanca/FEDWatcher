(() => {
  "use strict";

  const TABLE_OVERRIDES = {
    macro_data: { label: "Macro", numeric: ["core_cpi_index","core_cpi_mom","core_cpi_yoy","unemployment_rate","us2y_yield"], xField: "observation_month", title: "Core CPI · Unemployment · 2Y Yield", sub: "Monthly observations · FRED" },
    documents: { label: "Documents", numeric: [], xField: "release_date", title: "FOMC Documents", sub: "Statements & minutes" },
    market_data: { label: "Markets", numeric: ["sofr_rate","ois_1m","ois_3m","ois_6m","ois_1y","ois_2y","us2y_yield"], xField: "timestamp", title: "Market Implied Rates", sub: "SOFR / OIS / 2Y" },
    sentiment: { label: "Sentiment", numeric: ["tone_score","confidence"], xField: "created_at", title: "Document Sentiment", sub: "Tone score & confidence" },
    signals: { label: "Signals", numeric: ["tone_implied_next_rate","market_implied_next_rate","divergence"], xField: "created_at", title: "Tone vs Market", sub: "Divergence signals" },
  };

  const PALETTE = [
    "oklch(0.45 0.08 230)",
    "oklch(0.52 0.13 25)",
    "oklch(0.50 0.09 155)",
    "oklch(0.62 0.10 75)",
    "oklch(0.42 0.012 80)",
    "oklch(0.55 0.10 290)",
    "oklch(0.55 0.10 200)",
  ];

  const RANGES = [
    { key: "1Y", label: "1Y", months: 12 },
    { key: "5Y", label: "5Y", months: 60 },
    { key: "10Y", label: "10Y", months: 120 },
    { key: "MAX", label: "Max", months: null },
  ];

  const $ = (id) => document.getElementById(id);
  const fmt = (v) => v === null || v === undefined || v === "" ? "—" : (typeof v === "number" ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v));
  const isNumLike = (v) => v !== null && v !== undefined && v !== "" && !isNaN(Number(v));

  let DATA = null;
  let TABLES = [];
  let activeKey = "macro_data";
  let activeRange = "10Y";
  let scaleMode = "multi"; // multi | indexed | shared
  let chart = null;
  let hiddenSeries = new Set();

  let DOCS = [];
  let OFFICIAL_DOCS = [];
  let FAKEFED_DOCS = [];
  let docFilter = "all";
  let docQuery = "";
  let fakeFedLoaded = false;
  let fakeFedEnabled = false;

  async function load() {
    try {
      const [d, docs] = await Promise.all([
        fetchJson("/api/snapshot", {}),
        fetchJson("/api/documents?limit=1000", []),
      ]);
      DATA = d;
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
    renderDbOverview();
    renderTablePicker();
    renderRangePicker();
    wireScalePicker();
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
      console.error(`API unavailable for ${url}.`, e);
    }
    return fallbackValue;
  }

  function normalizeDocumentsPayload(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.rows)) return payload.rows;
    return [];
  }

  function buildTableDefinitions(data) {
    return Object.keys(data || {}).sort((a, b) => {
      const priority = ["macro_data", "documents", "sentiment", "signals", "market_data", "fomc_policy_moves"];
      const ai = priority.indexOf(a);
      const bi = priority.indexOf(b);
      if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
      return a.localeCompare(b);
    }).map(key => {
      const tbl = data[key] ?? { columns: [], rows: [] };
      const override = TABLE_OVERRIDES[key] ?? {};
      const numeric = override.numeric ?? inferNumericColumns(tbl);
      const xField = override.xField ?? inferXField(tbl);
      return {
        key,
        label: override.label ?? humanizeTableName(key),
        numeric,
        xField,
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
      const cpiUnit = yoy !== null ? "%" : "";
      const heroCpi = $("hero-cpi");
      if (heroCpi) heroCpi.firstChild.textContent = `${cpiText}${cpiUnit} `;
      const m = $("metric-cpi");
      if (m) m.innerHTML = `${cpiText}<span class="unit">${cpiUnit || "·idx"}</span>`;
      const dEl = $("metric-cpi-d");
      if (dEl) {
        const prevIdx = rows.findIndex(r => r === cpiRow) - 1;
        if (prevIdx >= 0 && isNumLike(rows[prevIdx].core_cpi_yoy) && yoy !== null) {
          const d = yoy - Number(rows[prevIdx].core_cpi_yoy);
          dEl.textContent = `${d >= 0 ? "+" : ""}${d.toFixed(2)} pp`;
          dEl.parentElement.classList.toggle("neg", d > 0);
          dEl.parentElement.classList.toggle("pos", d < 0);
        }
      }
    }
    if (unRow) {
      const u = Number(unRow.unemployment_rate);
      const heroUn = $("hero-un");
      if (heroUn) heroUn.firstChild.textContent = `${u.toFixed(1)}% `;
      const m = $("metric-un");
      if (m) m.innerHTML = `${u.toFixed(1)}<span class="unit">%</span>`;
      const dEl = $("metric-un-d");
      if (dEl) {
        const prevIdx = rows.findIndex(r => r === unRow) - 1;
        if (prevIdx >= 0 && isNumLike(rows[prevIdx].unemployment_rate)) {
          const d = u - Number(rows[prevIdx].unemployment_rate);
          dEl.textContent = `${d >= 0 ? "+" : ""}${d.toFixed(1)} pp`;
          dEl.parentElement.classList.toggle("neg", d > 0);
          dEl.parentElement.classList.toggle("pos", d < 0);
        }
      }
    }
  }

  function renderTablePicker() {
    const root = $("table-picker");
    root.innerHTML = "";
    for (const t of TABLES) {
      const b = document.createElement("button");
      b.type = "button";
      const rows = (DATA[t.key]?.rows ?? []).length;
      b.innerHTML = `<span>${escapeHtml(t.label)}</span><small>${rows.toLocaleString()}</small>`;
      b.dataset.key = t.key;
      if (rows === 0) {
        b.title = "No rows yet";
      }
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

  function renderActive() {
    document.querySelectorAll("#table-picker button").forEach(b => b.classList.toggle("active", b.dataset.key === activeKey));
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

    const allDatasets = cols.map((col, i) => {
      const color = PALETTE[i % PALETTE.length];
      const raw = rows.map(r => isNumLike(r[col]) ? Number(r[col]) : null);
      const data = transformSeries(raw, scaleMode);
      return {
        label: col,
        column: col,
        data,
        rawData: raw,
        borderColor: color,
        backgroundColor: color,
        borderWidth: 1.75,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: true,
        tension: 0.25,
        hidden: hiddenSeries.has(col),
        yAxisID: yAxisIdFor(scaleMode, i, cols.length),
      };
    });

    renderLegend(allDatasets);

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
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "oklch(0.15 0.012 80 / 0.96)",
            titleFont: { family: "JetBrains Mono", size: 11 },
            bodyFont: { family: "JetBrains Mono", size: 12 },
            padding: 10,
            borderColor: "oklch(0.45 0.08 230)",
            borderWidth: 1,
            callbacks: {
              label: (item) => {
                const ds = allDatasets[item.datasetIndex];
                const raw = ds.rawData[item.dataIndex];
                const shown = item.parsed.y;
                if (scaleMode === "indexed" && raw !== null) {
                  return `${ds.label}: ${fmt(shown)} (raw ${fmt(raw)})`;
                }
                return `${ds.label}: ${fmt(shown)}`;
              },
            },
          },
        },
        scales,
      },
    });
  }

  function transformSeries(raw, mode) {
    if (mode !== "indexed") return raw;
    const first = raw.find(v => v !== null);
    if (first === undefined || first === 0) return raw;
    return raw.map(v => v === null ? null : (v / first) * 100);
  }

  function yAxisIdFor(mode, i, total) {
    if (mode === "shared") return "y";
    if (mode === "indexed") return "y";
    // multi
    return `y${i}`;
  }

  function buildScales(mode, datasets) {
    const baseTicks = { color: "oklch(0.460 0.010 80)", font: { family: "JetBrains Mono", size: 10 } };
    const baseGrid  = { color: "oklch(0.910 0.005 80)", drawTicks: false };
    const x = {
      ticks: { ...baseTicks, maxRotation: 0, autoSkipPadding: 24 },
      grid: baseGrid,
    };
    if (mode === "shared") {
      return { x, y: { ticks: baseTicks, grid: baseGrid } };
    }
    if (mode === "indexed") {
      return {
        x,
        y: {
          ticks: { ...baseTicks, callback: (v) => `${v.toFixed(0)}` },
          grid: baseGrid,
          title: { display: true, text: "Index = 100 at range start", color: "oklch(0.620 0.008 80)", font: { family: "JetBrains Mono", size: 10 } },
        },
      };
    }
    // multi: each visible dataset gets its own axis, alternating left/right, hidden when more than 2
    const out = { x };
    let leftCount = 0, rightCount = 0;
    datasets.forEach((ds, i) => {
      const visible = !ds.hidden;
      const side = i % 2 === 0 ? "left" : "right";
      if (visible) (side === "left" ? leftCount++ : rightCount++);
      out[ds.yAxisID] = {
        type: "linear",
        position: side,
        display: visible && (side === "left" ? leftCount <= 1 : rightCount <= 1),
        ticks: { ...baseTicks, color: ds.borderColor },
        grid: i === 0 ? baseGrid : { drawOnChartArea: false },
      };
    });
    return out;
  }

  function renderLegend(datasets) {
    const root = $("chart-legend");
    root.innerHTML = "";
    if (!datasets.length) {
      root.innerHTML = '<span class="series-chip muted"><span class="swatch" style="background:var(--c-line-2)"></span>no numeric series</span>';
      return;
    }
    datasets.forEach((ds) => {
      const last = lastNonNull(ds.rawData);
      const chip = document.createElement("span");
      chip.className = "series-chip" + (ds.hidden ? " muted" : "");
      chip.title = ds.hidden ? "Show series" : "Hide series";
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
    const head = $("table-head");
    const body = $("table-body");
    head.innerHTML = "";
    body.innerHTML = "";

    if (!tbl.columns.length) {
      body.innerHTML = `<tr><td class="empty" colspan="1">No schema yet.</td></tr>`;
      setRowCount(0);
      return;
    }

    for (const c of tbl.columns) {
      const th = document.createElement("th");
      th.textContent = c;
      if (def.numeric.includes(c) || c === "id") th.classList.add("num");
      head.appendChild(th);
    }

    const filter = ($("table-filter").value || "").toLowerCase();
    const rows = tbl.rows
      .slice()
      .reverse()
      .filter(r => !shouldHideRow(def, r))
      .filter(r => !filter || tbl.columns.some(c => String(r[c] ?? "").toLowerCase().includes(filter)));

    setRowCount(rows.length);
    setTableTitle(def.key);

    if (!rows.length) {
      body.innerHTML = `<tr><td class="empty" colspan="${tbl.columns.length}">No rows ${filter ? "match filter" : "yet"}.</td></tr>`;
      return;
    }

    for (const r of rows) {
      const tr = document.createElement("tr");
      const interpolatedFields = macroInterpolatedFields(r);
      if (def.key === "macro_data" && interpolatedFields.size) tr.classList.add("has-interpolated");
      for (const c of tbl.columns) {
        const td = document.createElement("td");
        const v = r[c];
        td.textContent = fmt(v);
        if (def.numeric.includes(c) || c === "id" || c.endsWith("_id")) td.classList.add("num");
        if (shouldHighlightMissing(def, c, v, r)) {
          td.classList.add("missing-value");
          td.title = "Missing value";
        }
        if (def.key === "macro_data" && interpolatedFields.has(c)) {
          td.classList.add("interpolated-value");
          td.title = "Interpolated from adjacent months";
        }
        if (def.key === "macro_data" && c === "interpolated_fields" && v) {
          td.classList.add("quality-note");
          td.textContent = `filled: ${String(v).replaceAll(",", ", ")}`;
        }
        tr.appendChild(td);
      }
      tr.addEventListener("click", () => openModal(def, tbl, r));
      body.appendChild(tr);
    }
  }

  function setRowCount(n) {
    const t = $("row-count-text");
    if (t) t.textContent = String(n);
    const b = $("row-count-badge");
    if (b) b.textContent = `${n} rows`;
  }
  function setTableTitle(key) {
    const t = $("table-title");
    if (t) t.textContent = key;
  }

  function shouldHighlightMissing(def, column, value, row) {
    if (value !== null && value !== undefined && value !== "") return false;
    if (def.key !== "macro_data") return false;
    if (["core_cpi_index", "unemployment_rate", "us2y_yield"].includes(column)) return true;
    if (["core_cpi_mom", "core_cpi_yoy"].includes(column)) return !hasValue(row?.core_cpi_index);
    return false;
  }

  function shouldHideRow(def, row) {
    if (def.key !== "macro_data") return false;
    return ["core_cpi_index", "unemployment_rate", "us2y_yield"].every(field => !hasValue(row[field]));
  }

  function hasValue(value) {
    return value !== null && value !== undefined && value !== "";
  }

  function macroInterpolatedFields(row) {
    const raw = String(row?.interpolated_fields || "").trim();
    if (!raw) return new Set();
    return new Set(raw.split(",").map(field => field.trim()).filter(Boolean));
  }

  function openModal(def, tbl, row) {
    $("modal-eyebrow").textContent = def.key;
    const id = row[def.xField] ?? row.id ?? "row";
    $("modal-title").textContent = `${def.label} · ${id}`;
    const body = $("modal-body");
    body.innerHTML = "";
    const dl = document.createElement("dl");
    dl.className = "kv-grid";
    for (const c of tbl.columns) {
      const dt = document.createElement("dt");
      dt.textContent = c;
      const dd = document.createElement("dd");
      const v = row[c];
      if (v === null || v === undefined || v === "") {
        dd.textContent = "null";
        dd.classList.add("null");
      } else {
        dd.textContent = fmt(v);
      }
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
    $("table-filter").addEventListener("input", () => renderActive());
  }

  function closeModal() {
    const m = $("row-modal");
    m.hidden = true;
    const panel = m.querySelector(".modal-panel");
    if (panel) panel.classList.remove("modal-doc");
    document.body.style.overflow = "";
  }

  /* === DOCUMENT FEED === */
  const DOC_TYPE_LABEL = { statement: "FOMC Statement", minutes: "FOMC Minutes" };
  const DOC_TYPE_SHORT = { statement: "Statement", minutes: "Minutes" };

  function wireFeed() {
    const ftr = $("feed-filter");
    if (ftr) {
      ftr.querySelectorAll("button").forEach(b => {
        b.addEventListener("click", () => {
          docFilter = b.dataset.feed;
          ftr.querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
          renderFeed();
        });
      });
    }
    const s = $("feed-search");
    if (s) s.addEventListener("input", () => { docQuery = s.value.toLowerCase(); renderFeed(); });
  }

  function wireSourceSwitch() {
    const root = $("source-switch");
    if (!root) return;
    const fakeBtn = root.querySelector('[data-source="fakefed"]');

    if (fakeBtn) {
      fakeBtn.addEventListener("click", async () => {
        if (fakeFedEnabled) {
          fakeFedEnabled = false;
          DOCS = OFFICIAL_DOCS.slice();
          setSourceSwitchState();
          renderFeed();
          scrollFeedIntoView();
          return;
        }

        fakeBtn.classList.add("loading");
        fakeBtn.textContent = "Loading...";
        try {
          if (!fakeFedLoaded) {
            const response = await fetch("/assets/fakefed-documents.json", { cache: "no-store" });
            FAKEFED_DOCS = response.ok ? await response.json() : [];
            fakeFedLoaded = true;
          }
          fakeFedEnabled = true;
          DOCS = mergeDocuments(OFFICIAL_DOCS, FAKEFED_DOCS);
          setSourceSwitchState();
          docFilter = "all";
          docQuery = "";
          const search = $("feed-search");
          if (search) search.value = "";
          const filter = $("feed-filter");
          if (filter) filter.querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset.feed === "all"));
          renderFeed();
          scrollFeedIntoView();
        } finally {
          fakeBtn.classList.remove("loading");
          fakeBtn.textContent = "FakeFed";
        }
      });
    }
  }

  function renderFeed() {
    const root = $("doc-feed");
    const count = $("feed-count");
    if (!root) return;
    root.innerHTML = "";

    const filtered = DOCS.filter(d => {
      if (docFilter !== "all" && d.doc_type !== docFilter) return false;
      if (!docQuery) return true;
      const hay = `${d.release_date} ${d.doc_type} ${d.central_bank} ${d.raw_text || ""}`.toLowerCase();
      return hay.includes(docQuery);
    });

    if (count) count.textContent = `${filtered.length} doc${filtered.length === 1 ? "" : "s"}`;

    if (!filtered.length) {
      root.innerHTML = `<div class="doc-empty">No documents ${docQuery ? "match search" : "yet"}.</div>`;
      return;
    }

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
    const panel = m.querySelector(".modal-panel");
    panel.classList.add("modal-doc");
    $("modal-eyebrow").textContent = `${d.central_bank ?? "FED"} · ${(d.doc_type || "").toUpperCase()}`;
    const dateLabel = formatReleaseDate(d.release_date);
    $("modal-title").textContent = `${DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type} — ${dateLabel.long}`;
    const body = $("modal-body");
    body.innerHTML = "";

    const meta = document.createElement("dl");
    meta.className = "kv-grid";
    const fields = [
      ["release date", d.release_date ?? "—"],
      ["type", DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type ?? "—"],
      ["central bank", d.central_bank ?? "—"],
      ["raw text length", d.raw_text ? `${d.raw_text.length.toLocaleString()} chars` : "—"],
      ["processed", d.processed ? "yes" : "no"],
      ["source id", d.source_id ?? "—"],
    ];
    for (const [k, v] of fields) {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      meta.appendChild(dt); meta.appendChild(dd);
    }
    body.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "doc-actions";
    if (d.url) {
      const a = document.createElement("a");
      a.className = "btn btn-accent";
      a.href = d.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.innerHTML = `Open on federalreserve.gov <span class="arrow">↗</span>`;
      actions.appendChild(a);
    }
    const copyBtn = document.createElement("button");
    copyBtn.className = "btn btn-secondary btn-sm";
    copyBtn.type = "button";
    copyBtn.textContent = "Copy text";
    copyBtn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(d.raw_text || ""); copyBtn.textContent = "Copied"; setTimeout(() => (copyBtn.textContent = "Copy text"), 1200); } catch (_) { copyBtn.textContent = "Copy failed"; }
    });
    actions.appendChild(copyBtn);
    body.appendChild(actions);

    if (d.raw_text) {
      const pre = document.createElement("div");
      pre.className = "doc-text";
      pre.textContent = d.raw_text;
      body.appendChild(pre);
    } else {
      const note = document.createElement("p");
      note.style.color = "var(--c-ink-3)";
      note.style.marginTop = "var(--s-5)";
      note.textContent = "No raw text captured for this document.";
      body.appendChild(note);
    }

    m.hidden = false;
    document.body.style.overflow = "hidden";
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
    const root = $("source-switch");
    if (!root) return;
    const fakeBtn = root.querySelector('[data-source="fakefed"]');
    if (!fakeBtn) return;
    fakeBtn.classList.toggle("active", fakeFedEnabled);
    fakeBtn.classList.toggle("badge-neg", fakeFedEnabled);
    fakeBtn.classList.toggle("badge-outline", !fakeFedEnabled);
    fakeBtn.setAttribute("aria-pressed", fakeFedEnabled ? "true" : "false");
    fakeBtn.title = fakeFedEnabled ? "Hide FakeFed documents" : "Show FakeFed documents";
  }

  function scrollFeedIntoView() {
    const feed = $("feed");
    if (feed) feed.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function sourceLabelFor(d) {
    const url = String(d.url || d.source_id || "").toLowerCase();
    const bank = String(d.central_bank || "").toLowerCase();
    if (url.includes("fakefed") || bank.includes("fakefed")) return "FakeFed";
    return d.central_bank || "FED";
  }

  load();
})();
