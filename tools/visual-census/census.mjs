// Visual census of the Software Library page - a manual pre-release check.
//
//   node tools/visual-census/census.mjs [--theme dark|light]
//        [--root <project>] [--out <dir>]
//
// NOT A GATE, and deliberately outside t/: the test suites are Python-only by
// standing decision, and run_all.py only collects t/test_*.py. This exists
// because a Python harness can check class names but not what the browser
// actually paints, and several of these states only exist mid-task.
//
// What it does: stages server/ with a fresh database (stage.py - the real
// appease.db and .env are never copied), drives a throwaway headless Chrome
// profile through every Software Library state, and for each one reports:
//   - light FILLS on a dark page     (every colour round-tripped through a
//                                     1x1 canvas, so oklch() is not skipped)
//   - light BORDERS on a dark ground
//   - TEXT below 4.5:1 (3:1 large) against the first opaque background behind
//     it, translucent layers composited
//   - any JavaScript exception
// plus two explicit checks for traps this page has already sprung: the
// duplicate dialog's tier swap, and the detection dialog's rebuilt class list.
//
// States the page only reaches through the network (reading an installer, a
// detected result) are set with the page's OWN functions. That proves how the
// state renders, not that the real upload reaches it.
//
// Exit: 0 clean, 1 unintended findings (dark only - light is informational),
// 2 the census itself could not run. Needs Chrome and Node 24 (global
// WebSocket and fetch).

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i === -1 ? fallback : args[i + 1];
};
const THEME = opt("--theme", "dark");
const ROOT = resolve(opt("--root", join(HERE, "..", "..")));
const WORK = mkdtempSync(join(tmpdir(), "appease-census-"));
const OUT = resolve(opt("--out", join(WORK, "report")));
const PORT = 5078;
const DEVTOOLS_PORT = 9340;
const BASE = `http://127.0.0.1:${PORT}`;
const CHROME = process.env.CHROME || [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
].find((p) => existsSync(p));

if (!["dark", "light"].includes(THEME)) { console.error("--theme must be dark or light"); process.exit(2); }
if (!CHROME) { console.error("Chrome not found - set CHROME to its path"); process.exit(2); }
if (typeof WebSocket === "undefined") { console.error("Node 24 or newer is required (global WebSocket)"); process.exit(2); }
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const children = [];
function cleanup() {
  for (const c of children) { try { c.kill(); } catch { /* already gone */ } }
}
process.on("exit", cleanup);

