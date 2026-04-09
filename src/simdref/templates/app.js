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
const isaGroups      = $("isa-groups");
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

/* Detail chunk cache: prefix -> Promise<data> */
const chunkCache = new Map();

/* ISA state */
const defaultEnabledIsas = new Set(["SSE", "AVX", "AVX2", "AVX-512"]);
let availableIsas = [];
let enabledIsas = new Set();
let enableAllIsas = false;

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
  return String(v || "").replace(/{evex}\s*/ig, "").replaceAll("_EVEX", "").trim();
}

function tokens(v) {
  return String(v || "").replaceAll("_", " ").replaceAll(",", " ").replaceAll("{", " ").replaceAll("}", " ")
    .toLowerCase().match(/[a-z0-9]+/g) || [];
}

function displayIsa(values) {
  const seen = new Set();
  const out = [];
  for (const raw of (values || [])) {
    const v = String(raw || "");
    let base = v.replace(/_(128|256|512)$/i, "").replace(/_SCALAR$/i, "");
    const u = base.toUpperCase();
    if (u.startsWith("AVX10_")) {
      const parts = base.split("_");
      base = `AVX10.${parts[1]}${parts.length > 2 ? " " + parts.slice(2).join(" ") : ""}`;
    } else if (u.startsWith("AVX512")) {
      let suffix = base.slice(6);
      base = suffix.startsWith("_") ? `AVX512 ${suffix.slice(1).replaceAll("_", " ")}` : `AVX512${suffix}`;
    } else if (u.startsWith("AMX_")) {
      base = `AMX-${base.split("_", 2)[1].replaceAll("_", "-")}`;
    }
    if (!seen.has(base)) { seen.add(base); out.push(base); }
  }
  return out.join(", ") || "-";
}

function isaFamily(v) {
  const d = displayIsa([v]).toUpperCase().replaceAll(" ", "");
  if (!d || d === "-") return "Other";
  if (d.startsWith("APX")) return "APX";
  if (d.startsWith("AMX")) return "AMX";
  if (d.startsWith("AVX10")) return "AVX10";
  if (d.startsWith("AVX512")) return "AVX-512";
  if (d === "AVX2" || d === "AVX2GATHER") return "AVX2";
  if (d === "AVX" || d === "FMA" || d === "FMA4" || d === "F16C" || d === "XOP") return "AVX";
  if (d.startsWith("SSE") || d.startsWith("SSSE")) return "SSE";
  if (d.startsWith("MMX") || d === "3DNOW" || d === "PENTIUMMMX") return "MMX";
  if (d === "I86" || d === "I186" || d === "I386" || d === "I486" || d === "I586" || d.startsWith("BMI") || d === "ADX" || d === "AES" || d === "PCLMULQDQ" || d === "CRC32" || d === "CMOV" || d === "X87") return "x86";
  return "Other";
}

function isaFamilies(values) {
  return [...new Set((values || []).map(isaFamily).filter(Boolean))];
}

