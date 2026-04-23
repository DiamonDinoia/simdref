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
const categoryPanel  = $("category-panel");
const categoryChips  = $("category-chips");
const categorySummary = $("category-summary");
const themeToggle    = $("theme-toggle");
const themeIconLight = $("theme-icon-light");
const themeIconDark  = $("theme-icon-dark");
const shortcutsOverlay = $("shortcuts-overlay");

/* ── State ────────────────────────────────────────────────────────── */
let catalog = null;            // search-index.json payload
let searchEntries = [];
let searchTokenIndex = new Map();
let searchPrefixIndex = new Map();
let resultPool = [];
let activeKey = null;
let focusedIndex = -1;
let renderTimer = null;
let visibleSet = null;
let filterRenderScheduled = false;
let renderedCount = 0;
let loadMoreScheduled = false;
const INITIAL_RENDER_BATCH = 50;
const RENDER_BATCH_SIZE = 10;
const LOAD_MORE_THRESHOLD_PX = 600;

/* Viewport virtualisation — keep only rows inside the visible window
 * (plus a small buffer) in the DOM. Row height must match .result in
 * style.css. */
const ROW_HEIGHT_PX = 88;
const VIEWPORT_BUFFER_ROWS = 30;
let virtualWrap = null;
let virtualRange = { start: -1, end: -1 };

/* Detail chunk caches: prefix -> Promise<data>. Separate maps for
 * instructions (detail-chunks/) and intrinsics (intrinsic-chunks/) so
 * colliding prefixes (e.g. "ADD") don't shadow each other. */
const chunkCache = new Map();
const intrinsicChunkCache = new Map();

/* ISA state */
let defaultEnabledIsas = new Set(["SSE", "AVX", "AVX-512"]);
let availableIsas = [];
let enabledIsas = new Set();
let enableAllIsas = false;
let enabledSubIsas = new Map();

/* Category state — null means "all enabled" (no filter) */
let availableCategories = [];   // [{family, category, subcategory, count}]
let enabledCategories = null;

/* Kind (intrinsic vs instruction/asm) filter — both enabled by default. */
const enabledKinds = new Set(["intrinsic", "instruction"]);

/* Perf source-kind filter — toggle modeled vs measured perf tables.
   Persisted to localStorage like the theme so the choice survives reloads. */
const _storedPerfKinds = (() => {
  try {
    const raw = localStorage.getItem("simdref-perf-kinds");
    if (raw) return new Set(JSON.parse(raw));
  } catch (_) { /* ignore */ }
  return new Set(["measured", "modeled"]);
})();
const enabledPerfKinds = _storedPerfKinds;
function persistPerfKinds() {
  try {
    localStorage.setItem("simdref-perf-kinds", JSON.stringify([...enabledPerfKinds]));
  } catch (_) { /* ignore */ }
}

let FAMILY_SUB_ORDER = {};
let DEFAULT_SUBS = {};
let isaFamilyOrder = {};
let ARCH_PRESETS = {};
let enabledArmArch = null; // null = no filter; Set of "A32"|"A64"|"BOTH" when active

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
  // Swap the hljs stylesheet if it's been loaded.
  const light = document.getElementById("hljs-theme-light");
  const dark  = document.getElementById("hljs-theme-dark");
  if (light) light.disabled = theme === "dark";
  if (dark)  dark.disabled  = theme !== "dark";
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

function categoryVisible(item) {
  if (enabledCategories === null) return true;
  const cat = item.category || (item.metadata && item.metadata.category) || "";
  if (!cat) return enabledCategories.has("");
  return enabledCategories.has(cat);
}

function armArchVisible(item) {
  if (!enabledArmArch) return true;
  // Only intrinsics carry arm_arch classification; instructions are not filtered.
  if (!(item.isa_families || []).includes("Arm")) return true;
  const bucket = item.arm_arch || null;
  return bucket !== null && enabledArmArch.has(bucket);
}

/* ── Bucket indexes (built once after load) ─────────────────────────
 * Filter toggles intersect small buckets instead of scanning ~140k
 * entries. Buckets:
 *   byKind:     "intrinsic" | "instruction"       -> entry indexes
 *   byFamily:   family name                        -> entry indexes
 *   byCategory: category name (or "")              -> entry indexes
 *   byArmArch:  "A32" | "A64" | "BOTH" | "__none"  -> entry indexes
 */
let bucketsBuilt = false;
const byKind = new Map();
const byFamily = new Map();
const byCategory = new Map();
const byArmArch = new Map();

function _pushBucket(map, key, i) {
  let arr = map.get(key);
  if (!arr) { arr = []; map.set(key, arr); }
  arr.push(i);
}

function buildBuckets() {
  byKind.clear(); byFamily.clear(); byCategory.clear(); byArmArch.clear();
  for (let i = 0; i < searchEntries.length; i++) {
    const e = searchEntries[i];
    _pushBucket(byKind, e.kind, i);
    const fams = e.item.isa_families || [];
    if (fams.length === 0) _pushBucket(byFamily, "__none", i);
    for (const f of fams) _pushBucket(byFamily, f, i);
    const cat = e.item.category || "";
    _pushBucket(byCategory, cat, i);
    const arch = e.item.arm_arch || "__none";
    _pushBucket(byArmArch, arch, i);
  }
  bucketsBuilt = true;
}

function _unionKeys(map, keys) {
  const out = new Set();
  for (const k of keys) {
    const list = map.get(k);
    if (!list) continue;
    for (const i of list) out.add(i);
  }
  return out;
}

function rebuildVisibleSet() {
  if (!bucketsBuilt) buildBuckets();
  // Kind filter: union of enabled-kind buckets.
  let cand = _unionKeys(byKind, [...enabledKinds]);

  // ISA family/sub-family filter — keep the existing predicate for the
  // sub-ISA case (FAMILY_SUB_ORDER), but short-circuit at the family
  // bucket first to avoid scanning every row.
  if (!enableAllIsas) {
    const famCand = _unionKeys(byFamily, [...enabledIsas]);
    cand = _intersect(cand, famCand);
    // Sub-ISA filtering — may still require a per-entry check when
    // enabledSubIsas doesn't match the family default.
    const needsSubCheck = [...enabledIsas].some(f => FAMILY_SUB_ORDER[f]);
    if (needsSubCheck) {
      cand = _filterSet(cand, (i) => isaVisible(searchEntries[i].item));
    }
  }

  if (enabledCategories !== null) {
    const catCand = _unionKeys(byCategory, [...enabledCategories]);
    cand = _intersect(cand, catCand);
  }

  if (enabledArmArch) {
    // Non-Arm entries are never filtered out. Union of:
    //   * arm_arch bucket entries (Arm, bucketed by A32/A64/BOTH)
    //   * entries whose families don't include "Arm"
    const armCand = _unionKeys(byArmArch, [...enabledArmArch]);
    const nonArm = _filterSet(cand, (i) => !(searchEntries[i].item.isa_families || []).includes("Arm"));
    // armCand ∩ cand, ∪ nonArm
    const intersect = new Set();
    for (const i of armCand) if (cand.has(i)) intersect.add(i);
    for (const i of nonArm) intersect.add(i);
    cand = intersect;
  }

  visibleSet = cand;
}

function _intersect(a, b) {
  const [small, big] = a.size <= b.size ? [a, b] : [b, a];
  const out = new Set();
  for (const i of small) if (big.has(i)) out.add(i);
  return out;
}

function _filterSet(s, pred) {
  const out = new Set();
  for (const i of s) if (pred(i)) out.add(i);
  return out;
}

