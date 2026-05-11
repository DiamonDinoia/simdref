/* Node-driven regression test for the web app's search-index / bucket
 * code. Loads the real templates/app.js inside a vm sandbox with stub
 * browser globals, then exercises:
 *
 *   - Phase-1 buildSearchIndexes/buildBuckets with instructions only
 *   - Phase-2 extendSearchIndexes/extendBuckets with appended intrinsics
 *
 * and asserts:
 *
 *   (a) searching "add" returns hits that include the appended intrinsics,
 *   (b) byKind["intrinsic"] contains the new entries (the bug Marco hit
 *       when disabling the instruction kind made search empty).
 *
 * Run via tests/test_search_index_js.py.
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import {fileURLToPath} from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const APP_JS = fs.readFileSync(
  path.resolve(here, "..", "..", "src/simdref/templates/app.js"),
  "utf8",
);

// Stub every DOM/browser API app.js touches at module load. Proxy lets
// us return chainable, no-op stubs for anything we forgot — the goal is
// to make module evaluation succeed, not to faithfully emulate a browser.
const noop = () => {};
function makeElement() {
  const el = {
    addEventListener: noop,
    removeEventListener: noop,
    appendChild: noop,
    removeChild: noop,
    insertBefore: noop,
    setAttribute: noop,
    removeAttribute: noop,
    getAttribute: () => null,
    contains: () => false,
    focus: noop,
    blur: noop,
    click: noop,
    scrollIntoView: noop,
    classList: {add: noop, remove: noop, toggle: noop, contains: () => false},
    dataset: {},
    style: new Proxy({}, {
      get(t, p) { return typeof p === "string" && /^[a-z]/.test(p) ? noop : t[p]; },
      set(t, p, v) { t[p] = v; return true; },
    }),
    innerHTML: "",
    textContent: "",
    value: "",
    checked: false,
    children: [],
    childNodes: [],
    firstChild: null,
    parentNode: null,
    isConnected: false,
    getBoundingClientRect: () => ({top: 0, left: 0, width: 0, height: 0}),
  };
  return new Proxy(el, {
    get(t, p) {
      if (p in t) return t[p];
      // Methods we forgot become no-ops; properties become further proxies.
      return typeof p === "string" && /^[a-z]/.test(p) ? noop : undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
}

const windowStub = {
  addEventListener: noop,
  removeEventListener: noop,
  requestIdleCallback: undefined,
  requestAnimationFrame: (cb) => setTimeout(cb, 0),
  cancelAnimationFrame: clearTimeout,
  matchMedia: () => ({matches: false, addEventListener: noop, removeEventListener: noop}),
  scrollTo: noop,
  innerWidth: 1280,
  innerHeight: 800,
  location: {hash: "", search: "", href: "http://localhost/"},
  history: {replaceState: noop, pushState: noop},
  SIMDREF_UI: {},
  navigator: {clipboard: {writeText: () => Promise.resolve()}, userAgent: "node"},
};

const documentStub = new Proxy(
  {
    getElementById: () => makeElement(),
    querySelector: () => makeElement(),
    querySelectorAll: () => [],
    createElement: () => makeElement(),
    createTextNode: () => ({}),
    createDocumentFragment: () => makeElement(),
    addEventListener: noop,
    removeEventListener: noop,
    body: makeElement(),
    documentElement: makeElement(),
    head: makeElement(),
    activeElement: makeElement(),
  },
  {
    get(t, p) { return p in t ? t[p] : noop; },
    set(t, p, v) { t[p] = v; return true; },
  },
);

const sandbox = {
  console,
  window: windowStub,
  document: documentStub,
  navigator: windowStub.navigator,
  location: windowStub.location,
  localStorage: {getItem: () => null, setItem: noop, removeItem: noop},
  // fetch hangs — bootstrap will register but never complete.
  fetch: () => new Promise(() => {}),
  DecompressionStream: undefined,
  Response: undefined,
  URLSearchParams,
  Map, Set, Promise, JSON, Math, Date, RegExp,
  Array, Object, Number, String, Boolean, Symbol,
  Error, TypeError, RangeError,
  setTimeout, clearTimeout, setInterval, clearInterval,
  Intl,
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.global = sandbox;
// Don't forward `window`-mirrored globals: tests don't need them and adding
// them risks shadowing.

const probe = `
;globalThis.__TEST_API__ = {
  get searchEntries() { return searchEntries; },
  set searchEntries(v) { searchEntries = v; },
  get searchTokenIndex() { return searchTokenIndex; },
  get searchPrefixIndex() { return searchPrefixIndex; },
  get bucketsBuilt() { return bucketsBuilt; },
  set bucketsBuilt(v) { bucketsBuilt = v; },
  get byKind() { return byKind; },
  get byFamily() { return byFamily; },
  buildSearchIndexes: (e) => buildSearchIndexes(e),
  extendSearchIndexes: (e, b) => extendSearchIndexes(e, b),
  buildBuckets: () => buildBuckets(),
  extendBuckets: (e, b) => extendBuckets(e, b),
  candidateIndexes: (q) => candidateIndexes(q),
  tokens: (s) => tokens(s),
};
`;

vm.createContext(sandbox);
try {
  vm.runInContext(APP_JS + probe, sandbox, {filename: "app.js"});
} catch (err) {
  console.error("[fatal] app.js failed to evaluate:", err);
  process.exit(2);
}

const T = sandbox.__TEST_API__;
if (!T) {
  console.error("[fatal] __TEST_API__ probe was not installed");
  process.exit(2);
}

// ── Fixture: 3 instructions + 3 intrinsics, all touching "add" ───────
const instructions = [
  {key: "x86:ADD", mnemonic: "ADD", form: "ADD r/m, r", summary: "Integer add",
   architecture: "x86", isa: ["x86"], display_isa: "x86", isa_families: ["x86"],
   display_key: "ADD r/m, r", display_form: "ADD r/m, r", display_mnemonic: "ADD",
   search_fields: ["ADD", "ADD r/m, r", "Integer add", "x86"]},
  {key: "x86:VADDPS", mnemonic: "VADDPS", form: "VADDPS xmm, xmm, xmm",
   summary: "Packed fp add", architecture: "x86", isa: ["AVX"],
   display_isa: "AVX", isa_families: ["AVX"],
   display_key: "VADDPS xmm, xmm, xmm", display_form: "VADDPS xmm, xmm, xmm",
   display_mnemonic: "VADDPS",
   search_fields: ["VADDPS", "VADDPS xmm, xmm, xmm", "Packed fp add", "AVX"]},
  {key: "x86:SUB", mnemonic: "SUB", form: "SUB r/m, r", summary: "Integer subtract",
   architecture: "x86", isa: ["x86"], display_isa: "x86", isa_families: ["x86"],
   display_key: "SUB r/m, r", display_form: "SUB r/m, r", display_mnemonic: "SUB",
   search_fields: ["SUB", "SUB r/m, r", "Integer subtract", "x86"]},
];

const intrinsics = [
  {name: "_mm_add_ps", subtitle: "Add packed single-precision floats.",
   architecture: "x86", isa: ["SSE"], display_isa: "SSE", isa_families: ["SSE"],
   search_fields: ["_mm_add_ps", "Add packed single-precision floats", "SSE", "ADDPS"]},
  {name: "_mm256_add_ps", subtitle: "Add 8 packed singles.",
   architecture: "x86", isa: ["AVX"], display_isa: "AVX", isa_families: ["AVX"],
   search_fields: ["_mm256_add_ps", "Add 8 packed singles", "AVX", "VADDPS"]},
  {name: "vaddq_u8", subtitle: "Arm NEON add packed u8.",
   architecture: "arm", isa: ["NEON"], display_isa: "NEON", isa_families: ["Arm"],
   search_fields: ["vaddq_u8", "Arm NEON add packed u8", "NEON"]},
];

function instrEntry(i) {
  return {kind: "instruction", key: i.key, title: i.display_key, subtitle: i.summary,
          item: i, fields: i.search_fields};
}
function intrEntry(i) {
  return {kind: "intrinsic", key: i.name, title: i.name, subtitle: i.subtitle,
          item: i, fields: i.search_fields};
}

// ── Phase 1: instructions only ───────────────────────────────────────
T.searchEntries = instructions.map(instrEntry);
T.bucketsBuilt = false;
T.buildSearchIndexes(T.searchEntries);
T.buildBuckets();

const failures = [];
function assert(cond, msg) { if (!cond) failures.push(msg); }

const phase1Add = T.candidateIndexes("add") || [];
assert(phase1Add.length > 0, "Phase 1: candidateIndexes('add') returned empty");
assert((T.byKind.get("instruction") || []).length === 3,
       `Phase 1: byKind['instruction'] should have 3 entries, got ${(T.byKind.get("instruction") || []).length}`);
assert(!(T.byKind.get("intrinsic") && T.byKind.get("intrinsic").length),
       "Phase 1: byKind['intrinsic'] should be empty before intrinsics arrive");

// ── Phase 2: append intrinsics ──────────────────────────────────────
const intrinsicEntries = intrinsics.map(intrEntry);
const base = T.searchEntries.length;
for (const e of intrinsicEntries) T.searchEntries.push(e);
T.extendSearchIndexes(intrinsicEntries, base);
T.extendBuckets(intrinsicEntries, base);

const phase2Add = T.candidateIndexes("add") || [];
assert(phase2Add.length >= phase1Add.length + 3,
       `Phase 2: 'add' candidates grew by less than 3 (was ${phase1Add.length}, now ${phase2Add.length})`);

const intrinsicBucket = T.byKind.get("intrinsic") || [];
assert(intrinsicBucket.length === 3,
       `Phase 2: byKind['intrinsic'] should have 3 entries, got ${intrinsicBucket.length}`);

const armBucket = T.byFamily.get("Arm") || [];
assert(armBucket.length === 1, `Phase 2: byFamily['Arm'] should have 1 entry, got ${armBucket.length}`);

// Regression for the kind-filter bug: candidates ∩ byKind['intrinsic']
// must be non-empty when searching "add".
const intrSet = new Set(intrinsicBucket);
const intrHits = phase2Add.filter(i => intrSet.has(i));
assert(intrHits.length >= 3,
       `Phase 2: searching 'add' with intrinsic-only kind filter found ${intrHits.length} hits (need >=3)`);
const intrNames = new Set(intrHits.map(i => T.searchEntries[i].item.name));
for (const want of ["_mm_add_ps", "_mm256_add_ps", "vaddq_u8"]) {
  assert(intrNames.has(want),
         `Phase 2: 'add' intrinsic results missing ${want}; got ${[...intrNames].join(",")}`);
}

// ── Source check: the Phase-2 ingest pump must call extendBuckets,
// otherwise intrinsics would never reach byKind / byFamily and the kind
// filter would silently drop them all (Marco's bug). ──────────────────
const ingestSlice = APP_JS.slice(APP_JS.indexOf("function _ingestIntrinsics"));
const pumpEnd = ingestSlice.indexOf("\n}");
const pumpBody = pumpEnd > 0 ? ingestSlice.slice(0, pumpEnd) : ingestSlice;
assert(
  /extendBuckets\s*\(/.test(pumpBody),
  "_ingestIntrinsics() does not call extendBuckets — kind filter will drop intrinsics",
);
assert(
  /extendSearchIndexes\s*\(/.test(pumpBody),
  "_ingestIntrinsics() does not call extendSearchIndexes — search will miss intrinsics",
);

if (failures.length) {
  for (const f of failures) console.error("FAIL:", f);
  process.exit(1);
}
console.log("OK: phase-1 and phase-2 search/bucket invariants hold");
