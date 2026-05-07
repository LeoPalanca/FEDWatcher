(() => {
  "use strict";

  const TABLES = [
    { key: "macro_data",   label: "Macro",     numeric: ["core_cpi_index","core_cpi_mom","core_cpi_yoy","unemployment_rate","us2y_yield"], xField: "observation_month", title: "Core CPI · Unemployment · 2Y Yield", sub: "Monthly observations · FRED" },
    { key: "documents",    label: "Documents", numeric: [], xField: "release_date", title: "FOMC Documents", sub: "Statements & minutes" },
    { key: "market_data",  label: "Markets",   numeric: ["sofr_rate","ois_1m","ois_3m","ois_6m","ois_1y","ois_2y","us2y_yield"], xField: "timestamp", title: "Market Implied Rates", sub: "SOFR / OIS / 2Y" },
    { key: "sentiment",    label: "Sentiment", numeric: ["tone_score","confidence"], xField: "created_at", title: "Document Sentiment", sub: "Tone score & confidence" },
    { key: "signals",      label: "Signals",   numeric: ["tone_implied_next_rate","market_implied_next_rate","divergence"], xField: "created_at", title: "Tone vs Market", sub: "Divergence signals" },
  ];

  const PALETTE = [
    "oklch(0.45 0.08 230)",   // accent
    "oklch(0.52 0.13 25)",    // neg
    "oklch(0.50 0.09 155)",   // pos
    "oklch(0.62 0.10 75)",    // warn
    "oklch(0.42 0.012 80)",   // dark neutral
  ];

  const $ = (id) => document.getElementById(id);
  const fmt = (v) => v === null || v === undefined || v === "" ? "—" : (typeof v === "number" ? Number(v).toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v));
  const isNumLike = (v) => v !== null && v !== undefined && v !== "" && !isNaN(Number(v));

  let DATA = null;
  let activeKey = "macro_data";
  let chart = null;
  let hiddenSeries = new Set();

  async function load() {
    try {
      const res = await fetch("/assets/data.json", { cache: "no-store" });
      DATA = await res.json();
    } catch (e) {
      DATA = {};
      console.error("data load failed", e);
    }
    renderPicker();
    renderActive();
    wireModal();
  }

  function renderPicker() {
    const root = $("table-picker");
    root.innerHTML = "";
    for (const t of TABLES) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = t.label;
      b.dataset.key = t.key;
      const rows = (DATA[t.key]?.rows ?? []).length;
      if (rows === 0) {
        b.disabled = true;
        b.title = "No rows yet";
      }
      if (t.key === activeKey) b.classList.add("active");
      b.addEventListener("click", () => { activeKey = t.key; hiddenSeries = new Set(); renderActive(); });
      root.appendChild(b);
    }
  }

  function renderActive() {
    document.querySelectorAll("#table-picker button").forEach(b => b.classList.toggle("active", b.dataset.key === activeKey));
    const def = TABLES.find(t => t.key === activeKey);
    const tbl = DATA[activeKey] ?? { columns: [], rows: [] };
    $("chart-title").textContent = def.title;
    $("chart-sub").textContent = def.sub;
    renderChart(def, tbl);
    renderTable(def, tbl);
  }

  function renderChart(def, tbl) {
    const ctx = $("series-chart");
    if (chart) { chart.destroy(); chart = null; }
    const rows = tbl.rows.slice().sort((a, b) => String(a[def.xField]).localeCompare(String(b[def.xField])));
    const labels = rows.map(r => r[def.xField] ?? "");
    const series = def.numeric
      .filter(col => tbl.columns.includes(col))
      .map((col, i) => ({
        label: col,
        data: rows.map(r => isNumLike(r[col]) ? Number(r[col]) : null),
        borderColor: PALETTE[i % PALETTE.length],
        backgroundColor: PALETTE[i % PALETTE.length],
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        spanGaps: true,
        tension: 0.25,
        hidden: hiddenSeries.has(col),
      }));

    renderLegend(series);

    if (!series.length || !rows.length) {
      ctx.style.display = "none";
      return;
    }
    ctx.style.display = "";

    chart = new Chart(ctx, {
      type: "line",
      data: { labels, datasets: series },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "oklch(0.15 0.012 80 / 0.95)",
            titleFont: { family: "JetBrains Mono", size: 11 },
            bodyFont: { family: "JetBrains Mono", size: 12 },
            padding: 10,
            borderColor: "oklch(0.45 0.08 230)",
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            ticks: { color: "oklch(0.460 0.010 80)", font: { family: "JetBrains Mono", size: 10 }, maxRotation: 0, autoSkipPadding: 24 },
            grid: { color: "oklch(0.910 0.005 80)", drawTicks: false },
          },
          y: {
            ticks: { color: "oklch(0.460 0.010 80)", font: { family: "JetBrains Mono", size: 10 } },
            grid: { color: "oklch(0.910 0.005 80)", drawTicks: false },
          },
        },
      },
    });
  }

  function renderLegend(series) {
    const root = $("chart-legend");
    root.innerHTML = "";
    if (!series.length) {
      root.innerHTML = '<span class="ent muted"><span class="l" style="background:var(--c-line-2)"></span>no numeric series</span>';
      return;
    }
    series.forEach((s, i) => {
      const e = document.createElement("span");
      e.className = "ent" + (hiddenSeries.has(s.label) ? " muted" : "");
      e.innerHTML = `<span class="l" style="background:${s.borderColor}"></span>${s.label}`;
      e.addEventListener("click", () => {
        if (hiddenSeries.has(s.label)) hiddenSeries.delete(s.label); else hiddenSeries.add(s.label);
        renderActive();
      });
      root.appendChild(e);
    });
  }

  function renderTable(def, tbl) {
    const head = $("table-head");
    const body = $("table-body");
    head.innerHTML = "";
    body.innerHTML = "";

    if (!tbl.columns.length) {
      body.innerHTML = `<tr><td class="empty" colspan="1">No schema yet.</td></tr>`;
      $("row-count").querySelector(".n").textContent = "0";
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
      .filter(r => !filter || tbl.columns.some(c => String(r[c] ?? "").toLowerCase().includes(filter)));

    if (!rows.length) {
      body.innerHTML = `<tr><td class="empty" colspan="${tbl.columns.length}">No rows ${filter ? "match filter" : "yet"}.</td></tr>`;
      $("row-count").querySelector(".n").textContent = "0";
      return;
    }

    for (const r of rows) {
      const tr = document.createElement("tr");
      for (const c of tbl.columns) {
        const td = document.createElement("td");
        const v = r[c];
        td.textContent = fmt(v);
        if (def.numeric.includes(c) || c === "id") td.classList.add("num");
        tr.appendChild(td);
      }
      tr.addEventListener("click", () => openModal(def, tbl, r));
      body.appendChild(tr);
    }
    $("row-count").querySelector(".n").textContent = String(rows.length);
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
    $("row-modal").hidden = true;
    document.body.style.overflow = "";
  }

  load();
})();