function resultMarkup(entries, offset = 0) {
  return entries.map((e, i) => `
    <article class="result ${e.kind}-kind ${e.key === activeKey ? "active" : ""} ${offset + i === focusedIndex ? "focused" : ""}" data-key="${esc(e.key)}" data-index="${offset + i}">
      <div class="result-top">
        <span class="result-kind ${e.kind}">${esc(e.kind)}</span>
        <span class="result-isa">${esc(e.item.display_architecture || e.item.architecture || "-")}</span>
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
  } else if (catalog && Array.isArray(catalog.intrinsics) && Array.isArray(catalog.instructions)) {
    // Empty-query hint: show the catalog size so visitors know the index loaded.
    const nI = catalog.intrinsics.length;
    const nA = catalog.instructions.length;
    resultsCount.textContent = shown < resultPool.length
      ? `Showing ${shown} of ${resultPool.length} (${nI} intrinsics · ${nA} instructions)`
      : `Type to search ${nI} intrinsics · ${nA} instructions`;
  } else {
    resultsCount.textContent = shown < resultPool.length
      ? `Showing ${shown} of ${resultPool.length} results`
      : `${resultPool.length} results`;
  }
}

function _ensureVirtualWrap() {
  if (virtualWrap && virtualWrap.isConnected) return virtualWrap;
  resultsNode.innerHTML = '<div class="results-virtual"></div>';
  virtualWrap = resultsNode.firstElementChild;
  return virtualWrap;
}

function _visibleRowRange() {
  const top = resultsNode.scrollTop;
  const h = resultsNode.clientHeight;
  const first = Math.max(0, Math.floor(top / ROW_HEIGHT_PX) - VIEWPORT_BUFFER_ROWS);
  const last = Math.min(resultPool.length, Math.ceil((top + h) / ROW_HEIGHT_PX) + VIEWPORT_BUFFER_ROWS);
  return [first, last];
}

function _rowMarkup(entry, index) {
  return `<article class="result ${entry.kind}-kind ${entry.key === activeKey ? "active" : ""} ${index === focusedIndex ? "focused" : ""}" data-key="${esc(entry.key)}" data-index="${index}" style="top:${index * ROW_HEIGHT_PX}px">
      <div class="result-top">
        <span class="result-kind ${entry.kind}">${esc(entry.kind)}</span>
        <span class="result-isa">${esc(entry.item.display_architecture || entry.item.architecture || "-")}</span>
        <span class="result-isa">${esc(entry.item.display_isa || displayIsa(entry.item.isa))}</span>
      </div>
      <div class="result-title">${esc(entry.title)}</div>
      <div class="result-meta">
        ${entry.item.lat && entry.item.lat !== "-" ? `<span class="result-perf">LAT ${esc(entry.item.lat)}</span>` : ""}
        ${entry.item.cpi && entry.item.cpi !== "-" ? `<span class="result-perf">CPI ${esc(entry.item.cpi)}</span>` : ""}
      </div>
      <div class="result-summary">${esc(entry.subtitle || "")}</div>
    </article>`;
}

function renderVisibleResults(reset = false) {
  const wrap = _ensureVirtualWrap();
  wrap.style.height = (resultPool.length * ROW_HEIGHT_PX) + "px";
  const [first, last] = _visibleRowRange();
  if (reset || first !== virtualRange.start || last !== virtualRange.end) {
    let html = "";
    for (let i = first; i < last; i++) html += _rowMarkup(resultPool[i], i);
    wrap.innerHTML = html;
    virtualRange = { start: first, end: last };
  }
  renderedCount = last;
}

function loadMoreResults() {
  // With virtualisation there's no incremental "load more" — the
  // viewport already shows what's needed. Kept for API parity.
  renderVisibleResults(false);
  syncResultsCount(queryInput.value.trim());
}

function maybeLoadMoreResults() {
  renderVisibleResults(false);
}

/* ── ISA sort key (chronological) ─────────────────────────────────── */
const chronology = {
  "I86": [0,0], "MMX": [1,0],
  "SSE": [2,0], "SSE2": [2,1], "SSE3": [2,2], "SSSE3": [2,3], "SSE4A": [2,4], "SSE4.1": [2,5], "SSE4.2": [2,6], "AES": [2,7], "PCLMULQDQ": [2,8],
  "F16C": [3,0], "FMA": [3,1],
  "AVX": [4,0], "NEON": [4,1], "AVX2": [5,0],
  "AVX512F": [6,0], "AVX512DQ": [6,1], "AVX512IFMA": [6,2], "AVX512PF": [6,3], "AVX512ER": [6,4], "AVX512CD": [6,5], "AVX512BW": [6,6], "AVX512VL": [6,7],
  "AVX512VBMI": [6,8], "AVX512VBMI2": [6,9], "AVX512VNNI": [6,10], "AVX512BITALG": [6,11], "AVX512VPOPCNTDQ": [6,12],
  "AVX512FP16": [6,99], "AVX10": [7,0], "AMX": [7,0], "SVE": [8,0], "SVE2": [8,1], "APX": [9,0],
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

/* ── gzip-aware fetch ─────────────────────────────────────────────────
 * Prefer a pre-compressed *.gz sidecar (so GitHub Pages & bare static
 * hosts don't need Content-Encoding: gzip), fall back to the raw .json.
 */
async function fetchJson(path) {
  const base = path.replace(/\.json$/, "");
  if (typeof DecompressionStream !== "undefined") {
    try {
      const r = await fetch(`${base}.json.gz`);
      if (r.ok) {
        const ds = r.body.pipeThrough(new DecompressionStream("gzip"));
        return await new Response(ds).json();
      }
    } catch (_) { /* fall through */ }
  }
  const r = await fetch(`${base}.json`);
  return r.ok ? r.json() : null;
}

/* ── Lazy detail loading ──────────────────────────────────────────── */
function chunkPrefix(mnemonic) {
  const c = (mnemonic || "").trim().toUpperCase();
  return c.length >= 3 ? c.slice(0, 3) : c;
}

/* Keep in sync with simdref.web._intrinsic_chunk_prefix. */
function intrinsicChunkPrefix(name) {
  let s = String(name || "").replace(/^_+/, "");
  if (s.slice(0, 6).toLowerCase() === "riscv_") s = s.slice(6);
  const m = s.match(/^(?:mm\d*|sv|vq?)_?/i);
  if (m && m[0].length < s.length) s = s.slice(m[0].length);
  s = s.replace(/^_+/, "");
  const clean = (s.match(/[a-z0-9]/gi) || []).join("").toLowerCase();
  if (clean.length < 2) return "misc";
  return clean.slice(0, 3);
}

function loadChunk(prefix) {
  if (chunkCache.has(prefix)) return chunkCache.get(prefix);
  const p = fetchJson(`detail-chunks/${encodeURIComponent(prefix)}.json`)
    .then(data => data || {})
    .catch(() => ({}));
  chunkCache.set(prefix, p);
  return p;
}

function loadIntrinsicChunk(prefix) {
  if (intrinsicChunkCache.has(prefix)) return intrinsicChunkCache.get(prefix);
  const p = fetchJson(`intrinsic-chunks/${encodeURIComponent(prefix)}.json`)
    .then(data => data || {})
    .catch(() => ({}));
  intrinsicChunkCache.set(prefix, p);
  return p;
}

/* Fetch one intrinsic's full details (lazy, per-bucket). Returns the
 * detail entry or null if not found in its bucket (build skew). */
async function loadIntrinsic(name) {
  const chunk = await loadIntrinsicChunk(intrinsicChunkPrefix(name));
  return chunk[name] || null;
}

/* Warm intrinsic-chunks for the top N visible hits during browser
 * idle time so clicking a result doesn't pay a network round-trip. */
function prefetchIntrinsicChunks(entries) {
  const schedule = window.requestIdleCallback || ((cb) => setTimeout(cb, 0));
  const prefixes = new Set();
  for (const e of entries) {
    if (!e || e.kind !== "intrinsic") continue;
    prefixes.add(intrinsicChunkPrefix(e.item.name));
    if (prefixes.size >= 4) break;
  }
  for (const prefix of prefixes) {
    if (intrinsicChunkCache.has(prefix)) continue;
    schedule(() => { loadIntrinsicChunk(prefix); });
  }
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

function splitMeasurements(measurements) {
  /* Bucket rows by sourceKind in a stable order (measured first).
     Mirrors split_perf_rows in src/simdref/display.py. */
  const buckets = new Map();
  for (const row of measurements) {
    const kind = row.sourceKind || "measured";
    if (!buckets.has(kind)) buckets.set(kind, []);
    buckets.get(kind).push(row);
  }
  const order = ["measured", "modeled"];
  const out = [];
  for (const kind of order) {
    if (buckets.has(kind)) out.push([kind, buckets.get(kind)]);
  }
  for (const [kind, rows] of buckets) {
    if (!order.includes(kind)) out.push([kind, rows]);
  }
  return out;
}

const perfKindLabels = {measured: "perf (measured)", modeled: "perf (modeled)"};
const perfKindBorders = {measured: "var(--accent-green, #2ea043)", modeled: "var(--accent-yellow, #bf8700)"};

function renderMeasurements(measurements) {
  if (!measurements || !measurements.length) return `<div style="color:var(--text-muted);font-size:0.82rem">No performance data.</div>`;
  const splits = splitMeasurements(measurements).filter(([kind]) => enabledPerfKinds.has(kind));
  if (!splits.length) return `<div style="color:var(--text-muted);font-size:0.82rem">All perf sources disabled.</div>`;
  let html = "";
  for (const [kind, rows] of splits) {
    const label = perfKindLabels[kind] || `perf (${kind})`;
    const color = perfKindBorders[kind] || "var(--accent-green, #2ea043)";
    const groups = groupMeasurements(rows);
    let inner = "";
    for (const [family, frows] of groups) {
      inner += `<details class="meas-group" open><summary>${esc(family)} (${frows.length})</summary>${renderTable(measHeaders, frows)}</details>`;
    }
    html += `<section class="perf-panel" style="border-left:3px solid ${color};padding-left:0.6rem;margin-bottom:0.8rem">
      <h4 style="margin:0 0 0.3rem 0;color:${color};font-size:0.85rem">${esc(label)} (${rows.length})</h4>
      ${inner}
    </section>`;
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
      html += `<details class="desc-section">
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
  const linkedRefs = detail ? (detail.instruction_refs || item.instruction_refs || []) : (item.instruction_refs || []);
  const linked = (linkedRefs.length
    ? linkedRefs.map(ref => catalog.instrByKey[ref.key] || catalog.instrByDisplayKey[ref.display_key] || catalog.instrByMnem[ref.name])
    : (item.instructions || []).map(k => catalog.instrByDisplayKey[k] || catalog.instrByKey[k] || catalog.instrByMnem[k]))
    .filter(Boolean).filter(r => isaVisible(r.isa));

  const hasMeasurements = detail && detail._measurements && detail._measurements.length;
  const hasOperands = detail && detail._operands && detail._operands.length;
  const meta = detail ? (detail.metadata || {}) : (item.metadata || {});
  const instrMeta = detail ? (detail._instructionMeta || {}) : {};
  const instrPdfRefs = detail ? (detail._pdfRefs || []) : [];

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
      <h3>Summary</h3>
      <div>${esc(detail ? detail.description : item.description)}</div>
    </section>
    <section class="section">
      <h3>Metadata</h3>
      <div class="kv-compact">
        ${kvChip("Arch", item.display_architecture || item.architecture)}
        ${kvChip("ISA", item.display_isa || displayIsa(item.isa))}
        ${item.header ? kvChip("Header", item.header) : ""}
        ${item.category ? kvChip("Category", item.subcategory ? `${item.subcategory} / ${item.category}` : item.category) : ""}
        ${instrMeta.category ? kvChip("Instr Cat", instrMeta.category) : ""}
        ${instrMeta.cpl ? kvChip("CPL", instrMeta.cpl) : ""}
        ${meta.supported_architectures ? kvChip("Supported", meta.supported_architectures) : ""}
        ${meta.classification_path ? kvChip("Section", meta.classification_path) : ""}
        ${detail && detail._linkedInstruction ? kvChip("Instruction", detail._linkedInstruction) : ""}
        ${meta.argument_preparation ? kvChip("Arg Prep", meta.argument_preparation) : ""}
        ${meta.result ? kvChip("Result", meta.result) : ""}
        ${detail && detail.notes && detail.notes.length ? kvChip("Notes", detail.notes.join("; ")) : ""}
      </div>
      <div class="kv-links">
        ${detail && detail.url ? kvLink("Source", detail.url) : ""}
        ${meta.reference_url ? kvLink("Reference", meta.reference_url) : ""}
        ${instrMeta.url ? kvLink("uops.info", canonUrl(instrMeta.url)) : ""}
        ${instrMeta["url-ref"] ? kvLink("Instr Ref", canonUrl(instrMeta["url-ref"])) : ""}
        ${instrPdfRefs.map(ref => `<a class="kv-link" href="${esc(ref.url || "")}" target="_blank" rel="noreferrer">${esc(ref.label || ref.source_id || "PDF")}${ref.page_start ? ` p${esc(ref.page_start)}` : ""}</a>`).join("")}
      </div>
    </section>
    ${hasOperands ? `<section class="section">
      <h3>Operands</h3>
      ${renderTable(operandHeaders, detail._operands)}
    </section>` : ""}
    ${hasMeasurements ? `<section class="section">
      <h3>Performance</h3>
      ${renderMeasurements(detail._measurements)}
    </section>` : ""}
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
    ${detail && detail.doc_sections && Object.keys(detail.doc_sections).length ? `<section class="section">
      <h3>ACLE Documentation</h3>
      ${renderDescriptionSections(detail.doc_sections)}
    </section>` : ""}
    ${detail && detail._instrDescription && Object.keys(detail._instrDescription).length ? `<section class="section">
      <h3>Instruction Semantics</h3>
      ${renderDescriptionSections(detail._instrDescription)}
    </section>` : ""}
  `;
}

function kvChip(label, value) {
  if (value == null || value === "" || value === "-") return "";
  return `<span class="kv-chip"><span class="kv-chip-k">${esc(label)}</span><span class="kv-chip-v">${esc(value)}</span></span>`;
}
function kvLink(label, href) {
  return `<a class="kv-link" href="${esc(href)}" target="_blank" rel="noreferrer">${esc(label)}</a>`;
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
    <section class="section">
      <h3>Metadata</h3>
      <div class="kv-compact">
        ${kvChip("Mnemonic", item.display_mnemonic || item.mnemonic)}
        ${kvChip("Form", d.display_form || item.display_form || d.form || item.form)}
        ${kvChip("Arch", item.display_architecture || item.architecture)}
        ${kvChip("ISA", item.display_isa || displayIsa(item.isa))}
        ${meta.category ? kvChip("Category", meta.category) : ""}
        ${meta.cpl ? kvChip("CPL", meta.cpl) : ""}
      </div>
      <div class="kv-links">
        ${meta.url ? kvLink("uops.info", canonUrl(meta.url)) : ""}
        ${meta["url-ref"] ? kvLink("Reference", canonUrl(meta["url-ref"])) : ""}
        ${pdfRefs.map(ref => `<a class="kv-link" href="${esc(ref.url || "")}" target="_blank" rel="noreferrer">${esc(ref.label || ref.source_id || "PDF")}${ref.page_start ? ` p${esc(ref.page_start)}` : ""}</a>`).join("")}
      </div>
    </section>
    ${operands.length ? `<section class="section">
      <h3>Operands</h3>
      ${renderTable(operandHeaders, operands)}
    </section>` : ""}
    ${measurements.length ? `<section class="section">
      <h3>Performance</h3>
      ${renderMeasurements(measurements)}
    </section>` : ""}
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
    ${d.description && Object.keys(d.description).length ? `<section class="section">
      <h3>Instruction Semantics</h3>
      ${renderDescriptionSections(d.description)}
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
    const primaryKey = entry.item.primary_instr || (entry.item.instruction_refs || [])[0]?.key || (entry.item.instructions || [])[0];
    let detail = null;
    if (primaryKey) {
      const instr = catalog.instrByDisplayKey[primaryKey] || catalog.instrByKey[primaryKey] || catalog.instrByMnem[primaryKey];
      if (instr) {
        const prefix = chunkPrefix(instr.mnemonic);
        const chunk = await loadChunk(prefix);
        const instrDetail = chunk[instr.key] || chunk[instr.display_key] || chunk[primaryKey] || {};
        // Load this intrinsic's chunk lazily; fall back to the
        // search-index entry if the chunk doesn't carry it.
        const full = await loadIntrinsic(entry.item.name);
        detail = full || entry.item;
        detail._measurements = instrDetail.measurements || [];
        detail._operands = instrDetail.operand_details || [];
        detail._instrDescription = instrDetail.description || {};
        detail._instructionMeta = instrDetail.metadata || {};
        detail._linkedInstruction = instrDetail.display_form || instr.display_key || instr.key || "";
        detail._pdfRefs = instrDetail.pdf_refs || [];
      }
    }
    if (!detail) {
      const full = await loadIntrinsic(entry.item.name);
      detail = full || entry.item;
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

/* ── Category Filters ─────────────────────────────────────────────── */

function updateCategorySummary() {
  if (!categorySummary) return;
  if (enabledCategories === null) {
    categorySummary.textContent = "Category: All";
    return;
  }
  const n = enabledCategories.size;
  if (n === 0) { categorySummary.textContent = "Category: None"; return; }
  if (n <= 2) {
    categorySummary.textContent = "Category: " + [...enabledCategories].map(c => c || "(none)").join(", ");
    return;
  }
  categorySummary.textContent = `Category: ${n} selected`;
}

function renderCategoryFilters() {
  if (!categoryChips) return;
  // De-duplicate categories across families (same name, aggregate count).
  const bucket = new Map();
  for (const spec of availableCategories) {
    const cat = spec.category || "";
    const prev = bucket.get(cat) || { category: cat, count: 0, families: new Set() };
    prev.count += spec.count || 0;
    if (spec.family) prev.families.add(spec.family);
    bucket.set(cat, prev);
  }
  const entries = [...bucket.values()].sort((a, b) => b.count - a.count);
  categoryChips.innerHTML = entries.map(entry => {
    const active = enabledCategories === null || enabledCategories.has(entry.category);
    const label = entry.category || "(uncategorised)";
    return `<label class="isa-chip ${active ? "active" : ""}" title="${esc([...entry.families].join(", "))}">
      <input type="checkbox" data-category="${esc(entry.category)}" ${active ? "checked" : ""}>
      ${esc(label)}
      <span class="chip-count">${entry.count}</span>
    </label>`;
  }).join("");

  for (const cb of categoryChips.querySelectorAll("input[data-category]")) {
    cb.addEventListener("change", () => {
      const cat = cb.dataset.category;
      if (enabledCategories === null) {
        // First interaction: switch from "all" to an explicit set.
        enabledCategories = new Set(entries.map(e => e.category));
      }
      if (cb.checked) enabledCategories.add(cat); else enabledCategories.delete(cat);
      visibleSet = null;
      scheduleFilterRender();
    });
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
      scheduleFilterRender();
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
      scheduleFilterRender();
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
      scheduleFilterRender();
    });
  }
  updateIsaSummary();
}

function applyIsaPreset(mode) {
  const preset = ARCH_PRESETS[mode];
  if (!preset) return;

  if (mode === "all") {
    enableAllIsas = true;
    enabledIsas = new Set(availableIsas);
    initEnabledSubIsas();
  } else {
    enableAllIsas = false;
    enabledIsas = new Set((preset.families || []).filter(v => availableIsas.includes(v)));
    // Apply preset subs per family (intersect with FAMILY_SUB_ORDER).
    enabledSubIsas = new Map();
    const presetSubs = new Set(preset.subs || []);
    for (const family of availableIsas) {
      const subs = FAMILY_SUB_ORDER[family];
      if (!subs) continue;
      const keep = new Set(subs.filter(s => presetSubs.has(s)));
      enabledSubIsas.set(family, keep);
    }
  }

  // arm_arch facet
  enabledArmArch = preset.arm_arch ? new Set(preset.arm_arch) : null;

  // kind facet
  enabledKinds.clear();
  for (const k of (preset.kind || ["intrinsic", "instruction"])) enabledKinds.add(k);
  // Reflect kind state in the checkboxes.
  for (const cb of document.querySelectorAll('#kind-bar input[data-kind]')) {
    cb.checked = enabledKinds.has(cb.dataset.kind);
    const chip = cb.closest(".isa-chip");
    if (chip) chip.classList.toggle("active", cb.checked);
  }

  visibleSet = null;
  scheduleFilterRender();
}

/* ── Results rendering ────────────────────────────────────────────── */
function scheduleFilterRender() {
  if (filterRenderScheduled) return;
  filterRenderScheduled = true;
  requestAnimationFrame(() => {
    filterRenderScheduled = false;
    rebuildVisibleSet();
    if (typeof updateIsaSummary === "function") updateIsaSummary();
    if (typeof updateCategorySummary === "function") updateCategorySummary();
    renderIsaFilters();
    if (typeof renderCategoryFilters === "function") renderCategoryFilters();
    renderResults();
  });
}

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

  renderedCount = 0;
  virtualRange = { start: -1, end: -1 };
  resultsNode.scrollTop = 0;
  syncResultsCount(query);
  renderVisibleResults(true);
  prefetchIntrinsicChunks(resultPool.slice(0, 16));

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
  renderTimer = setTimeout(() => { renderTimer = null; renderResults(); }, 90);
}