async function waitFor(url, seconds) {
  for (let i = 0; i < seconds * 4; i++) {
    try { const r = await fetch(url); if (r.ok) return r; } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error(`nothing answered at ${url} after ${seconds}s`);
}

// ---- the census function, run inside the page ----------------------------
const AUDIT = `(() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  const rgba = (v) => {
    if (!v || v === 'transparent') return [0, 0, 0, 0];
    ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = '#000'; ctx.fillStyle = v; ctx.fillRect(0, 0, 1, 1);
    const d = ctx.getImageData(0, 0, 1, 1).data;
    return [d[0], d[1], d[2], d[3] / 255];
  };
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (top, under) => [0, 1, 2].map((i) => Math.round(top[i] * top[3] + under[i] * (1 - top[3]))).concat(1);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    }
    return true;
  };
  // What a person actually sees at an element: its paint composited up the
  // WHOLE ancestor chain, opacity included. Opacity fades an element's
  // entire subtree - text, fills, borders - toward whatever is behind it, so
  // an ancestor at 0.45 matters as much as the element's own 0.4. Measuring
  // only background layers, as this once did, scored a faded control at its
  // full-strength contrast and reported it clean.
  //
  // Per node, from the root down: paint its background over what is below,
  // let its descendants paint over that, then mix the result back toward
  // what was below by the node's opacity. \`paint\` is laid over the target
  // element's own background: its text colour or a border colour, or null
  // for the ground itself.
  const mix = (top, under, a) => [0, 1, 2].map((i) => Math.round(top[i] * a + under[i] * (1 - a))).concat(1);
  const composite = (el, paint) => {
    const nodes = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) nodes.unshift(n);
    const render = (i, below) => {
      const cs = getComputedStyle(nodes[i]);
      let c = over(rgba(cs.backgroundColor), below);
      c = i < nodes.length - 1 ? render(i + 1, c) : (paint ? over(paint, c) : c);
      return mix(c, below, parseFloat(cs.opacity));
    };
    return render(0, [255, 255, 255, 1]);
  };
  const hex = (c) => '#' + c.slice(0, 3).map((x) => x.toString(16).padStart(2, '0')).join('');
  const name = (el) => {
    let s = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '');
    const cls = (el.getAttribute('class') || '').trim().split(/\\s+/).filter(Boolean);
    return s + (cls.length ? '.' + cls.slice(0, 6).join('.') : '');
  };
  const out = { fills: [], borders: [], texts: [], skippedDisabled: [] };
  const seen = new Set();
  const push = (kind, item) => { const k = kind + JSON.stringify(item); if (!seen.has(k)) { seen.add(k); out[kind].push(item); } };
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg') || ['SCRIPT', 'STYLE', 'LINK', 'META'].includes(el.tagName) || !visible(el)) continue;
    const cs = getComputedStyle(el);
    if (rgba(cs.backgroundColor)[3] > 0.2) {
      const seen = composite(el, null);
      if (lum(seen) > 0.45) push('fills', { el: name(el), bg: hex(seen) });
    }
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      const bc = rgba(cs['border' + side + 'Color']);
      if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none' && bc[3] > 0.2) {
        const seen = composite(el, bc);
        if (lum(seen) > 0.45 && lum(composite(el.parentElement || el, null)) < 0.2) {
          push('borders', { el: name(el), border: hex(seen) }); break;
        }
      }
    }
    const ownText = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim());
    if (ownText || ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && (el.value || el.placeholder))) {
      // Disabled controls are exempt from text contrast, and they are exactly
      // what fades on purpose (disabled:opacity-40). Skipped, but counted, so
      // an exemption is never silent.
      if (el.closest(':disabled, [aria-disabled="true"]')) {
        push('skippedDisabled', name(el));
        continue;
      }
      const g = composite(el, null);
      const fg = composite(el, rgba(cs.color));
      const r = ratio(fg, g);
      const size = parseFloat(cs.fontSize), bold = parseInt(cs.fontWeight, 10) >= 700;
      const need = (size >= 24 || (size >= 18.66 && bold)) ? 3 : 4.5;
      if (r < need) push('texts', { el: name(el), fg: hex(fg), on: hex(g), ratio: Math.round(r * 100) / 100, need,
        text: (el.value || el.placeholder || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 48) });
    }
  }
  return out;
})()`;

// Findings that are known and recorded elsewhere: reported, never counted.
//
// This is the one place a real finding can hide, so it is an EXPLICIT
// ENUMERATION, not a pattern. An entry absorbs a finding only when ALL of
// these hold: the element is one it names, the text is exactly its fg, the
// ground is exactly its bg, and the ratio sits inside its band. A pattern
// would absorb new failures too: any element with white-on-primary text, or
// ANY contrast failure on the "Add anyway" button, would pass as "known".
//
// A new element with an accepted colour pair is therefore a finding until
// someone adds it here on purpose. An entry that matches nothing on a run is
// printed as unused, so the list cannot rot silently.
const KNOWN = [
  { why: "white on the primary blue is 4.4981:1 - a known, accepted gap (--color-primary-foreground)",
    fg: "#ffffff", on: "#4173d8", min: 4.49, max: 4.5,
    elements: ["span.sb-label", "button#add-software-btn", "button#type-pill-offline",
      "button#type-pill-online", "button#duplicate-dialog-confirm", "button#detect-dialog-retry",
      "button#sw-edit-save"] },
  { why: '"Add anyway" is white on bg-amber-600 at 3.2:1 in both themes - a known gap, not yet fixed',
    fg: "#ffffff", on: "#e17100", min: 3.19, max: 3.21,
    elements: ["button#duplicate-dialog-confirm"] },
];
// `name()` is tag#id.class.class - an element matches on that exact prefix,
// never on a class appearing somewhere later in the list.
const isElement = (el, prefix) => el === prefix || el.startsWith(prefix + ".");
const allowanceFor = (f) => KNOWN.find((k) => f.fg === k.fg && f.on === k.on
  && f.ratio >= k.min && f.ratio <= k.max && k.elements.some((p) => isElement(f.el, p)));
const allowanceUse = new Map();

let chrome;
let exitCode = 0;
try {
  const server = spawn(process.env.PYTHON || "python", [join(HERE, "stage.py"), WORK, String(PORT), ROOT],
    { stdio: ["ignore", "pipe", "pipe"] });
  children.push(server);
  let serverLog = "";
  server.stdout.on("data", (d) => { serverLog += d; });
  server.stderr.on("data", (d) => { serverLog += d; });
  try { await waitFor(BASE + "/login", 60); } catch (e) { console.error(serverLog); throw e; }

  chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${DEVTOOLS_PORT}`,
    `--user-data-dir=${join(WORK, "chrome-profile")}`, "--no-first-run", "--no-default-browser-check",
    "--disable-extensions", "about:blank"], { stdio: "ignore" });
  children.push(chrome);
  const targets = await (await waitFor(`http://127.0.0.1:${DEVTOOLS_PORT}/json/list`, 30)).json();
  const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener("open", r, { once: true }); ws.addEventListener("error", j, { once: true }); });

  let nextId = 1;
  const pending = new Map();
  const loadWaiters = [];
  const exceptions = [];
  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve: ok, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(msg.error.message)) : ok(msg.result);
    } else if (msg.method === "Page.loadEventFired") {
      loadWaiters.splice(0).forEach((f) => f());
    } else if (msg.method === "Runtime.exceptionThrown") {
      exceptions.push((msg.params.exceptionDetails.exception?.description || "exception").split("\n")[0]);
    }
  });
  const send = (method, params = {}) => new Promise((ok, reject) => {
    const id = nextId++;
    pending.set(id, { resolve: ok, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) {
      throw new Error("the census could not drive the page - was a page function renamed?\n  "
        + (r.exceptionDetails.exception?.description || expression).split("\n")[0]);
    }
    return r.result.value;
  };
  const navigate = async (url) => {
    const loaded = new Promise((r) => loadWaiters.push(r));
    await send("Page.navigate", { url });
    await loaded;
    await sleep(400);
  };
  const submitAndWait = async (expression) => {
    const loaded = new Promise((r) => loadWaiters.push(r));
    await evaluate(expression);
    await loaded;
    await sleep(400);
  };

  await send("Page.enable");
  await send("Runtime.enable");
  await send("DOM.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1920, height: 1080, deviceScaleFactor: 1, mobile: false });
  await send("Network.setCookie", { name: "theme", value: THEME, url: BASE });
  await navigate(BASE + "/login");
  await submitAndWait(`(() => { document.getElementById('username').value = 'census.admin';
    document.getElementById('password').value = 'Census!2026'; document.querySelector('form.auth-form').submit(); })()`);
  await navigate(BASE + "/software");
  const htmlClass = await evaluate("document.documentElement.className");
  if ((THEME === "dark") !== /\bdark\b/.test(htmlClass)) throw new Error(`expected the ${THEME} theme, <html> has "${htmlClass}"`);

  const report = { theme: THEME, root: ROOT, states: [], checks: [] };
  const counted = { fills: 0, borders: 0, texts: 0, exceptions: 0, checks: 0, skipped: new Set() };

  async function state(label, clip) {
    const found = await evaluate(AUDIT);
    const errors = exceptions.splice(0);
    const known = [];
    found.texts = found.texts.filter((f) => {
      const k = allowanceFor(f);
      if (!k) return true;
      known.push({ ...f, why: k.why });
      const key = `${k.why} | ${f.el.split(".")[0]}`;
      allowanceUse.set(key, (allowanceUse.get(key) || 0) + 1);
      return false;
    });
    // Light surfaces are the point of the light theme.
    if (THEME === "light") { found.fills = []; found.borders = []; }
    report.states.push({ label, ...found, known, exceptions: errors });
    counted.fills += found.fills.length;
    counted.borders += found.borders.length;
    counted.texts += found.texts.length;
    counted.exceptions += errors.length;
    for (const el of found.skippedDisabled) counted.skipped.add(el.split(".")[0]);
    const bad = found.fills.length + found.borders.length + found.texts.length + errors.length;
    console.log(`  ${bad ? "FOUND" : "clean"}  ${label}${bad ? `  (${found.fills.length} fill, ${found.borders.length} border, ${found.texts.length} text, ${errors.length} error)` : ""}`);
    for (const f of found.fills) console.log(`           fill    ${f.bg}  ${f.el}`);
    for (const f of found.borders) console.log(`           border  ${f.border}  ${f.el}`);
    for (const f of found.texts) console.log(`           text    ${f.ratio}:1 ${f.fg} on ${f.on}  ${f.el}  "${f.text}"`);
    for (const e of errors) console.log(`           error   ${e}`);
    let params = { format: "png" };
    if (clip) {
      const rect = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(clip)}); if (!e) return null;
        const r = e.getBoundingClientRect(); return { x: Math.max(0, r.x - 16), y: Math.max(0, r.y - 16), width: r.width + 32, height: r.height + 32, scale: 1 }; })()`);
      if (rect) params = { format: "png", clip: rect };
    }
    const shot = await send("Page.captureScreenshot", params);
    writeFileSync(join(OUT, String(report.states.length).padStart(2, "0") + "-"
      + label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, "") + ".png"), Buffer.from(shot.data, "base64"));
  }
  function check(label, ok, detail) {
    report.checks.push({ label, ok, detail });
    if (!ok) counted.checks += 1;
    console.log(`  ${ok ? "pass " : "FAIL "}  ${label}${ok ? "" : "  <-- " + detail}`);
  }
  const reached = async (expression, label) => {
    if (!(await evaluate(expression))) throw new Error(`the census did not reach "${label}" - the page has changed`);
  };
  // What a class paints in THIS theme, measured like everything else.
  const probe = (cls, prop) => evaluate(`(() => { const p = document.createElement('div'); p.className = ${JSON.stringify(cls)};
    p.style.cssText = 'position:absolute;left:-9999px;width:4px;height:4px;border-style:solid;border-width:1px';
    document.body.appendChild(p); const v = getComputedStyle(p)[${JSON.stringify(prop)}]; p.remove(); return v; })()`);
  const computed = (selector, prop) => evaluate(`getComputedStyle(document.querySelector(${JSON.stringify(selector)}))[${JSON.stringify(prop)}]`);
  const CARD = "#add-software-form";

  console.log(`\nSoftware Library, ${THEME} theme, staged from ${ROOT}\n`);

  // ---- Local file --------------------------------------------------------
  await state("empty dropzone", CARD);
  await evaluate(`document.getElementById('dropzone-empty').dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: new DataTransfer() }))`);
  await state("dragging over the dropzone", CARD);
  await evaluate(`document.getElementById('dropzone-empty').dispatchEvent(new DragEvent('dragleave', { bubbles: true }))`);

  await evaluate(`(() => { document.getElementById('detecting-filename').textContent = 'setup-x64.exe (48.2 MB)'; showDropzoneState('detecting'); })()`);
  await reached(`!document.getElementById('dropzone-detecting').classList.contains('hidden')`, "reading");
  await state("reading an installer", CARD);

  await evaluate(`(() => {
    document.getElementById('detected_confidence_tier').value = 'high';
    document.getElementById('filled-filename').textContent = 'setup-x64.exe';
    document.getElementById('detect-status-msg').textContent = 'Detected - review and edit before adding';
    const n = document.getElementById('name'); n.value = 'Example App'; validateName();
    const v = document.getElementById('version'); v.value = '1.2.3'; validateVersion();
    showDropzoneState('filled'); })()`);
  await reached(`!document.getElementById('dropzone-filled').classList.contains('hidden') && !document.getElementById('filled-tier').classList.contains('hidden')`, "detected");
  await state("detected, high confidence", CARD);

  await evaluate(`document.getElementById('category-listbox-btn').click()`);
  await reached(`!document.getElementById('category-listbox-menu').classList.contains('hidden')`, "category list open");
  await state("category list open", CARD);
  const hoverAt = await evaluate(`(() => { const r = document.querySelector('#category-listbox-menu .ael-opt:nth-child(3)').getBoundingClientRect(); return { x: r.x + 30, y: r.y + r.height / 2 }; })()`);
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: hoverAt.x, y: hoverAt.y });
  await sleep(150);
  await state("category option hovered", "#category-listbox-menu");
  await evaluate(`document.querySelector('#category-listbox-menu .ael-opt:nth-child(3)').click()`);
  await state("category picked", CARD);
  await evaluate(`(() => { const s = document.getElementById('category'); s.value = ''; validateCategory(); s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await reached(`document.getElementById('category-listbox-btn').classList.contains('ael-invalid')`, "category invalid");
  await state("category invalid", CARD);

  for (const kind of ["failed", "unknown"]) {
    await evaluate(`showDetectDialog(${JSON.stringify(kind)}, 'Census message.')`);
    await sleep(300);
    await state(`detection dialog: ${kind}`, "#detect-dialog-card");
    // The script rewrites this card's whole class list on open: what it
    // paints NOW is what matters, not what the template said.
    const bg = await computed("#detect-dialog-card", "backgroundColor");
    const canvas = await probe("bg-canvas", "backgroundColor");
    check(`detection dialog (${kind}) paints the canvas after the script rebuilds it`, bg === canvas, `${bg} vs canvas ${canvas}`);
    await evaluate(`closeDetectDialog()`);
    await sleep(300);
  }

  await evaluate(`discardDetectedFile()`);
  await state("after replace", CARD);
  await evaluate(`showDropzoneError('Only .exe and .msi files are allowed.')`);
  await state("dropzone error", CARD);
  await evaluate(`hideDropzoneError()`);

  await evaluate(`showLoadingOverlay('offline')`);
  await sleep(200);
  await state("loading overlay", null);
  await evaluate(`document.getElementById('loading-overlay').classList.add('hidden')`);

  // The tier swap: exact, THEN name_only - the order that leaves a stale
  // border behind if remove() misses the class add() used.
  await evaluate(`(() => { const n = document.getElementById('name'); n.value = 'VLC media player'; validateName();
    const v = document.getElementById('version'); v.value = '3.0.20'; validateVersion(); openDuplicateDialog('exact'); })()`);
  await sleep(300);
  await state("duplicate dialog: exact", "#duplicate-dialog-card");
  const warn = await probe("border-warn-strong", "borderTopColor");
  check("exact-duplicate dialog carries the warn border", (await computed("#duplicate-dialog-card", "borderTopColor")) === warn, warn);
  await evaluate(`closeDuplicateDialog()`);
  await sleep(300);
  await evaluate(`openDuplicateDialog('name_only', '3.0.18')`);
  await sleep(300);
  await state("duplicate dialog: name_only, opened after exact", "#duplicate-dialog-card");
  const blue = await probe("border-blue-500", "borderTopColor");
  const now = await computed("#duplicate-dialog-card", "borderTopColor");
  check("switching to name_only replaces the warn border with blue", now === blue, `${now}, expected ${blue}`);
  await evaluate(`closeDuplicateDialog()`);
  await sleep(300);

  // ---- Chocolatey ----------------------------------------------------------
  await evaluate(`selectType('online')`);
  await state("chocolatey tab", CARD);
  await evaluate(`chocoShowSkeleton()`);
  await state("searching", CARD);
  await evaluate(`chocoRenderResults([
    { id: 'example', name: 'Example', title: 'Example package', version: '1.0.0', downloads: 120000 },
    { id: 'example.old', name: 'Example', title: 'Example (old)', version: '0.9.0', downloads: 900, deprecated: true }])`);
  await state("search results", CARD);
  const resultAt = await evaluate(`(() => { const r = document.querySelector('#choco-results .choco-option').getBoundingClientRect(); return { x: r.x + 30, y: r.y + r.height / 2 }; })()`);
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: resultAt.x, y: resultAt.y });
  await sleep(150);
  await state("search result hovered", CARD);
  await evaluate(`chocoShowNote('No packages found.')`);
  await state("no results", CARD);
  await evaluate(`chocoCloseResults(); chocoSetStatus(CHOCO_UNAVAILABLE, 'muted')`);
  await state("search unavailable", CARD);
  await evaluate(`chocoRenderResults([{ id: 'example.old', name: 'Example', title: 'Example (old)', version: '0.9.0', downloads: 900, deprecated: true }]); chocoChoose(0)`);
  await reached(`!document.getElementById('choco-picked').classList.contains('hidden')`, "package picked");
  await state("package selected", CARD);
  await evaluate(`document.getElementById('category-listbox-btn').click()`);
  await state("category list open on the chocolatey tab", CARD);
  await evaluate(`document.body.click()`);

  // ---- Saved Software ------------------------------------------------------
  await evaluate(`selectType('offline')`);
  await evaluate(`document.querySelector('[data-dt-rowmenu]').click()`);
  await sleep(300);
  await state("row menu", null);
  await evaluate(`(() => { const b = Array.from(document.querySelectorAll('button, [role=menuitem]')).find((x) => x.offsetParent && /^\\s*Edit\\s*$/.test(x.textContent)); b.click(); })()`);
  await sleep(500);
  await reached(`document.getElementById('sw-edit-drawer').open`, "edit drawer");
  await state("edit drawer", null);
  await evaluate(`document.getElementById('sw-edit-drawer').close()`);
  await sleep(300);

  // ---- The no-JS duplicate banner, through the real route -----------------
  for (const [version, label] of [["3.0.20", "exact"], ["3.0.18", "name_only"]]) {
    await navigate(BASE + "/software");
    await submitAndWait(`(() => { const f = document.getElementById('add-software-form');
      document.getElementById('type').value = 'online';
      document.getElementById('package_name').value = 'vlc';
      document.getElementById('name').value = 'VLC media player';
      document.getElementById('version').value = ${JSON.stringify(version)};
      document.getElementById('category').value = 'Multimedia';
      HTMLFormElement.prototype.submit.call(f); })()`);
    await reached(`!!document.querySelector('.banner-tone-warn, .banner-tone-info, [class*="bg-amber-50"], [class*="bg-blue-50"]')`, `duplicate banner (${label})`);
    await sleep(300);
    await state(`duplicate bounce: ${label}, dialog re-opened`, null);
    await evaluate(`closeDuplicateDialog()`);
    await sleep(300);
    await state(`duplicate bounce: ${label} banner`, CARD);
  }

  writeFileSync(join(OUT, "report.json"), JSON.stringify(report, null, 2));
  const unintended = counted.fills + counted.borders + counted.texts + counted.exceptions + counted.checks;
  const knownCount = report.states.reduce((n, s) => n + s.known.length, 0);
  console.log(`\n${report.states.length} states, ${report.checks.length} checks. `
    + `Unintended: ${counted.fills} fill, ${counted.borders} border, ${counted.texts} text, `
    + `${counted.exceptions} error, ${counted.checks} failed check. Known and recorded: ${knownCount}.`);
  if (counted.skipped.size) {
    console.log(`Text contrast not measured on disabled controls (exempt): ${[...counted.skipped].join(", ")}`);
  }
  const unused = KNOWN.flatMap((k) => k.elements.map((p) => `${k.why} | ${p.split(".")[0]}`))
    .filter((key) => !allowanceUse.has(key));
  if (unused.length) {
    console.log("Allowances that matched nothing this run (remove them if the finding is gone):");
    for (const key of unused) console.log(`  ${key}`);
  }
  console.log(`Screenshots and report.json: ${OUT}`);
  if (THEME === "light") console.log("Light theme is informational - it never fails the census.");
  exitCode = THEME === "dark" && unintended ? 1 : 0;
} catch (err) {
  console.error("\ncensus could not run: " + err.message);
  exitCode = 2;
} finally {
  cleanup();
  await sleep(500);
  try { rmSync(join(WORK, "server"), { recursive: true, force: true }); } catch { /* best effort */ }
  try { rmSync(join(WORK, "chrome-profile"), { recursive: true, force: true }); } catch { /* best effort */ }
}
process.exit(exitCode);
