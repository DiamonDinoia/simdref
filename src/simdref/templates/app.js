/* simdref – client-side SPA logic */
"use strict";

/* ── DOM refs ─────────────────────────────────────────────────────── */
const $ = (id) => document.getElementById(id);
const queryInput     = $("query");
const resultsNode    = $("results");
const detailNode     = $("detail");
const detailEmpty    = $("detail-empty");
const metaNode       = $("meta");
const resultsCount   = $("results-count");
const isaSummary     = $("isa-summary");
const isaPanel       = $("isa-panel");
const isaFamiliesNode = $("isa-families");
const isaSubgroupsNode = $("isa-subgroups");
const themeToggle    = $("theme-toggle");
const themeIconLight = $("theme-icon-light");
const themeIconDark  = $("theme-icon-dark");
const shortcutsOverlay = $("shortcuts-overlay");
const acNode         = $("autocomplete");

/* ── State ────────────────────────────────────────────────────────── */
let catalog = null;            // search-index.json payload
let intrinsicDetails = null;   // intrinsic-details.json (loaded on first need)
let searchEntries = [];
let searchTokenIndex = new Map();
let searchPrefixIndex = new Map();
let resultPool = [];
let activeKey = null;
let focusedIndex = -1;
let renderTimer = null;
let acItems = [];              // current autocomplete entries
let acIndex = -1;              // highlighted autocomplete index
let visibleSet = null;
let renderedCount = 0;
let loadMoreScheduled = false;
const INITIAL_RENDER_BATCH = 50;
const RENDER_BATCH_SIZE = 10;
const LOAD_MORE_THRESHOLD_PX = 600;

/* Detail chunk cache: prefix -> Promise<data> */
const chunkCache = new Map();

/* ISA state */
let defaultEnabledIsas = new Set(["SSE", "AVX", "AVX-512"]);
let availableIsas = [];
let enabledIsas = new Set();
let enableAllIsas = false;
let enabledSubIsas = new Map();

let FAMILY_SUB_ORDER = {};
let DEFAULT_SUBS = {};
let isaFamilyOrder = {};

/* ── Theme ────────────────────────────────────────────────────────── */
function getEffectiveTheme() {
  const stored = localStorage.getItem("simdref-theme");
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("simdref-theme", theme);
  themeIconLight.style.display = theme === "dark" ? "none" : "";
  themeIconDark.style.display = theme === "dark" ? "" : "none";
}

function toggleTheme() {
  applyTheme(getEffectiveTheme() === "dark" ? "light" : "dark");
}

applyTheme(getEffectiveTheme());