/* ── Keyboard navigation ──────────────────────────────────────────── */
function focusResult(idx) {
  if (idx < 0 || idx >= resultPool.length) return;
  focusedIndex = idx;
  // Scroll target row into the viewport, leaving a row's worth of margin
  // on either edge so it's not clipped.
  const rowTop = idx * ROW_HEIGHT_PX;
  const viewTop = resultsNode.scrollTop;
  const viewBottom = viewTop + resultsNode.clientHeight;
  if (rowTop < viewTop) resultsNode.scrollTop = rowTop;
  else if (rowTop + ROW_HEIGHT_PX > viewBottom) resultsNode.scrollTop = rowTop - resultsNode.clientHeight + ROW_HEIGHT_PX;
  renderVisibleResults(false);
  for (const n of resultsNode.querySelectorAll(".result")) {
    n.classList.toggle("focused", parseInt(n.dataset.index) === idx);
  }
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

  // Escape: close overlays, blur
  if (e.key === "Escape") {
    if (!shortcutsOverlay.classList.contains("hidden")) {
      shortcutsOverlay.classList.add("hidden");
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
  if (e.key === "g") { categoryPanel.classList.toggle("hidden"); return; }

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
    if (item) entry = { kind: "intrinsic", key: item.name, title: item.name, subtitle: item.subtitle || "", item, fields: item.search_fields || [] };
  } else {
    const item = catalog.instrByKey[key];
    if (item) entry = { kind: "instruction", key: item.key, title: item.display_key || item.key, subtitle: item.summary || "", item, fields: item.search_fields || [] };
  }
  if (entry) renderDetail(entry);
});