function isaVisible(values) {
  if (enableAllIsas) return true;
  const f = isaFamilies(values);
  return f.length > 0 && f.some(v => enabledIsas.has(v));
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
  const norms = (values && values.length ? values : ["-"]).map(v => displayIsa([v]).toUpperCase());
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
    entry.searchTokens = [...new Set(entry.fields.flatMap(f => tokens(f)))];
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
    cur = new Set([...cur].filter(i => allowed.has(i)));
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
      <h2>${esc(item.name)}</h2>
      <div class="detail-sub">${esc(detail ? detail.signature : item.signature)}</div>
      <div class="chips">
        ${(item.isa || []).map(v => `<span class="chip">${esc(displayIsa([v]))}</span>`).join("")}
        ${item.category ? `<span class="chip">${esc(item.category)}</span>` : ""}
        ${item.header ? `<span class="chip">${esc(item.header)}</span>` : ""}
      </div>
    </div>
    <section class="section">
      <h3>Description</h3>
      <div>${esc(detail ? detail.description : item.description)}</div>
    </section>
    <section class="section">
      <h3>Metadata</h3>
      <dl class="kv">
        <dt>Header</dt><dd>${esc(item.header || "-")}</dd>
        <dt>ISA</dt><dd>${esc(displayIsa(item.isa))}</dd>
        <dt>Category</dt><dd>${esc(item.category || "-")}</dd>
        ${detail && detail.notes && detail.notes.length ? `<dt>Notes</dt><dd>${esc(detail.notes.join("; "))}</dd>` : ""}
        ${detail && detail._url ? `<dt>uops.info</dt><dd><a href="${esc(detail._url)}" target="_blank" rel="noreferrer">${esc(detail._url)}</a></dd>` : ""}
        ${detail && detail._urlRef ? `<dt>Reference</dt><dd><a href="${esc(detail._urlRef)}" target="_blank" rel="noreferrer">${esc(detail._urlRef)}</a></dd>` : ""}
      </dl>
    </section>
    <section class="section">
      <h3>Instructions</h3>
      ${linked.length ? renderTable(
        [
          {key: "key", label: "Instruction", render: r => `<a class="xref" href="#${encodeURIComponent(r.key)}" data-kind="instruction" data-key="${esc(r.key)}">${esc(displayInstr(r.key))}</a>`},
          {key: "summary", label: "Summary"},
          {key: "isa", label: "ISA", render: r => esc(displayIsa(r.isa))},
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

  return `
    <div class="detail-head">
      <span class="result-kind instruction">instruction</span>
      <h2>${esc(displayInstr(item.key || item.mnemonic))}</h2>
      <div class="detail-sub">${esc(displayInstr(d.form || item.form || item.mnemonic))}</div>
      <div class="chips">
        ${(item.isa || []).map(v => `<span class="chip">${esc(displayIsa([v]))}</span>`).join("")}
        ${meta.category ? `<span class="chip">${esc(meta.category)}</span>` : ""}
        ${meta.cpl ? `<span class="chip">CPL ${esc(meta.cpl)}</span>` : ""}
      </div>
    </div>
    <section class="section">
      <h3>Description</h3>
      <div>${esc(d.summary || item.summary || "-")}</div>
    </section>
    <section class="section">
      <h3>Metadata</h3>
      <dl class="kv">
        <dt>Mnemonic</dt><dd class="mono">${esc(item.mnemonic)}</dd>
        <dt>Form</dt><dd class="mono">${esc(displayInstr(d.form || item.form || "-"))}</dd>
        <dt>ISA</dt><dd>${esc(displayIsa(item.isa))}</dd>
        <dt>Category</dt><dd>${esc(meta.category || "-")}</dd>
        ${meta.url ? `<dt>uops.info</dt><dd><a href="${esc(canonUrl(meta.url))}" target="_blank" rel="noreferrer">${esc(canonUrl(meta.url))}</a></dd>` : ""}
        ${meta["url-ref"] ? `<dt>Reference</dt><dd><a href="${esc(canonUrl(meta["url-ref"]))}" target="_blank" rel="noreferrer">${esc(canonUrl(meta["url-ref"]))}</a></dd>` : ""}
      </dl>
    </section>
    <section class="section">
      <h3>Intrinsics</h3>
      ${linked.length ? renderTable(
        [
          {key: "name", label: "Intrinsic", render: r => `<a class="xref" href="#${encodeURIComponent(r.name)}" data-kind="intrinsic" data-key="${esc(r.name)}">${esc(r.name)}</a>`},
          {key: "description", label: "Description"},
          {key: "isa", label: "ISA", render: r => esc(displayIsa(r.isa))},
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
        detail._url = instrDetail.metadata ? canonUrl(instrDetail.metadata.url) : "";
        detail._urlRef = instrDetail.metadata ? canonUrl(instrDetail.metadata["url-ref"]) : "";
      }
    }
    if (!detail) {
      const intrDetails = await loadIntrinsicDetails();
      detail = intrDetails[entry.item.name] || entry.item;
      detail._measurements = [];
      detail._operands = [];
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
const isaFamilyOrder = {
  "x86": 0, "MMX": 1, "SSE": 2, "AVX": 3, "AVX2": 4,
  "AVX-512": 5, "AVX10": 6, "AMX": 7, "APX": 8, "Other": 9,
};

function updateIsaSummary() {
  if (enableAllIsas) { isaSummary.textContent = "ISA: All"; return; }
  const active = availableIsas.filter(v => enabledIsas.has(v));
  if (!active.length) { isaSummary.textContent = "ISA: None"; return; }
  if (active.length <= 3) { isaSummary.textContent = "ISA: " + active.join(", "); return; }
  isaSummary.textContent = `ISA: ${active.slice(0, 2).join(", ")} +${active.length - 2}`;
}

function renderIsaFilters() {
  // Group ISAs by family
  const groups = new Map();
  for (const isa of availableIsas) {
    if (!groups.has(isa)) groups.set(isa, isa);
  }
  isaGroups.innerHTML = availableIsas.map(isa => `
    <label class="isa-chip ${enabledIsas.has(isa) || enableAllIsas ? "active" : ""}">
      <input type="checkbox" data-isa="${esc(isa)}" ${enabledIsas.has(isa) || enableAllIsas ? "checked" : ""}>
      ${esc(isa)}
    </label>
  `).join("");

  for (const cb of isaGroups.querySelectorAll("input[data-isa]")) {
    cb.addEventListener("change", () => {
      const isa = cb.dataset.isa;
      if (cb.checked) enabledIsas.add(isa); else enabledIsas.delete(isa);
      enableAllIsas = false;
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
  }
  renderIsaFilters();
  renderResults();
}

/* ── Results rendering ────────────────────────────────────────────── */
function renderResults() {
  const query = queryInput.value.trim();
  if (!catalog) return;
  const visible = searchEntries.filter(e => isaVisible(e.item.isa));

  if (!query) {
    resultPool = [
      ...visible.filter(e => e.kind === "intrinsic").slice(0, 20),
      ...visible.filter(e => e.kind === "instruction").slice(0, 20),
    ];
  } else {
    const cids = candidateIndexes(query);
    const pool = cids == null ? visible : cids.map(i => searchEntries[i]).filter(Boolean).filter(e => isaVisible(e.item.isa));
    resultPool = pool
      .map(e => ({...e, score: rankEntry(query, e)}))
      .filter(e => Number.isFinite(e.score) && e.score >= 35)
      .sort((a, b) =>
        b.score - a.score
        || (a.kind !== b.kind ? (a.kind === "instruction" ? -1 : 1) : 0)
        || (() => { const [aa,ab] = isaSortKey(a.item.isa); const [ba,bb] = isaSortKey(b.item.isa); return aa-ba||ab-bb; })()
        || naturalCmp(a.title, b.title)
        || a.title.length - b.title.length
      )
      .slice(0, 50);
  }

  resultsCount.textContent = query
    ? `${resultPool.length} result${resultPool.length !== 1 ? "s" : ""} for "${query}"`
    : `${resultPool.length} results`;

  resultsNode.innerHTML = resultPool.map((e, i) => `
    <article class="result ${e.key === activeKey ? "active" : ""} ${i === focusedIndex ? "focused" : ""}" data-key="${esc(e.key)}" data-index="${i}">
      <div class="result-top">
        <span class="result-kind ${e.kind}">${esc(e.kind)}</span>
        <span class="result-isa">${esc(displayIsa(e.item.isa))}</span>
      </div>
      <div class="result-title">${esc(e.kind === "instruction" ? displayInstr(e.title) : e.title)}</div>
      <div class="result-meta">
        ${e.item.lat && e.item.lat !== "-" ? `<span class="result-perf">LAT ${esc(e.item.lat)}</span>` : ""}
        ${e.item.cpi && e.item.cpi !== "-" ? `<span class="result-perf">CPI ${esc(e.item.cpi)}</span>` : ""}
      </div>
      <div class="result-summary">${esc(e.subtitle || "")}</div>
    </article>
  `).join("");

  for (const node of resultsNode.querySelectorAll(".result")) {
    node.addEventListener("click", () => {
      const idx = parseInt(node.dataset.index);
      focusedIndex = idx;
      renderDetail(resultPool[idx]);
    });
  }

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
      <span class="ac-name">${esc(e.kind === "instruction" ? displayInstr(e.title) : e.title)}</span>
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
  queryInput.value = entry.kind === "instruction" ? displayInstr(entry.title) : entry.title;
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

  // / or Ctrl+K: focus search
  if ((e.key === "/" || (e.key === "k" && (e.ctrlKey || e.metaKey))) && !inInput) {
    e.preventDefault();
    queryInput.focus();
    queryInput.select();
    return;
  }

  // In search input: arrow keys navigate autocomplete, then results
  if (inInput && document.activeElement === queryInput) {
    const acOpen = !acNode.classList.contains("hidden") && acItems.length;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (acOpen) highlightAcItem(Math.min(acIndex + 1, acItems.length - 1));
      else focusResult(Math.min(focusedIndex + 1, resultPool.length - 1));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      if (acOpen && acIndex > 0) highlightAcItem(acIndex - 1);
      else if (acOpen && acIndex === 0) { acIndex = -1; for (const el of acNode.querySelectorAll(".ac-item")) el.classList.remove("ac-active"); }
      else focusResult(Math.max(focusedIndex - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (acOpen && acIndex >= 0) { selectAcItem(acIndex); }
      else { hideAutocomplete(); renderResults(); }
      return;
    }
    return;
  }

  if (inInput) return;

  // d: toggle dark mode
  if (e.key === "d") { toggleTheme(); return; }

  // f: toggle ISA filter
  if (e.key === "f") { isaPanel.classList.toggle("hidden"); return; }

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

  // h: back to search
  if (e.key === "h") { queryInput.focus(); queryInput.select(); return; }
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
    if (item) entry = { kind: "intrinsic", key: item.name, title: item.name, subtitle: item.description, item, fields: [] };
  } else {
    const item = catalog.instrByKey[key];
    if (item) entry = { kind: "instruction", key: item.key, title: displayInstr(item.key), subtitle: item.summary, item, fields: [] };
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
queryInput.addEventListener("blur", () => setTimeout(hideAutocomplete, 150));
queryInput.addEventListener("focus", () => { if (queryInput.value.trim().length >= 2) updateAutocomplete(); });

/* ── Hash navigation ──────────────────────────────────────────────── */
window.addEventListener("hashchange", () => {
  const key = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (!catalog || !key) return;
  const entry = resultPool.find(e => e.key === key)
    || (catalog.intrByName[key] ? {kind: "intrinsic", key, title: key, subtitle: catalog.intrByName[key].description, item: catalog.intrByName[key], fields: []} : null)
    || (catalog.instrByKey[key] ? {kind: "instruction", key, title: displayInstr(key), subtitle: catalog.instrByKey[key].summary, item: catalog.instrByKey[key], fields: []} : null);
  if (entry) renderDetail(entry);
});

/* ── Bootstrap ────────────────────────────────────────────────────── */
fetch("search-index.json")
  .then(r => r.json())
  .then(data => {
    catalog = data;
    catalog.intrByName = Object.fromEntries(data.intrinsics.map(i => [i.name, i]));
    catalog.instrByKey = Object.fromEntries(data.instructions.map(i => [i.key, i]));
    catalog.instrByMnem = Object.fromEntries(data.instructions.map(i => [i.mnemonic, i]));

    searchEntries = [
      ...data.intrinsics.map(i => ({
        kind: "intrinsic", key: i.name, title: i.name, subtitle: i.description, item: i,
        fields: [i.name, i.signature || "", i.description || "", i.header || "", displayIsa(i.isa), (i.instructions || []).join(" ")],
      })),
      ...data.instructions.map(i => ({
        kind: "instruction", key: i.key, title: displayInstr(i.key), subtitle: i.summary, item: i,
        fields: [i.mnemonic || "", i.form || "", i.summary || "", displayIsa(i.isa), (i.linked_intrinsics || []).join(" ")],
      })),
    ];

    buildSearchIndexes(searchEntries);

    availableIsas = [...new Set(searchEntries.flatMap(e => isaFamilies(e.item.isa)))]
      .sort((a, b) => (isaFamilyOrder[a] ?? 99) - (isaFamilyOrder[b] ?? 99) || a.localeCompare(b));
    enabledIsas = new Set([...defaultEnabledIsas].filter(v => availableIsas.includes(v)));

    renderIsaFilters();
    metaNode.textContent = `${data.intrinsics.length} intrinsics \u00b7 ${data.instructions.length} instructions`;

    const fromHash = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (fromHash) queryInput.value = fromHash;
    renderResults();
  });