/* ── Helpers ──────────────────────────────────────────────────────── */
function esc(v) {
  return String(v ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function canonUrl(v) {
  if (!v) return "";
  if (String(v).startsWith("http")) return v;
  return "https://www." + String(v).replace(/^\/+/, "");
}

function displayInstr(v) {
  return String(v || "").trim();
}

function tokens(v) {
  return String(v || "").replaceAll("_", " ").replaceAll(",", " ").replaceAll("{", " ").replaceAll("}", " ")
    .toLowerCase().match(/[a-z0-9]+/g) || [];
}

function displayIsa(values) {
  return Array.isArray(values) ? (values.join(", ") || "-") : String(values || "-");
}

function defaultSubsForFamily(family) {
  const order = FAMILY_SUB_ORDER[family] || [];
  const defaults = DEFAULT_SUBS[family] || new Set(order);
  return new Set(order.filter(sub => defaults.has(sub)));
}

function initEnabledSubIsas() {
  enabledSubIsas = new Map();
  for (const family of availableIsas) {
    enabledSubIsas.set(family, FAMILY_SUB_ORDER[family] ? defaultSubsForFamily(family) : new Set());
  }
}

function ensureManualIsaState() {
  if (!enableAllIsas) return;
  enableAllIsas = false;
  enabledIsas = new Set(availableIsas);
  for (const family of availableIsas) {
    if (FAMILY_SUB_ORDER[family]) enabledSubIsas.set(family, new Set(FAMILY_SUB_ORDER[family]));
  }
}

function isaVisible(item) {
  if (enableAllIsas) return true;
  for (const family of (item.isa_families || [])) {
    if (!enabledIsas.has(family)) continue;
    if (!FAMILY_SUB_ORDER[family]) return true;
    const enabledSubs = enabledSubIsas.get(family);
    const entrySubs = item.isa_subs || [];
    if (entrySubs.some(subIsa => enabledSubs && enabledSubs.has(subIsa))) return true;
  }
  return false;
}

function rebuildVisibleSet() {
  visibleSet = new Set();
  for (let i = 0; i < searchEntries.length; i++) {
    const e = searchEntries[i];
    if (isaVisible(e.item)) visibleSet.add(i);
  }
}

function resultMarkup(entries, offset = 0) {
  return entries.map((e, i) => `
    <article class="result ${e.kind}-kind ${e.key === activeKey ? "active" : ""} ${offset + i === focusedIndex ? "focused" : ""}" data-key="${esc(e.key)}" data-index="${offset + i}">
      <div class="result-top">
        <span class="result-kind ${e.kind}">${esc(e.kind)}</span>
        <span class="result-isa">${esc(e.item.display_isa || displayIsa(e.item.isa))}</span>
      </div>
      <div class="result-title">${esc(e.title)}</div>
      <div class="result-meta">
        ${e.item.lat && e.item.lat !== "-" ? `<span class="result-perf">LAT ${esc(e.item.lat)}</span>` : ""}
        ${e.item.cpi && e.item.cpi !== "-" ? `<span class="result-perf">CPI ${esc(e.item.cpi)}</span>` : ""}
      </div>
      <div class="result-summary">${esc(e.subtitle || "")}</div>
    </article>
  `).join("");
}

function syncResultsCount(query) {
  const shown = Math.min(renderedCount, resultPool.length);
  if (query) {
    resultsCount.textContent = shown < resultPool.length
      ? `Showing ${shown} of ${resultPool.length} results for "${query}"`
      : `${resultPool.length} result${resultPool.length !== 1 ? "s" : ""} for "${query}"`;
  } else {
    resultsCount.textContent = shown < resultPool.length
      ? `Showing ${shown} of ${resultPool.length} results`
      : `${resultPool.length} results`;
  }
}

function renderVisibleResults(reset = false) {
  const nextCount = Math.min(renderedCount, resultPool.length);
  if (reset) {
    resultsNode.innerHTML = resultMarkup(resultPool.slice(0, nextCount), 0);
    return;
  }
  const currentCount = resultsNode.querySelectorAll(".result").length;
  if (nextCount > currentCount) {
    resultsNode.insertAdjacentHTML("beforeend", resultMarkup(resultPool.slice(currentCount, nextCount), currentCount));
  }
}

function loadMoreResults() {
  if (renderedCount >= resultPool.length) return;
  renderedCount = Math.min(renderedCount + RENDER_BATCH_SIZE, resultPool.length);
  renderVisibleResults(false);
  syncResultsCount(queryInput.value.trim());
}

function maybeLoadMoreResults() {
  while (
    renderedCount < resultPool.length &&
    resultsNode.scrollHeight - (resultsNode.scrollTop + resultsNode.clientHeight) <= LOAD_MORE_THRESHOLD_PX
  ) {
    const before = renderedCount;
    loadMoreResults();
    if (renderedCount === before) break;
  }
}

/* ── ISA sort key (chronological) ─────────────────────────────────── */
const chronology = {
  "I86": [0,0], "MMX": [1,0],
  "SSE": [2,0], "SSE2": [2,1], "SSE3": [2,2], "SSSE3": [2,3], "SSE4A": [2,4], "SSE4.1": [2,5], "SSE4.2": [2,6], "AES": [2,7], "PCLMULQDQ": [2,8],
  "F16C": [3,0], "FMA": [3,1],
  "AVX": [4,0], "AVX2": [5,0],
  "AVX512F": [6,0], "AVX512DQ": [6,1], "AVX512IFMA": [6,2], "AVX512PF": [6,3], "AVX512ER": [6,4], "AVX512CD": [6,5], "AVX512BW": [6,6], "AVX512VL": [6,7],
  "AVX512VBMI": [6,8], "AVX512VBMI2": [6,9], "AVX512VNNI": [6,10], "AVX512BITALG": [6,11], "AVX512VPOPCNTDQ": [6,12],
  "AVX512FP16": [6,99], "AVX10": [7,0], "AMX": [7,0], "APX": [9,0],
};
const chronologyEntries = Object.entries(chronology).sort((a, b) => b[0].length - a[0].length);

function isaSortKey(values) {
  const norms = (values && values.length ? values : ["-"]).map(v => String(Array.isArray(v) ? displayIsa(v) : v || "-").toUpperCase());
  function rank(v) {
    const c = v.replaceAll(" ", "");
    for (const [pfx, bucket] of chronologyEntries) { if (c.startsWith(pfx)) return bucket; }
    if (c.startsWith("AVX10")) return chronology["AVX10"];
    if (c.startsWith("AMX")) return chronology["AMX"];
    if (c.startsWith("APX")) return chronology["APX"];
    return [8, 0];
  }
  return norms.reduce((best, v) => {
    const cur = rank(v);
    if (!best) return cur;
    return cur[0] < best[0] || (cur[0] === best[0] && cur[1] < best[1]) ? cur : best;
  }, null) || [8, 0];
}

/* ── Search ranking ───────────────────────────────────────────────── */
function classifyQuery(q) {
  const lo = q.trim().toLowerCase();
  const norm = tokens(q).join(" ");
  if (lo.startsWith("_mm") || lo.startsWith("__m") || norm === "mm" || norm.startsWith("mm ")) return "intrinsic";
  if (lo.includes("_mm")) return "intrinsic";
  const parts = norm.split(" ").filter(Boolean);
  if (parts.length) {
    const f = parts[0];
    if (["add","sub","mul","div","mov","cmp","and","or","xor"].includes(f) || f.startsWith("v")) return "instruction";
  }
  return "neutral";
}

function tokenOverlapCount(qt, ct) {
  let n = 0;
  for (const t of qt) { if (ct.some(c => c === t || c.startsWith(t) || t.startsWith(c))) n++; }
  return n;
}

function meaningfulTokens(t) { return t.filter(x => !/^(mm|mm\d+|xmm|ymm|zmm)$/.test(x)); }

function hasOverlap(query, text) {
  const q = (query || "").trim().toLowerCase(), t = (text || "").trim().toLowerCase();
  if (!q || !t) return false;
  if (q === t || t.startsWith(q) || t.includes(q)) return true;
  const qt = tokens(query), ct = tokens(text);
  const m = meaningfulTokens(qt);
  return m.length ? tokenOverlapCount(m, ct) > 0 : tokenOverlapCount(qt, ct) > 0;
}

function scoreText(query, text) {
  const q = query.trim().toLowerCase(), t = (text || "").trim().toLowerCase();
  if (!q || !t) return 0;
  if (q === t) return 220;
  if (t.startsWith(q)) return 175;
  if (t.includes(q)) return 135;
  const qt = tokens(q), tt = tokens(t);
  if (!qt.length || !tt.length) return 0;
  const ov = tokenOverlapCount(qt, tt);
  return ov ? 100 * ov / qt.length + 40 : 0;
}

function rankEntry(query, entry) {
  const kind = classifyQuery(query);
  if (kind === "intrinsic" && !hasOverlap(query, entry.kind === "intrinsic" ? entry.item.name : entry.item.key)) return -Infinity;
  if (kind === "instruction" && !hasOverlap(query, entry.kind === "instruction" ? entry.item.key : entry.item.name)) return -Infinity;
  let score = Math.max(...entry.fields.map(f => scoreText(query, f)));
  if (entry.kind === "intrinsic") {
    const wq = new Set(tokens(query).filter(t => /^mm\d+$|^xmm$|^ymm$|^zmm$/.test(t)));
    const wt = new Set(tokens(entry.title).filter(t => /^mm\d+$|^xmm$|^ymm$|^zmm$/.test(t)));
    if (wq.size && wt.size) score += [...wq].some(w => wt.has(w)) ? 22 : -22;
  }
  if (kind === "intrinsic") score += entry.kind === "intrinsic" ? 45 : -25;
  if (kind === "instruction") score += entry.kind === "instruction" ? 35 : -10;
  return score;
}

/* ── Search index ─────────────────────────────────────────────────── */
function buildSearchIndexes(entries) {
  searchTokenIndex = new Map();
  searchPrefixIndex = new Map();
  entries.forEach((entry, i) => {
    entry.searchTokens = [...new Set((entry.item.search_tokens || entry.fields.flatMap(f => tokens(f))))];
    for (const t of entry.searchTokens) {
      if (!searchTokenIndex.has(t)) searchTokenIndex.set(t, []);
      searchTokenIndex.get(t).push(i);
      for (let sz = 1; sz <= Math.min(t.length, 6); sz++) {
        const pfx = t.slice(0, sz);
        if (!searchPrefixIndex.has(pfx)) searchPrefixIndex.set(pfx, []);
        searchPrefixIndex.get(pfx).push(i);
      }
    }
  });
}

function candidateIndexes(query) {
  const qt = tokens(query);
  if (!qt.length) return null;
  const lists = qt.map(t => searchTokenIndex.get(t) || searchPrefixIndex.get(t) || []);
  if (lists.some(l => !l.length)) return [];
  lists.sort((a, b) => a.length - b.length);
  let cur = new Set(lists[0]);
  for (const l of lists.slice(1)) {
    const allowed = new Set(l);
    const next = new Set();
    for (const i of cur) if (allowed.has(i)) next.add(i);
    cur = next;
    if (!cur.size) break;
  }
  return [...cur];
}

/* ── Natural sort ─────────────────────────────────────────────────── */
function naturalKey(v) { return (v || "").toLowerCase().match(/[a-z]+|\d+/g) || []; }
function naturalCmp(a, b) {
  const ak = naturalKey(a), bk = naturalKey(b);
  for (let i = 0; i < Math.max(ak.length, bk.length); i++) {
    if (ak[i] == null) return -1;
    if (bk[i] == null) return 1;
    const an = /^\d+$/.test(ak[i]), bn = /^\d+$/.test(bk[i]);
    if (an && bn) { const d = Number(ak[i]) - Number(bk[i]); if (d) return d; }
    else if (an !== bn) return an ? 1 : -1;
    else { const d = ak[i].localeCompare(bk[i]); if (d) return d; }
  }
  return 0;
}

/* ── Lazy detail loading ──────────────────────────────────────────── */
function chunkPrefix(mnemonic) {
  const c = (mnemonic || "").trim().toUpperCase();
  return c.length >= 3 ? c.slice(0, 3) : c;
}

function loadChunk(prefix) {
  if (chunkCache.has(prefix)) return chunkCache.get(prefix);
  const p = fetch(`detail-chunks/${encodeURIComponent(prefix)}.json`)
    .then(r => r.ok ? r.json() : {})
    .catch(() => ({}));
  chunkCache.set(prefix, p);
  return p;
}

function loadIntrinsicDetails() {
  if (intrinsicDetails) return Promise.resolve(intrinsicDetails);
  return fetch("intrinsic-details.json")
    .then(r => r.ok ? r.json() : {})
    .then(data => { intrinsicDetails = data; return data; })
    .catch(() => ({}));
}

/* ── Render helpers ───────────────────────────────────────────────── */
function renderTable(headers, rows) {
  if (!rows.length) return `<div style="color:var(--text-muted);font-size:0.82rem">No data.</div>`;
  return `<table><thead><tr>${headers.map(h => `<th>${esc(h.label)}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => `<tr>${headers.map(h => {
      const cls = h.cls ? ` class="${h.cls}"` : "";
      return `<td${cls}>${h.render ? h.render(r) : esc(r[h.key] ?? "-")}</td>`;
    }).join("")}</tr>`).join("")
  }</tbody></table>`;
}

/* ── Measurement grouping ─────────────────────────────────────────── */
const uarchOrder = ["ARL-P","ARL-E","MTL-P","MTL-E","EMR","ADL-P","ADL-E","RKL","TGL","ICL","CLX","CNL","SKX","CFL","KBL","SKL","BDW","HSW","IVB","SNB","ZEN5","ZEN4","ZEN3","ZEN2","ZEN+"];

function uarchFamily(u) {
  if (["ZEN5","ZEN4","ZEN3","ZEN2","ZEN+"].includes(u)) return "AMD";
  if (["EMR","CLX","SKX","CNL"].includes(u)) return "Intel Server";
  return "Intel Client";
}

function groupMeasurements(rows) {
  const sorted = [...rows].sort((a, b) => {
    const ai = uarchOrder.indexOf(a.uarch), bi = uarchOrder.indexOf(b.uarch);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi) || a.uarch.localeCompare(b.uarch);
  });
  const groups = new Map();
  for (const r of sorted) {
    const fam = uarchFamily(r.uarch);
    if (!groups.has(fam)) groups.set(fam, []);
    groups.get(fam).push(r);
  }
  return groups;
}

const uarchLabels = {
  "ARL-P": "Arrow Lake-P (2024)", "ARL-E": "Arrow Lake-E (2024)",
  "MTL-P": "Meteor Lake-P (2023)", "MTL-E": "Meteor Lake-E (2023)",
  "EMR": "Emerald Rapids (2023)",
  "ADL-P": "Alder Lake-P (2021)", "ADL-E": "Alder Lake-E (2021)",
  "RKL": "Rocket Lake (2021)", "TGL": "Tiger Lake (2020)", "ICL": "Ice Lake (2019)",
  "CLX": "Cascade Lake (2019)", "CNL": "Cannon Lake (2018)", "SKX": "Skylake-X (2017)",
  "CFL": "Coffee Lake (2017)", "KBL": "Kaby Lake (2016)", "SKL": "Skylake (2015)",
  "BDW": "Broadwell (2014)", "HSW": "Haswell (2013)", "IVB": "Ivy Bridge (2012)", "SNB": "Sandy Bridge (2011)",
  "ZEN5": "Zen 5 (2024)", "ZEN4": "Zen 4 (2022)", "ZEN3": "Zen 3 (2020)", "ZEN2": "Zen 2 (2019)", "ZEN+": "Zen+ (2018)",
};

const measHeaders = [
  {key: "uarch", label: "microarch", render: r => esc(uarchLabels[r.uarch] || r.uarch)},
  {key: "latency", label: "LAT", cls: "highlight"},
  {key: "tpLoop", label: "CPI", cls: "highlight"},
  {key: "tpPorts", label: "CPI ports"},
  {key: "uops", label: "uops"},
  {key: "ports", label: "ports"},
];

function renderMeasurements(measurements) {
  if (!measurements || !measurements.length) return `<div style="color:var(--text-muted);font-size:0.82rem">No performance data.</div>`;
  const groups = groupMeasurements(measurements);
  let html = "";
  for (const [family, rows] of groups) {
    html += `<details class="meas-group" open><summary>${esc(family)} (${rows.length})</summary>${renderTable(measHeaders, rows)}</details>`;
  }
  return html;
}

/* ── Description sections ────────────────────────────────────────── */
const descSectionOrder = [
  "Description", "Operation", "Intrinsic Equivalents",
  "Flags Affected", "Exceptions", "SIMD Floating-Point Exceptions",
  "Numeric Exceptions", "Other Exceptions",
  "Protected Mode Exceptions", "Real-Address Mode Exceptions",
  "Virtual-8086 Mode Exceptions", "Compatibility Mode Exceptions",
  "64-Bit Mode Exceptions",
];
const codeSections = new Set(["Operation", "Intrinsic Equivalents"]);

function renderDescriptionSections(description) {
  if (!description || !Object.keys(description).length) return "";
  const shown = new Set();
  let html = "";
  for (const key of descSectionOrder) {
    if (description[key]) {
      const isCode = codeSections.has(key);
      const isFirst = key === "Description";
      html += `<details class="desc-section"${isFirst ? " open" : ""}>
        <summary>${esc(key)}</summary>
        ${isCode ? `<pre class="desc-code">${esc(description[key])}</pre>` : `<div class="desc-body">${esc(description[key])}</div>`}
      </details>`;
      shown.add(key);
    }
  }
  for (const [key, value] of Object.entries(description)) {
    if (!shown.has(key)) {
      html += `<details class="desc-section">
        <summary>${esc(key)}</summary>
        <div class="desc-body">${esc(value)}</div>
      </details>`;
    }
  }
  return html;
}

/* ── Detail rendering ─────────────────────────────────────────────── */
const operandHeaders = [
  {key: "idx", label: "Idx"},
  {key: "rw", label: "R/W", render: r => esc(((r.r === "1" ? "r" : "") + (r.w === "1" ? "w" : "")) || "-")},
  {key: "type", label: "Type"},
  {key: "width", label: "Width"},
  {key: "xtype", label: "XType"},
  {key: "name", label: "Name", cls: "mono"},
];

function renderIntrinsicDetail(item, detail) {
  const linked = (item.instructions || [])
    .map(k => catalog.instrByKey[k] || catalog.instrByMnem[k])
    .filter(Boolean).filter(r => isaVisible(r.isa));

  const hasMeasurements = detail && detail._measurements && detail._measurements.length;
  const hasOperands = detail && detail._operands && detail._operands.length;

  return `
    <div class="detail-head">
      <span class="result-kind intrinsic">intrinsic</span>
      <h2>${esc(item.name)} <button class="copy-btn" data-copy="${esc(item.name)}" title="Copy name">&#x2398;</button></h2>
      <div class="detail-sub">${esc(detail ? detail.signature : item.signature)}</div>
      <div class="chips">
        ${((item.display_isa_tokens || item.isa || [])).map(v => `<span class="chip">${esc(Array.isArray(v) ? displayIsa(v) : v)}</span>`).join("")}
        ${item.header ? `<span class="chip">${esc(item.header)}</span>` : ""}
      </div>
    </div>
    <section class="section">
      <h3>Description</h3>
      <div>${esc(detail ? detail.description : item.description)}</div>
    </section>
    ${detail && detail._instrDescription && Object.keys(detail._instrDescription).length ? `<section class="section">
      <h3>Instruction Semantics</h3>
      ${renderDescriptionSections(detail._instrDescription)}
    </section>` : ""}
    <section class="section">
      <h3>Metadata</h3>
      <dl class="kv">
        <dt>Header</dt><dd>${esc(item.header || "-")}</dd>
        <dt>ISA</dt><dd>${esc(item.display_isa || displayIsa(item.isa))}</dd>
        ${detail && detail.notes && detail.notes.length ? `<dt>Notes</dt><dd>${esc(detail.notes.join("; "))}</dd>` : ""}
        ${detail && detail._url ? `<dt>uops.info</dt><dd><a href="${esc(detail._url)}" target="_blank" rel="noreferrer">${esc(detail._url)}</a></dd>` : ""}
        ${detail && detail._urlRef ? `<dt>Reference</dt><dd><a href="${esc(detail._urlRef)}" target="_blank" rel="noreferrer">${esc(detail._urlRef)}</a></dd>` : ""}
        ${detail && Array.isArray(detail._pdfRefs) ? detail._pdfRefs.map(ref => `<dt>${esc(ref.label || ref.source_id || "PDF")}</dt><dd><a href="${esc(ref.url || "")}" target="_blank" rel="noreferrer">Open PDF${ref.page_start ? ` (page ${esc(ref.page_start)})` : ""}</a></dd>`).join("") : ""}
      </dl>
    </section>
    <section class="section">
      <h3>Instructions</h3>
      ${linked.length ? renderTable(
        [
          {key: "key", label: "Instruction", render: r => `<a class="xref" href="#${encodeURIComponent(r.key)}" data-kind="instruction" data-key="${esc(r.key)}">${esc(r.display_key || r.key)}</a>`},
          {key: "summary", label: "Summary"},
          {key: "isa", label: "ISA", render: r => esc(r.display_isa || displayIsa(r.isa))},
        ],
        linked
      ) : `<div style="color:var(--text-muted);font-size:0.82rem">No linked instructions.</div>`}
    </section>
    ${hasOperands ? `<section class="section">
      <h3>Operands</h3>
      ${renderTable(operandHeaders, detail._operands)}
    </section>` : ""}
    ${hasMeasurements ? `<section class="section">
      <h3>Performance</h3>
      ${renderMeasurements(detail._measurements)}
    </section>` : ""}
  `;
}

function renderInstructionDetail(item, detail) {
  const d = detail || {};
  const linked = (item.linked_intrinsics || [])
    .map(n => catalog.intrByName[n])
    .filter(Boolean).filter(r => isaVisible(r.isa));

  const measurements = d.measurements || [];
  const operands = d.operand_details || [];
  const meta = d.metadata || {};
  const pdfRefs = Array.isArray(d.pdf_refs) ? d.pdf_refs : (meta["intel-sdm-url"] ? [{
    source_id: "intel-sdm",
    label: "Intel SDM",
    url: meta["intel-sdm-url"],
    page_start: meta["intel-sdm-page-start"] || "",
    page_end: meta["intel-sdm-page-end"] || "",
  }] : []);

  return `
    <div class="detail-head">
      <span class="result-kind instruction">instruction</span>
      <h2>${esc(item.display_key || item.key || item.mnemonic)} <button class="copy-btn" data-copy="${esc(item.display_key || item.key || item.mnemonic)}" title="Copy name">&#x2398;</button></h2>
      <div class="detail-sub">${esc(d.display_form || item.display_form || d.form || item.form || item.display_mnemonic || item.mnemonic)}</div>
      <div class="chips">
        ${((item.display_isa_tokens || item.isa || [])).map(v => `<span class="chip">${esc(Array.isArray(v) ? displayIsa(v) : v)}</span>`).join("")}
        ${meta.cpl ? `<span class="chip">CPL ${esc(meta.cpl)}</span>` : ""}
      </div>
    </div>
    <section class="section">
      <h3>Summary</h3>
      <div>${esc(d.summary || item.summary || "-")}</div>
    </section>
    ${d.description && Object.keys(d.description).length ? `<section class="section">
      <h3>Instruction Semantics</h3>
      ${renderDescriptionSections(d.description)}
    </section>` : ""}
    <section class="section">
      <h3>Metadata</h3>
      <dl class="kv">
        <dt>Mnemonic</dt><dd class="mono">${esc(item.display_mnemonic || item.mnemonic)}</dd>
        <dt>Form</dt><dd class="mono">${esc(d.display_form || item.display_form || d.form || item.form || "-")}</dd>
        <dt>ISA</dt><dd>${esc(item.display_isa || displayIsa(item.isa))}</dd>
        ${meta.url ? `<dt>uops.info</dt><dd><a href="${esc(canonUrl(meta.url))}" target="_blank" rel="noreferrer">${esc(canonUrl(meta.url))}</a></dd>` : ""}
        ${meta["url-ref"] ? `<dt>Reference</dt><dd><a href="${esc(canonUrl(meta["url-ref"]))}" target="_blank" rel="noreferrer">${esc(canonUrl(meta["url-ref"]))}</a></dd>` : ""}
        ${pdfRefs.map(ref => `<dt>${esc(ref.label || ref.source_id || "PDF")}</dt><dd><a href="${esc(ref.url || "")}" target="_blank" rel="noreferrer">Open PDF${ref.page_start ? ` (page ${esc(ref.page_start)})` : ""}</a></dd>`).join("")}
      </dl>
    </section>
    <section class="section">
      <h3>Intrinsics</h3>
      ${linked.length ? renderTable(
        [
          {key: "name", label: "Intrinsic", render: r => `<a class="xref" href="#${encodeURIComponent(r.name)}" data-kind="intrinsic" data-key="${esc(r.name)}">${esc(r.name)}</a>`},
          {key: "description", label: "Description"},
          {key: "isa", label: "ISA", render: r => esc(r.display_isa || displayIsa(r.isa))},
        ],
        linked
      ) : `<div style="color:var(--text-muted);font-size:0.82rem">No linked intrinsics.</div>`}
    </section>
    ${operands.length ? `<section class="section">
      <h3>Operands</h3>
      ${renderTable(operandHeaders, operands)}
    </section>` : ""}
    ${measurements.length ? `<section class="section">
      <h3>Performance</h3>
      ${renderMeasurements(measurements)}
    </section>` : ""}
  `;
}

/* ── Detail view ──────────────────────────────────────────────────── */
async function renderDetail(entry) {
  activeKey = entry.key;
  detailEmpty.style.display = "none";

  // Highlight in results
  for (const n of resultsNode.querySelectorAll(".result")) {
    n.classList.toggle("active", n.dataset.key === entry.key);
  }
  location.hash = encodeURIComponent(entry.key);

  if (entry.kind === "intrinsic") {
    // Load primary instruction detail for operands/measurements
    const primaryKey = (entry.item.instructions || [])[0];
    let detail = null;
    if (primaryKey) {
      const instr = catalog.instrByKey[primaryKey] || catalog.instrByMnem[primaryKey];
      if (instr) {
        const prefix = chunkPrefix(instr.mnemonic);
        const chunk = await loadChunk(prefix);
        const instrDetail = chunk[instr.key] || {};
        // Also load full intrinsic details
        const intrDetails = await loadIntrinsicDetails();
        detail = intrDetails[entry.item.name] || entry.item;
        detail._measurements = instrDetail.measurements || [];
        detail._operands = instrDetail.operand_details || [];
        detail._instrDescription = instrDetail.description || {};
        detail._url = instrDetail.metadata ? canonUrl(instrDetail.metadata.url) : "";
        detail._urlRef = instrDetail.metadata ? canonUrl(instrDetail.metadata["url-ref"]) : "";
        detail._pdfRefs = instrDetail.pdf_refs || [];
      }
    }
    if (!detail) {
      const intrDetails = await loadIntrinsicDetails();
      detail = intrDetails[entry.item.name] || entry.item;
      detail._measurements = [];
      detail._operands = [];
      detail._pdfRefs = [];
    }
    detailNode.innerHTML = renderIntrinsicDetail(entry.item, detail);
  } else {
    // Load instruction detail chunk
    const prefix = chunkPrefix(entry.item.mnemonic);
    const chunk = await loadChunk(prefix);
    const detail = chunk[entry.item.key] || null;
    detailNode.innerHTML = renderInstructionDetail(entry.item, detail);
  }

}

/* ── ISA Filters ──────────────────────────────────────────────────── */

function updateIsaSummary() {
  if (enableAllIsas) { isaSummary.textContent = "ISA: All"; return; }
  const active = availableIsas.filter(v => enabledIsas.has(v));
  if (!active.length) { isaSummary.textContent = "ISA: None"; return; }
  if (active.length <= 3) { isaSummary.textContent = "ISA: " + active.join(", "); return; }
  isaSummary.textContent = `ISA: ${active.slice(0, 2).join(", ")} +${active.length - 2}`;
}

function renderIsaFilters() {
  isaFamiliesNode.innerHTML = availableIsas.map(family => {
    const familyActive = enableAllIsas || enabledIsas.has(family);
    return `<label class="isa-chip ${familyActive ? "active" : ""}">
      <input type="checkbox" data-family="${esc(family)}" ${familyActive ? "checked" : ""}>
      ${esc(family)}
    </label>`;
  }).join("");

  isaSubgroupsNode.innerHTML = availableIsas.flatMap(family => {
    const subs = FAMILY_SUB_ORDER[family];
    const familyActive = enableAllIsas || enabledIsas.has(family);
    if (!subs || !familyActive) return [];
    const enabledSubs = enabledSubIsas.get(family) || new Set();
    return [`<div class="isa-group">
      <button class="isa-group-label family-active" type="button" data-group-family="${esc(family)}">${esc(family)}</button>
      <div class="isa-group-items">
        ${subs.map(sub => `<label class="isa-chip ${(enableAllIsas || enabledSubs.has(sub)) ? "active" : ""}">
          <input type="checkbox" data-sub-isa="${esc(sub)}" data-family="${esc(family)}" ${(enableAllIsas || enabledSubs.has(sub)) ? "checked" : ""}>
          ${esc(sub)}
        </label>`).join("")}
      </div>
    </div>`];
  }).join("");

  for (const cb of isaFamiliesNode.querySelectorAll("input[data-family]")) {
    cb.addEventListener("change", () => {
      const family = cb.dataset.family;
      ensureManualIsaState();
      if (cb.checked) enabledIsas.add(family); else enabledIsas.delete(family);
      visibleSet = null;
      rebuildVisibleSet();
      updateIsaSummary();
      renderIsaFilters();
      renderResults();
    });
  }

  for (const btn of isaSubgroupsNode.querySelectorAll("button[data-group-family]")) {
    btn.addEventListener("click", () => {
      const family = btn.dataset.groupFamily;
      ensureManualIsaState();
      const familyActive = enabledIsas.has(family);
      if (familyActive) {
        enabledIsas.delete(family);
        enabledSubIsas.set(family, new Set());
      } else {
        enabledIsas.add(family);
        enabledSubIsas.set(family, defaultSubsForFamily(family));
      }
      visibleSet = null;
      rebuildVisibleSet();
      updateIsaSummary();
      renderIsaFilters();
      renderResults();
    });
  }

  for (const cb of isaSubgroupsNode.querySelectorAll("input[data-sub-isa]")) {
    cb.addEventListener("change", () => {
      const family = cb.dataset.family;
      const subIsa = cb.dataset.subIsa;
      ensureManualIsaState();
      enabledIsas.add(family);
      const familySubs = new Set(enabledSubIsas.get(family) || []);
      if (cb.checked) familySubs.add(subIsa); else familySubs.delete(subIsa);
      enabledSubIsas.set(family, familySubs);
      if (!familySubs.size) enabledIsas.delete(family);
      visibleSet = null;
      rebuildVisibleSet();
      updateIsaSummary();
      renderIsaFilters();
      renderResults();
    });
  }
  updateIsaSummary();
}

function applyIsaPreset(mode) {
  if (mode === "all") {
    enableAllIsas = true;
  } else {
    enableAllIsas = false;
    enabledIsas = mode === "default"
      ? new Set([...defaultEnabledIsas].filter(v => availableIsas.includes(v)))
      : new Set();
    initEnabledSubIsas();
    if (mode === "none") {
      for (const family of availableIsas) {
        if (FAMILY_SUB_ORDER[family]) enabledSubIsas.set(family, new Set());
      }
    }
  }
  visibleSet = null;
  rebuildVisibleSet();
  renderIsaFilters();
  renderResults();
}

/* ── Results rendering ────────────────────────────────────────────── */
function renderResults() {
  const query = queryInput.value.trim();
  if (!catalog) return;
  if (visibleSet === null) rebuildVisibleSet();
  const visible = searchEntries.filter((_, i) => visibleSet.has(i));

  if (!query) {
    resultPool = visible;
  } else {
    const cids = candidateIndexes(query);
    const pool = cids == null ? visible : cids.filter(i => visibleSet.has(i)).map(i => searchEntries[i]);
    resultPool = pool
      .map(e => ({...e, score: rankEntry(query, e)}))
      .filter(e => Number.isFinite(e.score) && e.score >= 35)
      .sort((a, b) =>
        b.score - a.score
        || (a.kind !== b.kind ? (a.kind === "instruction" ? -1 : 1) : 0)
        || (() => { const [aa,ab] = isaSortKey(a.item.display_isa_tokens || a.item.isa); const [ba,bb] = isaSortKey(b.item.display_isa_tokens || b.item.isa); return aa-ba||ab-bb; })()
        || naturalCmp(a.title, b.title)
        || a.title.length - b.title.length
      )
      .slice(0, 5000);
  }

  renderedCount = Math.min(INITIAL_RENDER_BATCH, resultPool.length);
  syncResultsCount(query);
  renderVisibleResults(true);
  requestAnimationFrame(() => maybeLoadMoreResults());

  // Auto-select
  const fromHash = decodeURIComponent(location.hash.replace(/^#/, ""));
  const selected = resultPool.find(e => e.key === activeKey)
    || resultPool.find(e => e.key === fromHash)
    || resultPool[0];
  if (selected) renderDetail(selected);
  else {
    detailNode.innerHTML = "";
    detailEmpty.style.display = "";
    detailEmpty.textContent = query ? `No results for "${query}".` : "Select a result or search for an intrinsic / instruction.";
  }
}

function scheduleRender() {
  if (renderTimer) clearTimeout(renderTimer);
  renderTimer = setTimeout(() => { renderTimer = null; renderResults(); updateAutocomplete(); }, 90);
}

/* ── Autocomplete ─────────────────────────────────────────────────── */
function updateAutocomplete() {
  const query = queryInput.value.trim();
  if (!query || !catalog || query.length < 2) { hideAutocomplete(); return; }
  acItems = resultPool.slice(0, 8);
  acIndex = -1;
  if (!acItems.length) { hideAutocomplete(); return; }
  acNode.innerHTML = acItems.map((e, i) => `
    <div class="ac-item" data-ac="${i}">
      <span class="result-kind ${e.kind}">${esc(e.kind)}</span>
      <span class="ac-name">${esc(e.title)}</span>
      <span class="ac-desc">${esc(e.subtitle || "")}</span>
    </div>
  `).join("");
  acNode.classList.remove("hidden");
  for (const el of acNode.querySelectorAll(".ac-item")) {
    el.addEventListener("click", () => {
      const idx = parseInt(el.dataset.ac);
      selectAcItem(idx);
    });
  }
}

function hideAutocomplete() {
  acNode.classList.add("hidden");
  acItems = [];
  acIndex = -1;
}

function highlightAcItem(idx) {
  acIndex = idx;
  for (const el of acNode.querySelectorAll(".ac-item")) {
    el.classList.toggle("ac-active", parseInt(el.dataset.ac) === idx);
  }
  const active = acNode.querySelector(`.ac-item[data-ac="${idx}"]`);
  if (active) active.scrollIntoView({block: "nearest"});
}

function selectAcItem(idx) {
  if (idx < 0 || idx >= acItems.length) return;
  const entry = acItems[idx];
  queryInput.value = entry.title;
  hideAutocomplete();
  renderResults();
  if (resultPool.length) {
    focusedIndex = 0;
    renderDetail(resultPool[0]);
  }
}

/* ── Keyboard navigation ──────────────────────────────────────────── */
function focusResult(idx) {
  if (idx < 0 || idx >= resultPool.length) return;
  while (idx >= renderedCount && renderedCount < resultPool.length) {
    renderedCount = Math.min(renderedCount + RENDER_BATCH_SIZE, resultPool.length);
    renderVisibleResults(false);
  }
  focusedIndex = idx;
  for (const n of resultsNode.querySelectorAll(".result")) {
    n.classList.toggle("focused", parseInt(n.dataset.index) === idx);
  }
  const el = resultsNode.querySelector(`.result[data-index="${idx}"]`);
  if (el) el.scrollIntoView({block: "nearest"});
}

function selectFocused() {
  if (focusedIndex >= 0 && focusedIndex < resultPool.length) {
    renderDetail(resultPool[focusedIndex]);
  }
}

resultsNode.addEventListener("click", (e) => {
  const node = e.target.closest(".result");
  if (!node) return;
  const idx = parseInt(node.dataset.index);
  focusedIndex = idx;
  renderDetail(resultPool[idx]);
  focusResult(idx);
});

resultsNode.addEventListener("scroll", () => {
  if (loadMoreScheduled) return;
  loadMoreScheduled = true;
  requestAnimationFrame(() => {
    loadMoreScheduled = false;
    maybeLoadMoreResults();
  });
});

function refineSearchFromResultsKey(key) {
  if (key === "Backspace") {
    if (!queryInput.value) return false;
    queryInput.focus();
    queryInput.value = queryInput.value.slice(0, -1);
    scheduleRender();
    return true;
  }
  if (key.length !== 1) return false;
  queryInput.focus();
  queryInput.value += key;
  scheduleRender();
  return true;
}

document.addEventListener("keydown", (e) => {
  const tag = document.activeElement?.tagName;
  const inInput = tag === "INPUT" || tag === "TEXTAREA";

  // Global: ? for shortcuts
  if (e.key === "?" && !inInput) {
    e.preventDefault();
    shortcutsOverlay.classList.toggle("hidden");
    return;
  }

  // Escape: close overlays, autocomplete, blur
  if (e.key === "Escape") {
    if (!shortcutsOverlay.classList.contains("hidden")) {
      shortcutsOverlay.classList.add("hidden");
      return;
    }
    if (!acNode.classList.contains("hidden")) {
      hideAutocomplete();
      return;
    }
    if (!isaPanel.classList.contains("hidden")) {
      isaPanel.classList.add("hidden");
      return;
    }
    if (inInput) { queryInput.blur(); return; }
    return;
  }

  // Ctrl+K: focus search (works everywhere)
  if (e.key === "k" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    queryInput.focus();
    queryInput.select();
    return;
  }

  // /: focus search (when not in input)
  if (e.key === "/" && !inInput) {
    e.preventDefault();
    queryInput.focus();
    queryInput.select();
    return;
  }

  // In search input: arrow keys navigate results directly
  if (inInput && document.activeElement === queryInput) {
    return;
  }

  if (
    (document.activeElement === resultsNode || document.activeElement === detailNode) &&
    !e.ctrlKey && !e.metaKey && !e.altKey &&
    refineSearchFromResultsKey(e.key)
  ) {
    e.preventDefault();
    return;
  }

  if (inInput) return;

  // d: toggle dark mode
  if (e.key === "d") { toggleTheme(); return; }

  // f: toggle ISA filter
  if (e.key === "f") { isaPanel.classList.toggle("hidden"); return; }

  if (e.shiftKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    resultsNode.scrollBy({ top: e.key === "ArrowDown" ? 120 : -120, behavior: "smooth" });
    return;
  }
  if (e.shiftKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
    e.preventDefault();
    return;
  }

  // j/k or arrows: navigate results
  if (e.key === "j" || e.key === "ArrowDown") {
    e.preventDefault();
    focusResult(Math.min(focusedIndex + 1, resultPool.length - 1));
    selectFocused();
    return;
  }
  if (e.key === "k" || e.key === "ArrowUp") {
    e.preventDefault();
    focusResult(Math.max(focusedIndex - 1, 0));
    selectFocused();
    return;
  }

  // Enter/l: select
  if (e.key === "Enter" || e.key === "l") { selectFocused(); return; }

  // [ / ]: previous/next result (alternate to j/k)
  if (e.key === "]") {
    e.preventDefault();
    focusResult(Math.min(focusedIndex + 1, resultPool.length - 1));
    selectFocused();
    return;
  }
  if (e.key === "[") {
    e.preventDefault();
    focusResult(Math.max(focusedIndex - 1, 0));
    selectFocused();
    return;
  }

  // Home/End: jump to first/last result
  if (e.key === "Home") {
    e.preventDefault();
    focusResult(0);
    selectFocused();
    return;
  }
  if (e.key === "End") {
    e.preventDefault();
    focusResult(resultPool.length - 1);
    selectFocused();
    return;
  }

  // Tab/Shift+Tab: switch focus between results and detail panels
  if (e.key === "Tab") {
    e.preventDefault();
    if (e.shiftKey) resultsNode.focus();
    else detailNode.focus();
    return;
  }

  // c: copy current intrinsic/instruction name to clipboard
  if (e.key === "c") {
    const copyBtn = detailNode.querySelector(".copy-btn");
    if (copyBtn) copyBtn.click();
    return;
  }

  // s: toggle sidebar collapse (mobile)
  if (e.key === "s") {
    const panel = document.getElementById("results-panel");
    panel.classList.toggle("collapsed");
    return;
  }

  // h: back to search
  if (e.key === "h") { queryInput.focus(); queryInput.select(); return; }
});

/* ── Copy button clicks ──────────────────────────────────────────── */
detailNode.addEventListener("click", (e) => {
  const btn = e.target.closest(".copy-btn");
  if (!btn) return;
  e.preventDefault();
  const text = btn.dataset.copy;
  if (text) navigator.clipboard.writeText(text).then(() => {
    btn.textContent = "\u2713";
    setTimeout(() => { btn.textContent = "\u2398"; }, 1200);
  });
});

/* ── Cross-reference clicks ───────────────────────────────────────── */
detailNode.addEventListener("click", (e) => {
  const link = e.target.closest("a.xref");
  if (!link) return;
  e.preventDefault();
  const key = link.dataset.key;
  const kind = link.dataset.kind;
  if (!key || !catalog) return;

  let entry = null;
  if (kind === "intrinsic") {
    const item = catalog.intrByName[key];
    if (item) entry = { kind: "intrinsic", key: item.name, title: item.name, subtitle: item.description, item, fields: item.search_fields || [] };
  } else {
    const item = catalog.instrByKey[key];
    if (item) entry = { kind: "instruction", key: item.key, title: item.display_key || item.key, subtitle: item.summary, item, fields: item.search_fields || [] };
  }
  if (entry) renderDetail(entry);
});

/* ── ISA panel toggle ─────────────────────────────────────────────── */
$("isa-toggle").addEventListener("click", () => isaPanel.classList.toggle("hidden"));
$("isa-default").addEventListener("click", () => applyIsaPreset("default"));
$("isa-none").addEventListener("click", () => applyIsaPreset("none"));
$("isa-all").addEventListener("click", () => applyIsaPreset("all"));
$("close-shortcuts").addEventListener("click", () => shortcutsOverlay.classList.add("hidden"));
themeToggle.addEventListener("click", toggleTheme);
queryInput.addEventListener("input", scheduleRender);
queryInput.addEventListener("keydown", (e) => {
  if (e.shiftKey && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    e.stopPropagation();
    resultsNode.scrollBy({ top: e.key === "ArrowDown" ? 120 : -120, behavior: "smooth" });
    return;
  }
  if (e.shiftKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
    e.preventDefault();
    e.stopPropagation();
    return;
  }
  if (e.key === "ArrowDown") {
    e.preventDefault();
    e.stopPropagation();
    focusResult(Math.min(focusedIndex + 1, resultPool.length - 1));
    selectFocused();
    return;
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    e.stopPropagation();
    focusResult(Math.max(focusedIndex - 1, 0));
    selectFocused();
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    e.stopPropagation();
    hideAutocomplete();
    if (focusedIndex >= 0) selectFocused();
    else if (resultPool.length) {
      focusResult(0);
      selectFocused();
    } else {
      renderResults();
    }
  }
});
queryInput.addEventListener("blur", () => setTimeout(hideAutocomplete, 150));
queryInput.addEventListener("focus", () => { if (queryInput.value.trim().length >= 2) updateAutocomplete(); });

/* ── Hash navigation ──────────────────────────────────────────────── */
window.addEventListener("hashchange", () => {
  const key = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (!catalog || !key) return;
  const entry = resultPool.find(e => e.key === key)
    || (catalog.intrByName[key] ? {kind: "intrinsic", key, title: key, subtitle: catalog.intrByName[key].description, item: catalog.intrByName[key], fields: catalog.intrByName[key].search_fields || []} : null)
    || (catalog.instrByKey[key] ? {kind: "instruction", key, title: catalog.instrByKey[key].display_key || key, subtitle: catalog.instrByKey[key].summary, item: catalog.instrByKey[key], fields: catalog.instrByKey[key].search_fields || []} : null);
  if (entry) renderDetail(entry);
});

/* ── Bootstrap ────────────────────────────────────────────────────── */
fetch("search-index.json")
  .then(r => r.json())
  .then(data => {
    catalog = data;
    const config = data.isa_config || {};
    defaultEnabledIsas = new Set(config.default_enabled || [...defaultEnabledIsas]);
    FAMILY_SUB_ORDER = config.family_sub_order || {};
    DEFAULT_SUBS = Object.fromEntries(Object.entries(config.default_subs || {}).map(([family, values]) => [family, new Set(values)]));
    isaFamilyOrder = config.family_order || {};
    catalog.intrByName = Object.fromEntries(data.intrinsics.map(i => [i.name, i]));
    catalog.instrByKey = Object.fromEntries(data.instructions.map(i => [i.key, i]));
    catalog.instrByMnem = Object.fromEntries(data.instructions.map(i => [i.mnemonic, i]));

    searchEntries = [
      ...data.intrinsics.map(i => ({
        kind: "intrinsic", key: i.name, title: i.name, subtitle: i.description, item: i,
        fields: i.search_fields || [i.name, i.signature || "", i.description || "", i.header || "", i.display_isa || displayIsa(i.isa), (i.instructions || []).join(" ")],
      })),
      ...data.instructions.map(i => ({
        kind: "instruction", key: i.key, title: i.display_key || i.key, subtitle: i.summary, item: i,
        fields: i.search_fields || [i.display_mnemonic || i.mnemonic || "", i.display_form || i.form || "", i.summary || "", i.display_isa || displayIsa(i.isa), (i.linked_intrinsics || []).join(" ")],
      })),
    ];

    buildSearchIndexes(searchEntries);

    availableIsas = [...new Set(searchEntries.flatMap(e => e.item.isa_families || []))]
      .sort((a, b) => (isaFamilyOrder[a] ?? 99) - (isaFamilyOrder[b] ?? 99) || a.localeCompare(b));
    enabledIsas = new Set([...defaultEnabledIsas].filter(v => availableIsas.includes(v)));
    initEnabledSubIsas();

    renderIsaFilters();

    rebuildVisibleSet();

    metaNode.textContent = `${data.intrinsics.length} intrinsics \u00b7 ${data.instructions.length} instructions`;

    const fromHash = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (fromHash) queryInput.value = fromHash;
    renderResults();
  });