/* ── ISA panel toggle ─────────────────────────────────────────────── */
$("isa-toggle").addEventListener("click", () => isaPanel.classList.toggle("hidden"));
$("isa-default").addEventListener("click", () => applyIsaPreset("default"));
if ($("isa-intel")) $("isa-intel").addEventListener("click", () => applyIsaPreset("intel"));
if ($("isa-arm32")) $("isa-arm32").addEventListener("click", () => applyIsaPreset("arm32"));
if ($("isa-arm64")) $("isa-arm64").addEventListener("click", () => applyIsaPreset("arm64"));
if ($("isa-riscv")) $("isa-riscv").addEventListener("click", () => applyIsaPreset("riscv"));
$("isa-none").addEventListener("click", () => applyIsaPreset("none"));
$("isa-all").addEventListener("click", () => applyIsaPreset("all"));

/* Kind filter — intrinsics vs instructions/asm */
for (const cb of document.querySelectorAll('#kind-bar input[data-kind]')) {
  cb.addEventListener("change", () => {
    const kind = cb.dataset.kind;
    if (cb.checked) enabledKinds.add(kind); else enabledKinds.delete(kind);
    cb.parentElement.classList.toggle("active", cb.checked);
    visibleSet = null;
    scheduleFilterRender();
  });
}

/* ── Category panel toggle + presets ──────────────────────────────── */
if ($("category-toggle")) {
  $("category-toggle").addEventListener("click", () => categoryPanel.classList.toggle("hidden"));
}
if ($("category-all")) {
  $("category-all").addEventListener("click", () => {
    enabledCategories = null;
    visibleSet = null;
    scheduleFilterRender();
  });
}
if ($("category-none")) {
  $("category-none").addEventListener("click", () => {
    enabledCategories = new Set();
    visibleSet = null;
    scheduleFilterRender();
  });
}
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
    if (focusedIndex >= 0) selectFocused();
    else if (resultPool.length) {
      focusResult(0);
      selectFocused();
    } else {
      renderResults();
    }
  }
});

/* ── Hash navigation ──────────────────────────────────────────────── */
window.addEventListener("hashchange", () => {
  const key = decodeURIComponent(location.hash.replace(/^#/, ""));
  if (!catalog || !key) return;
  const entry = resultPool.find(e => e.key === key)
    || (catalog.intrByName[key] ? {kind: "intrinsic", key, title: key, subtitle: catalog.intrByName[key].subtitle || "", item: catalog.intrByName[key], fields: catalog.intrByName[key].search_fields || []} : null)
    || (catalog.instrByKey[key] ? {kind: "instruction", key, title: catalog.instrByKey[key].display_key || key, subtitle: catalog.instrByKey[key].summary || "", item: catalog.instrByKey[key], fields: catalog.instrByKey[key].search_fields || []} : null);
  if (entry) renderDetail(entry);
});

/* ── Bootstrap ────────────────────────────────────────────────────── */
Promise.all([
  fetchJson("search-index.json"),
  fetchJson("filter_spec.json").catch(() => null),
  fetchJson("build_stamp.json").catch(() => null),
])
  .then(([data, spec, stamp]) => {
    catalog = data;
    // Prefer filter_spec.json (single source of truth) over embedded isa_config.
    const config = spec || data.isa_config || {};
    defaultEnabledIsas = new Set(config.default_enabled || [...defaultEnabledIsas]);
    FAMILY_SUB_ORDER = config.family_sub_order || {};
    DEFAULT_SUBS = Object.fromEntries(Object.entries(config.default_subs || {}).map(([family, values]) => [family, new Set(values)]));
    isaFamilyOrder = config.family_order || {};
    availableCategories = Array.isArray(config.categories) ? config.categories : [];
    ARCH_PRESETS = config.presets && typeof config.presets === "object" ? config.presets : {};
    if (stamp && metaNode) {
      const stale = stamp.catalog_generated_at && data.generated_at && stamp.catalog_generated_at !== data.generated_at;
      const label = stamp.git_sha ? `build ${stamp.git_sha}` : `v${stamp.version || ""}`;
      metaNode.textContent = label;
      metaNode.title = `Catalog ${stamp.catalog_generated_at || "?"}${stale ? " (stale)" : ""}`;
      metaNode.dataset.stamp = label;
      if (stale) metaNode.classList.add("stale");
    }
    catalog.intrByName = Object.fromEntries(data.intrinsics.map(i => [i.name, i]));
    catalog.instrByKey = Object.fromEntries(data.instructions.map(i => [i.key, i]));
    catalog.instrByDisplayKey = Object.fromEntries(data.instructions.map(i => [i.display_key || i.key, i]));
    catalog.instrByMnem = Object.fromEntries(data.instructions.map(i => [i.mnemonic, i]));

    searchEntries = [
      ...data.intrinsics.map(i => ({
        kind: "intrinsic", key: i.name, title: i.name, subtitle: i.subtitle || i.description || "", item: i,
        fields: i.search_fields || [i.name, i.description || "", i.display_isa || displayIsa(i.isa), (i.instructions || []).join(" ")],
      })),
      ...data.instructions.map(i => ({
        kind: "instruction", key: i.key, title: i.display_key || i.key, subtitle: i.summary || "", item: i,
        fields: i.search_fields || [i.display_mnemonic || i.mnemonic || "", i.display_form || i.form || "", i.summary || "", i.display_isa || displayIsa(i.isa)],
      })),
    ];

    buildSearchIndexes(searchEntries);

    availableIsas = [...new Set(searchEntries.flatMap(e => e.item.isa_families || []))]
      .sort((a, b) => (isaFamilyOrder[a] ?? 99) - (isaFamilyOrder[b] ?? 99) || a.localeCompare(b));
    enabledIsas = new Set([...defaultEnabledIsas].filter(v => availableIsas.includes(v)));
    initEnabledSubIsas();

    renderIsaFilters();
    renderCategoryFilters();
    updateCategorySummary();

    // Apply ?preset=NAME from URL, else default to "intel" for a
    // more responsive first load (smaller result set, fewer chips).
    try {
      const params = new URLSearchParams(location.search);
      const presetName = params.get("preset");
      if (presetName && ARCH_PRESETS[presetName]) {
        applyIsaPreset(presetName);
      } else if (ARCH_PRESETS["intel"]) {
        applyIsaPreset("intel");
      }
    } catch (_) { /* ignore malformed URL */ }

    rebuildVisibleSet();

    // Build-stamp badge text is set above if a stamp is present; only fall
    // back to the catalog-size summary when no stamp was emitted.
    if (!metaNode.dataset.stamp) {
      metaNode.textContent = `${data.intrinsics.length} intrinsics \u00b7 ${data.instructions.length} instructions`;
    }

    const fromHash = decodeURIComponent(location.hash.replace(/^#/, ""));
    if (fromHash) queryInput.value = fromHash;
    renderResults();
  })
  .catch((err) => {
    // Fetch failure (CORS, 404, gzip misconfig) should not leave "Loading..." up forever.
    console.error("simdref: failed to load catalog", err);
    if (metaNode) metaNode.textContent = "catalog load failed";
    if (resultsCount) resultsCount.textContent = "Failed to load search index";
    if (detailEmpty) {
      detailEmpty.textContent = "Could not load the search index. Open the browser console for details.";
      detailEmpty.style.display = "";
    }
  });

/* ── Annotate tab ────────────────────────────────────────────────────
 * Client-side port of src/simdref/annotate.py. Reuses the existing
 * fetchJson, chunkPrefix, loadChunk helpers. Zero backend.
 */
const ANN_INSTR_RE = /^(?<indent>[ \t]*)(?<mnemonic>[A-Za-z][A-Za-z0-9_.]*)(?:[ \t]+(?<operands>[^#\n]*?))?(?:[ \t]*(?<trailing>#.*))?$/;
const ANN_LABEL_RE = /^[ \t]*[A-Za-z_.$][\w.$]*:/;
const ANN_ATT_SUFFIXES = new Set(["b", "w", "l", "q", "s", "d", "t"]);
const ANN_STORAGE_KEY = "simdref:annotate:opts:v1";
const ANN_INPUT_KEY   = "simdref:annotate:input:v1";
const ANN_INPUT_MAX   = 256 * 1024;  // cap localStorage writes (256 KiB)

const instructionsByMnem = Object.create(null);
function buildInstructionsByMnem() {
  if (Object.keys(instructionsByMnem).length) return;
  for (const entry of (catalog.instructions || [])) {
    const key = String(entry.mnemonic || "").toUpperCase();
    if (!key) continue;
    (instructionsByMnem[key] ||= []).push(entry);
  }
}

function _archOf(entry) {
  return String(entry.architecture || "").toLowerCase();
}
function _archMatches(entry, isa) {
  if (!isa) return true;
  const a = _archOf(entry);
  if (isa === "x86")   return a === "x86";
  if (isa === "arm")   return a === "arm" || a === "aarch64" || a === "arm64" || a === "a64" || a === "a32";
  if (isa === "riscv") return a === "riscv" || a === "rvv";
  return true;
}

function parseAsmLine(line) {
  const raw = line.replace(/\n$/, "");
  if (!raw.trim()) return { kind: "blank", raw };
  const bare = raw.replace(/^[ \t]+/, "");
  if (bare.startsWith("#") || bare.startsWith("//")) return { kind: "comment", raw };
  if (bare.startsWith(".")) return { kind: "directive", raw };
  if (ANN_LABEL_RE.test(raw)) return { kind: "label", raw };
  const m = ANN_INSTR_RE.exec(raw);
  if (!m) return { kind: "comment", raw };
  return {
    kind: "instruction",
    raw,
    indent: m.groups.indent || "",
    mnemonic: m.groups.mnemonic || "",
    operands: (m.groups.operands || "").trim(),
    trailing: (m.groups.trailing || "").trim(),
  };
}

function lookupForms(mnemonic, isa) {
  const up = mnemonic.toUpperCase();
  const filter = (arr) => (arr || []).filter(e => _archMatches(e, isa));
  let forms = filter(instructionsByMnem[up]);
  if (forms.length) return forms;
  if (up.length > 2 && ANN_ATT_SUFFIXES.has(up.slice(-1).toLowerCase())) {
    forms = filter(instructionsByMnem[up.slice(0, -1)]);
    if (forms.length) return forms;
  }
  return [];
}

async function measurementsForEntry(entry) {
  const prefix = chunkPrefix(entry.mnemonic);
  const chunk = await loadChunk(prefix);
  const record = chunk && chunk[entry.key];
  return (record && Array.isArray(record.measurements)) ? record.measurements : [];
}

function _operandWidthTokens(operands) {
  if (!operands) return [];
  const s = operands.toLowerCase();
  const tokens = [];
  // Intel-syntax size specifiers: e.g. "QWORD PTR [rbp-8]".
  const sizeMap = {byte:"8", word:"16", dword:"32", qword:"64", xmmword:"128", ymmword:"256", zmmword:"512"};
  for (const [k, v] of Object.entries(sizeMap)) {
    if (new RegExp(`\\b${k}\\s+ptr\\b`).test(s)) tokens.push(`M${v}`);
  }
  // Registers (x86 only for now). Order matters: match longer/wider names first.
  const regPatterns = [
    [/\b(?:r[abcd]x|r[sd]i|rbp|rsp|r(?:8|9|1[0-5]))\b/, "R64"],
    [/\b(?:e[abcd]x|e[sd]i|ebp|esp|r(?:8|9|1[0-5])d)\b/, "R32"],
    [/\b(?:[abcd]x|[sd]i|bp|sp|r(?:8|9|1[0-5])w)\b/, "R16"],
    [/\b(?:[abcd][lh]|[sd]il|bpl|spl|r(?:8|9|1[0-5])b)\b/, "R8"],
    [/\bxmm\d+\b/, "XMM"],
    [/\bymm\d+\b/, "YMM"],
    [/\bzmm\d+\b/, "ZMM"],
    [/\bk[0-7]\b/, "K"],
  ];
  for (const [re, tok] of regPatterns) {
    if (re.test(s)) tokens.push(tok);
  }
  return tokens;
}

function _formOperandScore(form, opTokens) {
  if (!opTokens.length) return 0;
  const key = (form.display_key || form.key || "").toUpperCase();
  let score = 0;
  for (const tok of opTokens) {
    // Exact token hit = strong signal. R64 must match R64, not R8.
    if (new RegExp(`\\b${tok}\\b`).test(key)) score += 10;
    else if (tok.startsWith("R") && key.includes("M" + tok.slice(1))) score += 2; // reg→mem of same width
  }
  return score;
}

function pickForm(forms, arch, measurementsByKey, opTokens) {
  if (!forms.length) return null;
  const candidates = forms.slice();
  if (arch) {
    const archPinned = candidates.filter(f => (measurementsByKey.get(f.key) || []).some(r => r.uarch === arch));
    if (archPinned.length) candidates.length = 0, candidates.push(...archPinned);
  }
  let best = candidates[0];
  let bestScore = -Infinity;
  for (const f of candidates) {
    const m = measurementsByKey.get(f.key) || [];
    const measured = m.filter(r => (r.sourceKind || "measured") === "measured").length;
    const opScore = _formOperandScore(f, opTokens || []);
    // Operand match dominates; measurement coverage is the tiebreaker.
    const score = opScore * 10000 + measured * 100 + m.length;
    if (score > bestScore) { best = f; bestScore = score; }
  }
  return best;
}

function _cpiValue(row) {
  for (const k of ["tpUnrolled", "tpLoop", "tpPorts"]) {
    const v = row[k];
    if (v != null && v !== "" && v !== "-" && !isNaN(parseFloat(v))) return parseFloat(v);
  }
  return null;
}

function _latValue(row) {
  const v = row.latency;
  if (v == null || v === "" || v === "-") return null;
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

function aggregatePerf(measurements, { mode, includeModeled }) {
  function collect(kinds) {
    const lats = [], cpis = [], archs = [];
    for (const row of measurements) {
      const kind = row.sourceKind || "measured";
      if (!kinds.has(kind)) continue;
      const lat = _latValue(row);
      const cpi = _cpiValue(row);
      if (lat == null && cpi == null) continue;
      archs.push(row.uarch || "?");
      if (lat != null) lats.push(lat);
      if (cpi != null) cpis.push(cpi);
    }
    return { lats, cpis, archs };
  }
  let got = collect(new Set(["measured"]));
  let sourceKind = "measured";
  if (!got.archs.length && includeModeled) {
    got = collect(new Set(["modeled"]));
    sourceKind = "modeled";
  }
  if (!got.archs.length) {
    got = collect(new Set(["measured", "modeled"]));
    sourceKind = "mixed";
  }
  function reduce(vals) {
    if (!vals.length) return null;
    if (mode === "best")   return Math.min(...vals);
    if (mode === "worst")  return Math.max(...vals);
    if (mode === "median") {
      const s = [...vals].sort((a, b) => a - b);
      const m = Math.floor(s.length / 2);
      return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
    }
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  }
  return {
    latency: reduce(got.lats),
    cpi: reduce(got.cpis),
    nArchs: got.archs.length,
    sourceKind,
    archs: got.archs,
  };
}

function _fmtNum(x) {
  if (x == null) return "-";
  return Number.isInteger(x) ? x.toFixed(1) : (Math.abs(x) >= 100 ? x.toFixed(0) : x.toFixed(2));
}

/* The SDM ingest propagates a generic blurb (e.g. "Move 32-bit integer
 * operands.") to every MOV variant, including forms like MOV (M64, R64)
 * where it's plainly wrong. If the summary's stated N-bit width doesn't
 * match any width in the form's display_key, drop it — a wrong blurb is
 * worse than none. */
function _summaryMatchesForm(summary, form) {
  const m = /(\d+)\s*-?\s*bit/i.exec(summary || "");
  if (!m) return true;
  const declared = m[1];
  const key = String(form.display_key || form.key || "");
  const widths = [...key.matchAll(/[MRI](\d+)/g)].map(x => x[1]);
  if (!widths.length) return true;
  return widths.includes(declared);
}

function formatAnnotation(entry, measurements, opts) {
  const parts = [];
  if (opts.docs && entry.summary && _summaryMatchesForm(entry.summary, entry)) {
    parts.push(entry.summary.trim());
  }
  if (opts.perf) {
    let lat, cpi, tag;
    if (opts.arch) {
      const row = measurements.find(r => r.uarch === opts.arch);
      lat = row ? _latValue(row) : null;
      cpi = row ? _cpiValue(row) : null;
      const kind = row ? (row.sourceKind || "measured") : "no data";
      tag = `[${opts.arch}, ${kind}]`;
    } else {
      const summary = aggregatePerf(measurements, { mode: opts.agg, includeModeled: opts.includeModeled });
      lat = summary.latency; cpi = summary.cpi;
      tag = summary.nArchs === 0
        ? "[no data]"
        : `[${opts.agg} of ${summary.nArchs} archs, ${summary.sourceKind}]`;
    }
    parts.push(`lat=${_fmtNum(lat)}c cpi=${_fmtNum(cpi)} ${tag}`);
  }
  return parts.join(" | ");
}

function readAnnotateOpts() {
  return {
    perf:           $("ann-perf").checked,
    docs:           $("ann-docs").checked,
    isa:            $("ann-isa").value || "",
    arch:           $("ann-arch").value || "",
    agg:            $("ann-agg").value || "avg",
    includeModeled: $("ann-modeled").checked,
  };
}

function persistAnnotateOpts() {
  try { localStorage.setItem(ANN_STORAGE_KEY, JSON.stringify(readAnnotateOpts())); }
  catch (_) { /* quota / disabled */ }
}

function persistAnnotateInput() {
  try {
    const v = $("ann-input").value || "";
    if (!v) { localStorage.removeItem(ANN_INPUT_KEY); return; }
    localStorage.setItem(ANN_INPUT_KEY, v.slice(0, ANN_INPUT_MAX));
  } catch (_) { /* quota / disabled */ }
}

function restoreAnnotateInput() {
  try {
    const raw = localStorage.getItem(ANN_INPUT_KEY);
    if (raw == null) return false;
    $("ann-input").value = raw;
    return true;
  } catch (_) { return false; }
}

function restoreAnnotateOpts() {
  try {
    const raw = localStorage.getItem(ANN_STORAGE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw);
    if (typeof saved.perf === "boolean")           $("ann-perf").checked = saved.perf;
    if (typeof saved.docs === "boolean")           $("ann-docs").checked = saved.docs;
    if (typeof saved.includeModeled === "boolean") $("ann-modeled").checked = saved.includeModeled;
    if (typeof saved.agg === "string")             $("ann-agg").value = saved.agg;
    if (typeof saved.isa === "string")             $("ann-isa").value = saved.isa;
    if (typeof saved.arch === "string")            $("ann-arch").dataset.pendingArch = saved.arch;
  } catch (_) { /* ignore */ }
}

let archSelectPopulated = false;
async function populateArchSelect() {
  if (archSelectPopulated || !catalog || !catalog.instructions) return;
  archSelectPopulated = true;
  // Build arch set from the search-index's `cores` / measurement uarch
  // hints when available; otherwise peek at one chunk per isa-family.
  // Fastest reliable source: iterate measurements we've already fetched
  // in this session. If none yet, warm a couple of popular prefixes.
  const uarchSet = new Set();
  const seed = ["ADD", "VAD", "MUL", "MOV"];
  await Promise.all(seed.map(async (p) => {
    const chunk = await loadChunk(p).catch(() => ({}));
    for (const rec of Object.values(chunk || {})) {
      for (const row of (rec.measurements || [])) {
        if (row && row.uarch) uarchSet.add(row.uarch);
      }
    }
  }));
  const sel = $("ann-arch");
  const prior = sel.dataset.pendingArch || sel.value || "";
  const archs = [...uarchSet].sort();
  for (const a of archs) {
    const opt = document.createElement("option");
    opt.value = a; opt.textContent = a;
    sel.appendChild(opt);
  }
  if (prior && archs.includes(prior)) sel.value = prior;
  delete sel.dataset.pendingArch;
}

function escAnn(s) {
  return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
}

/* Lazy-load highlight.js (core + x86asm + armasm) from jsDelivr on the
 * first Annotate-tab activation. Falls back to escaped plain text if
 * the CDN is unreachable — highlighting is a nicety, not essential. */
const HLJS_BASE = "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.10.0";
let _hljsPromise = null;
function ensureHljs() {
  if (_hljsPromise) return _hljsPromise;
  _hljsPromise = new Promise((resolve) => {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    const light = document.createElement("link");
    light.id = "hljs-theme-light"; light.rel = "stylesheet";
    light.href = `${HLJS_BASE}/styles/github.min.css`;
    light.disabled = isDark;
    document.head.appendChild(light);
    const dark = document.createElement("link");
    dark.id = "hljs-theme-dark"; dark.rel = "stylesheet";
    dark.href = `${HLJS_BASE}/styles/github-dark.min.css`;
    dark.disabled = !isDark;
    document.head.appendChild(dark);

    const loadScript = (src) => new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.async = true; s.onload = res; s.onerror = rej;
      document.head.appendChild(s);
    });

    loadScript(`${HLJS_BASE}/highlight.min.js`)
      .then(() => Promise.all([
        loadScript(`${HLJS_BASE}/languages/x86asm.min.js`),
        loadScript(`${HLJS_BASE}/languages/armasm.min.js`),
      ]))
      .then(() => resolve(!!window.hljs))
      .catch(() => resolve(false));
  });
  return _hljsPromise;
}

function highlightAsm(text, isa) {
  if (window.hljs) {
    const lang = isa === "arm" ? "armasm" : "x86asm";
    try {
      return window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    } catch (_) { /* fall through */ }
  }
  return escAnn(text);
}

function syncInputHighlight() {
  const ta   = $("ann-input");
  const code = $("ann-input-hl-code");
  if (!ta || !code) return;
  // Append a trailing space if the textarea ends on a newline, otherwise
  // the mirror <pre> collapses the last empty line and scroll-syncs drift.
  const text = ta.value.endsWith("\n") ? ta.value + " " : ta.value;
  const isa = $("ann-isa").value || "";
  code.innerHTML = highlightAsm(text, isa);
  syncInputScroll();
}

function syncInputScroll() {
  const ta = $("ann-input");
  if (!ta) return;
  const mirror = ta.parentElement && ta.parentElement.querySelector(".ann-input-hl");
  if (mirror) { mirror.scrollTop = ta.scrollTop; mirror.scrollLeft = ta.scrollLeft; }
}

async function runAnnotate() {
  const input = $("ann-input").value;
  const out   = $("ann-output");
  const status = $("ann-status");
  status.textContent = "parsing…";
  buildInstructionsByMnem();

  const lines = input.split("\n");
  const parsed = lines.map(parseAsmLine);
  const mnems = new Set();
  for (const p of parsed) if (p.kind === "instruction") mnems.add(p.mnemonic.toUpperCase());

  const isa = $("ann-isa").value || "";

  // Dedup prefix fetches up-front.
  const prefixes = new Set();
  for (const m of mnems) {
    const forms = lookupForms(m, isa);
    for (const f of forms) prefixes.add(chunkPrefix(f.mnemonic));
  }
  status.textContent = `fetching ${prefixes.size} chunk${prefixes.size === 1 ? "" : "s"}…`;
  await Promise.all([...prefixes].map(p => loadChunk(p)));

  // Build measurement cache keyed by instruction-record key.
  const measurementsByKey = new Map();
  for (const p of prefixes) {
    const chunk = await loadChunk(p);
    for (const [k, rec] of Object.entries(chunk || {})) {
      if (Array.isArray(rec.measurements)) measurementsByKey.set(k, rec.measurements);
    }
  }

  const opts = readAnnotateOpts();
  let known = 0, unknown = 0;
  const outLines = [];
  for (const p of parsed) {
    if (p.kind !== "instruction") {
      outLines.push(highlightAsm(p.raw, isa));
      continue;
    }
    const forms = lookupForms(p.mnemonic, isa);
    const opTokens = isa === "x86" ? _operandWidthTokens(p.operands) : [];
    const form = forms.length ? pickForm(forms, opts.arch, measurementsByKey, opTokens) : null;
    if (!form) {
      unknown++;
      outLines.push(`${highlightAsm(p.raw, isa)}   <span class="hl-unknown">${p.trailing ? "" : "# ??"}</span>`);
      continue;
    }
    if (!opts.perf && !opts.docs) {
      outLines.push(highlightAsm(p.raw, isa));
      continue;
    }
    const measurements = measurementsByKey.get(form.key) || [];
    const annotation = formatAnnotation(form, measurements, opts);
    if (!annotation || p.trailing) {
      outLines.push(highlightAsm(p.raw, isa));
      continue;
    }
    known++;
    outLines.push(`${highlightAsm(p.raw, isa)}   <span class="hl-comment"># ${escAnn(annotation)}</span>`);
  }
  out.innerHTML = outLines.join("\n");
  status.textContent = `annotated ${known} / ${known + unknown} (${unknown} unknown)`;
}

function outputAsText() {
  // Strip highlighting spans from <pre> to get the plain .sa payload.
  const clone = $("ann-output").cloneNode(true);
  return clone.textContent;
}

let _annDebounce = null;
function scheduleAutoAnnotate(delay = 350) {
  if (_annDebounce) clearTimeout(_annDebounce);
  _annDebounce = setTimeout(() => { _annDebounce = null; runAnnotate(); }, delay);
}

const ANN_SPLIT_KEY = "simdref:annotate:split:v1";
function applySplit(ratio) {
  const r = Math.min(0.85, Math.max(0.15, ratio));
  const panes = $("ann-panes");
  if (!panes) return;
  panes.style.setProperty("--ann-split",   `${r}fr`);
  panes.style.setProperty("--ann-split-r", `${1 - r}fr`);
}
function restoreSplit() {
  let r = 1 / 3;  // default: left 1/3, right 2/3
  try {
    const raw = localStorage.getItem(ANN_SPLIT_KEY);
    if (raw) {
      const parsed = parseFloat(raw);
      if (isFinite(parsed) && parsed > 0 && parsed < 1) r = parsed;
    }
  } catch (_) { /* ignore */ }
  applySplit(r);
}
function wireSplitter() {
  const splitter = $("ann-splitter");
  const panes = $("ann-panes");
  if (!splitter || !panes) return;
  let dragging = false;
  splitter.addEventListener("mousedown", (e) => {
    dragging = true;
    splitter.classList.add("dragging");
    document.body.classList.add("ann-resizing");
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const rect = panes.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = (e.clientX - rect.left) / rect.width;
    applySplit(ratio);
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("dragging");
    document.body.classList.remove("ann-resizing");
    // Persist current ratio derived from the CSS var.
    const cur = panes.style.getPropertyValue("--ann-split").trim();
    const r = parseFloat(cur);
    if (isFinite(r)) {
      try { localStorage.setItem(ANN_SPLIT_KEY, String(r)); } catch (_) { /* ignore */ }
    }
  });
  splitter.addEventListener("dblclick", () => {
    applySplit(1 / 3);
    try { localStorage.setItem(ANN_SPLIT_KEY, String(1 / 3)); } catch (_) { /* ignore */ }
  });
}

function wireAnnotateTab() {
  const body = document.body;
  const tabSearch = $("tab-search");
  const tabAnnotate = $("tab-annotate");

  function setTab(name, { push = true } = {}) {
    const active = name === "annotate" ? "annotate" : "search";
    body.dataset.tab = active;
    tabSearch.setAttribute("aria-selected", active === "search" ? "true" : "false");
    tabAnnotate.setAttribute("aria-selected", active === "annotate" ? "true" : "false");
    if (push && location.hash.replace(/^#/, "") !== (active === "annotate" ? "annotate" : "")) {
      // Preserve hash-as-search-query for the search tab; only rewrite
      // when leaving/entering annotate explicitly.
      if (active === "annotate") history.replaceState(null, "", "#annotate");
      else if (location.hash === "#annotate") history.replaceState(null, "", location.pathname);
    }
    if (active === "annotate") {
      populateArchSelect();
      ensureHljs().then((ok) => {
        syncInputHighlight();
        if (ok && $("ann-input").value.trim()) runAnnotate();
      });
      setTimeout(() => $("ann-input").focus(), 0);
    }
  }

  tabSearch.addEventListener("click",   () => setTab("search"));
  tabAnnotate.addEventListener("click", () => setTab("annotate"));

  $("ann-run").addEventListener("click", runAnnotate);
  $("ann-clear").addEventListener("click", () => {
    $("ann-input").value = "";
    $("ann-output").textContent = "";
    $("ann-status").textContent = "";
    syncInputHighlight();
  });
  $("ann-copy").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(outputAsText());
      $("ann-status").textContent = "copied.";
    } catch (_) { $("ann-status").textContent = "copy failed"; }
  });
  $("ann-download").addEventListener("click", () => {
    const blob = new Blob([outputAsText()], { type: "text/plain;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = "annotated.sa";
    document.body.appendChild(a); a.click();
    setTimeout(() => { a.remove(); URL.revokeObjectURL(url); }, 0);
  });

  // Persist toolbar state + auto-annotate on toolbar change.
  for (const id of ["ann-perf", "ann-docs", "ann-modeled", "ann-isa", "ann-arch", "ann-agg"]) {
    $(id).addEventListener("change", () => {
      persistAnnotateOpts();
      if (id === "ann-isa") syncInputHighlight();
      scheduleAutoAnnotate(150);
    });
  }

  // Auto-annotate with debounce as the user types / pastes, and keep the
  // input's syntax-highlighted mirror in sync immediately.
  $("ann-input").addEventListener("input", () => {
    syncInputHighlight();
    persistAnnotateInput();
    scheduleAutoAnnotate();
  });
  $("ann-input").addEventListener("scroll", syncInputScroll);
  // Ctrl+Enter still forces an immediate run.
  $("ann-input").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); runAnnotate(); }
  });

  /* Ctrl+A inside the annotate panes: scope the select-all to the
   * current pane rather than the whole document. The input textarea's
   * native select-all already does the right thing, but the output
   * <pre> is a focusable static element where Ctrl+A would otherwise
   * fall through to document.execCommand('selectAll'). */
  function selectAllIn(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
  $("ann-output").addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "a" || e.key === "A")) {
      e.preventDefault();
      selectAllIn(e.currentTarget);
    }
  });

  wireSplitter();
  restoreAnnotateOpts();
  restoreSplit();
  const hadSavedInput = restoreAnnotateInput();
  if (hadSavedInput) syncInputHighlight();

  // Initial tab from URL hash: only #annotate activates the annotate
  // tab; other hashes are search queries (existing behavior).
  if (location.hash === "#annotate") setTab("annotate", { push: false });
  else setTab("search", { push: false });
}

// Wire once the DOM is ready; the catalog may still be loading.
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", wireAnnotateTab);
} else {
  wireAnnotateTab();
}
