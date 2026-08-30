// bro-web.mjs — hybrid web browsing engine for builderBRO
//
// Gives the agent real browser control over any website:
//   navigate, read, click, type, select, submit forms, evaluate JS,
//   screenshots, cookies, and persistent login sessions.
//
// Engine strategy:
//   1. If `playwright` is installed -> full headless Chromium (handles JS apps,
//      logins, SPAs, everything).
//   2. Otherwise -> zero-dependency fetch mode: GET pages, extract text/links/
//      forms, and submit simple HTML forms over raw HTTP.
//
// All exported functions follow the TOOLS convention in cli.mjs:
// take an args STRING, return a result STRING.

import { existsSync, mkdirSync } from "fs";
import { join } from "path";
import { homedir } from "os";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const SESSION_DIR = join(homedir(), ".bro", "web-profile"); // persistent logins/cookies

// ── engine state ─────────────────────────────────────────────
var pw = null;          // playwright module (if available)
var ctx = null;         // browser context
var page = null;        // active page
var refs = [];          // interactive elements of the last snapshot
var lastFieldIdx = -1;  // last field the agent typed into (for submit)

function hasPlaywright() {
  try { require.resolve("playwright"); return true; } catch (e) { return false; }
}

async function ensurePage() {
  if (!hasPlaywright()) return null;
  if (!pw) pw = require("playwright");
  if (!ctx) {
    try {
      mkdirSync(SESSION_DIR, { recursive: true });
      // Persistent context = cookies/sessions/logins survive between runs.
      ctx = await pw.chromium.launchPersistentContext(SESSION_DIR, {
        headless: true,
        viewport: { width: 1280, height: 800 },
        args: ["--no-sandbox", "--disable-dev-shm-usage"],
      });
    } catch (e) {
      throw new Error("browser launch failed: " + e.message);
    }
  }
  if (!page) page = ctx.pages()[0] || (await ctx.newPage());
  return page;
}

async function closeBrowser() {
  try { if (ctx) await ctx.close(); } catch (e) {}
  ctx = null; page = null; refs = [];
}

// ── snapshot: enumerate everything the agent can interact with ──
async function snapshot(pageObj, maxEls) {
  maxEls = maxEls || 60;
  const els = await pageObj.evaluate(function () {
    var sel = 'a[href],button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[contenteditable="true"],[onclick]';
    var out = [], seen = new Set();
    document.querySelectorAll(sel).forEach(function (el) {
      if (out.length >= 200) return;
      var r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return;
      var st = getComputedStyle(el);
      if (st.display === "none" || st.visibility === "hidden") return;
      // skip invisible wrappers around visible inputs
      if (el.tagName === "DIV" || el.tagName === "SPAN") {
        var hasInner = el.querySelector("input,textarea,select,button,a[href]");
        if (hasInner) return;
      }
      var key = el.tagName + "|" + (el.type || "") + "|" + (el.name || "") + "|" + (el.id || "");
      if (seen.has(key)) return;
      seen.add(key);
      out.push({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute("type") || "",
        id: el.id || "",
        name: el.getAttribute("name") || "",
        ph: el.getAttribute("placeholder") || "",
        label: el.getAttribute("aria-label") || "",
        text: (el.innerText || el.value || "").trim().replace(/\s+/g, " ").substring(0, 80),
        href: el.tagName === "A" ? el.href : "",
        value: ["input", "textarea"].includes(el.tagName.toLowerCase()) ? String(el.value || "").substring(0, 60) : ""
      });
    });
    // Also extract page text so the agent can READ the page
    var t = "";
    if (document.body && document.body.innerText) t = document.body.innerText;
    if (!t.trim() && document.body) t = document.body.textContent;
    if (!t.trim()) t = document.documentElement.innerText;
    if (!t.trim()) t = document.documentElement.textContent;
    return { title: document.title, url: location.href, bodyText: t, els: out };
  });

  refs = els.els.slice(0, maxEls);
  // Grab page text so the agent can read, not just click
  var bodyText = (els.bodyText || "").replace(/\s+/g, " ").trim().substring(0, 3000);
  var lines = ["PAGE: " + els.title, "URL: " + els.url, "", "CONTENT:\n" + (bodyText || "(no text extracted)"), "", "INTERACTIVE ELEMENTS:"];
  refs.forEach(function (e, i) {
    var desc = "[" + i + "] <" + e.tag + (e.type ? " type=" + e.type : "") +
      (e.id ? " id=" + e.id : "") + (e.name ? " name=" + e.name : "") + ">";
    if (e.ph) desc += ' placeholder="' + e.ph + '"';
    if (e.label) desc += " label=" + e.label;
    if (e.text && !["input", "textarea"].includes(e.tag)) desc += " text=" + JSON.stringify(e.text);
    else if (e.value) desc += " value=" + JSON.stringify(e.value);
    if (e.href) desc += "\n     -> " + e.href;
    lines.push(desc);
  });
  lines.push("", "Use [n] indexes with web_click / web_type / web_select.");
  return lines.join("\n");
}

// ── shared helpers ───────────────────────────────────────────
function parseRef(argStr) {
  var m = String(argStr || "").trim().match(/^(\d+)\b([\s\S]*)$/);
  if (!m) return null;
  var idx = parseInt(m[1], 10);
  return { idx: idx, rest: m[2].trim(), el: refs[idx] || null };
}

function cleanText(html) {
  return String(html)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, " ").trim();
}

// ── fetch-mode fallback implementations ──────────────────────
var fetchCookieJar = {}; // host -> {name:value}

function cookieHeaderFor(url) {
  try {
    var host = new URL(url).hostname;
    var jar = fetchCookieJar[host];
    return jar ? Object.keys(jar).map(function (k) { return k + "=" + jar[k]; }).join("; ") : "";
  } catch (e) { return ""; }
}
function storeCookies(url, res) {
  try {
    var host = new URL(url).hostname;
    var jar = fetchCookieJar[host] = fetchCookieJar[host] || {};
    var raw = typeof res.headers.getSetCookie === "function" ? res.headers.getSetCookie() : [];
    (raw.length ? raw : [res.headers.get("set-cookie")].filter(Boolean)).forEach(function (c) {
      var kv = c.split(";")[0].split("=");
      if (kv.length >= 2) jar[kv[0].trim()] = kv.slice(1).join("=").trim();
    });
  } catch (e) {}
}

async function fetchGet(url) {
  var res = await fetch(url, {
    headers: {
      "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
      ...(cookieHeaderFor(url) ? { Cookie: cookieHeaderFor(url) } : {})
    },
    redirect: "follow",
    signal: AbortSignal.timeout(30000)
  });
  storeCookies(url, res);
  return res;
}

// ── TOOLS-compatible tool functions ──────────────────────────

/** web_open <url> — navigate and show an interaction map of the page. */
export async function webOpen(a) {
  var url = String(a || "").trim();
  if (!url) return "ERR: usage: web_open https://example.com";
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  try {
    var pg = await ensurePage();
    if (pg) {
      await pg.goto(url, { waitUntil: "load", timeout: 60000 }).catch(function(){});
      await pg.waitForTimeout(2000); // let SPA hydration + headless-shell rendering settle
      return await snapshot(pg);
    }
    // fetch fallback
    var res = await fetchGet(url);
    var html = await res.text();
    var title = (html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [, ""])[1];
    var links = [];
    var re = /<a\s[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi, m;
    while ((m = re.exec(html)) !== null && links.length < 30) {
      var txt = cleanText(m[2]).substring(0, 60);
      if (txt) links.push(txt + "  ->  " + new URL(m[1], url).href);
    }
    var body = cleanText(html).substring(0, 4000);
    return "[fetch-mode — install playwright for clicking/typing]\n" +
      "URL: " + res.url + " (HTTP " + res.status + ")\nTITLE: " + cleanText(title) +
      "\n\nLINKS:\n" + (links.join("\n") || "(none)") +
      "\n\nTEXT:\n" + (body || "(empty)");
  } catch (e) { return "ERR: " + e.message; }
}

/** web_text — plain text of the current page (browser mode). */
export async function webText() {
  try {
    var pg = await ensurePage();
    if (!pg) return "ERR: web_text needs playwright (npm i playwright). Use web_raw instead.";
    return (await pg.evaluate(function () {
      var t = "";
      // headless-shell sometimes needs multiple fallbacks for text content
      t = document.body && document.body.innerText;
      if (!t || !t.trim()) t = document.body && document.body.textContent;
      if (!t || !t.trim()) t = document.documentElement.innerText;
      if (!t || !t.trim()) t = document.documentElement.textContent;
      return t || "";
    })).substring(0, MAX_WEB_OUT);
  } catch (e) { return "ERR: " + e.message; }
}

/** web_raw <url> — quick HTTP read of any URL without touching the browser session. */
export async function webRaw(a) {
  var url = String(a || "").trim();
  if (!url) return "ERR: usage: web_raw <url>";
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  try {
    var res = await fetchGet(url);
    var ct = res.headers.get("content-type") || "";
    var body = await res.text();
    if (/json/i.test(ct)) return "HTTP " + res.status + " " + ct + "\n" + body.substring(0, MAX_WEB_OUT);
    return "HTTP " + res.status + "\n" + cleanText(body).substring(0, MAX_WEB_OUT);
  } catch (e) { return "ERR: " + e.message; }
}

/** web_click <n> — click the n-th element from the last web_open snapshot. */
export async function webClick(a) {
  try {
    var p = parseRef(a);
    if (!pgReady()) return needPW();
    if (!p || !p.el) return "ERR: no element [" + (p ? p.idx : "?") + "] — run web_open first";
    var pg = await ensurePage();
    var loc = locatorFor(pg, p.el);
    await loc.click({ timeout: 10000 });
    await pg.waitForTimeout(1500);
    lastFieldIdx = -1;
    return "clicked [" + p.idx + "]" + (p.el.text ? " (" + p.el.text.substring(0, 40) + ")" : "") + "\n\n" + await snapshot(pg);
  } catch (e) { return "ERR: " + e.message; }
}

/** web_type <n> <text> — focus element n and type text into it. */
export async function webType(a) {
  try {
    var p = parseRef(a);
    if (pgReady()) {
      if (!p || !p.el) return "ERR: no element — run web_open first";
      var pg = await ensurePage();
      var loc = locatorFor(pg, p.el);
      await loc.click({ timeout: 10000 });
      await loc.fill("");           // clear existing value
      await loc.type(p.rest, { delay: 15 });  // human-ish typing beats naive detectors
      lastFieldIdx = p.idx;
      return "typed into [" + p.idx + "] <" + p.el.tag + (p.el.name ? " name=" + p.el.name : "") + ">: " + JSON.stringify(p.rest.substring(0, 60));
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_select <n> <value> — pick an option in a <select>. */
export async function webSelect(a) {
  try {
    var p = parseRef(a);
    if (pgReady()) {
      if (!p || !p.el) return "ERR: no element — run web_open first";
      var pg = await ensurePage();
      await locatorFor(pg, p.el).selectOption(p.rest).catch(async function () {
        // fall back to matching by visible label
        await locatorFor(pg, p.el).selectOption({ label: p.rest });
      });
      return "selected \"" + p.rest + "\" in [" + p.idx + "]";
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_key <key> — press Enter, Tab, Escape etc. on the focused element. */
export async function webKey(a) {
  try {
    if (pgReady()) {
      var pg = await ensurePage();
      await pg.keyboard.press(String(a || "Enter").trim());
      await pg.waitForTimeout(1000);
      return "pressed " + a;
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_submit — submit the form containing the last typed field (or press Enter there). */
export async function webSubmit() {
  try {
    if (pgReady()) {
      var pg = await ensurePage();
      if (lastFieldIdx >= 0 && refs[lastFieldIdx]) {
        var submitted = await pg.evaluate(function (idx) {
          var el = window.__broRefs && window.__broRefs[idx];
          if (!el) return false;
          var f = el.closest("form");
          if (f) { f.requestSubmit ? f.requestSubmit() : f.submit(); return true; }
          return false;
        }, lastFieldIdx).catch(function () { return false; });
        if (!submitted) await locatorFor(pg, refs[lastFieldIdx]).press("Enter");
      } else {
        // no typed field — submit the first form on the page
        await pg.evaluate(function () {
          var f = document.querySelector("form");
          if (f) f.requestSubmit ? f.requestSubmit() : f.submit();
        });
      }
      await pg.waitForLoadState("domcontentloaded", { timeout: 20000 }).catch(function(){});
      await pg.waitForTimeout(1500);
      return "submitted.\n\n" + await snapshot(pg);
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_scroll <down|up|top|bottom> */
export async function webScroll(a) {
  try {
    if (pgReady()) {
      var pg = await ensurePage();
      var dir = String(a || "down").trim();
      await pg.evaluate(function (d) {
        if (d === "top") window.scrollTo(0, 0);
        else if (d === "bottom") window.scrollTo(0, document.body.scrollHeight);
        else window.scrollBy(0, d === "up" ? -600 : 600);
      }, dir);
      await pg.waitForTimeout(400);
      return "scrolled " + dir + "\n\n" + await snapshot(pg, 40);
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_eval <js> — run arbitrary JS in the page and return the result (full API access). */
export async function webEval(a) {
  try {
    if (pgReady()) {
      var pg = await ensurePage();
      var r = await pg.evaluate(new Function("return (" + String(a) + ")"));
      return typeof r === "string" ? r : JSON.stringify(r, null, 1).substring(0, MAX_WEB_OUT);
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_screenshot [filename] — save a PNG of the current page. */
export async function webShot(a) {
  try {
    if (pgReady()) {
      var pg = await ensurePage();
      var file = String(a || "").trim() || join(process.cwd(), "bro-shot-" + Date.now() + ".png");
      await pg.screenshot({ path: file, fullPage: false });
      return "saved " + file;
    }
    return needPW();
  } catch (e) { return "ERR: " + e.message; }
}

/** web_cookies — dump cookies so the agent can see auth state. */
export async function webCookies() {
  try {
    if (ctx) {
      var c = await ctx.cookies();
      if (!c.length) return "(no cookies)";
      return c.map(function (k) { return k.domain + "  " + k.name + "=" + String(k.value).substring(0, 40); }).join("\n");
    }
    return Object.keys(fetchCookieJar).map(function (h) {
      return h + ": " + Object.keys(fetchCookieJar[h]).join(", ");
    }).join("\n") || "(no cookies)";
  } catch (e) { return "ERR: " + e.message; }
}

/** web_search <query> — search DuckDuckGo and return results (title, URL, snippet) in one shot. */
/** web_search <query> — search DuckDuckGo and return results (title, URL, snippet) in one shot. */
export async function webSearch(a) {
  var query = String(a || "").trim();
  if (!query) return "ERR: usage: web_search <your search query>";
  try {
    var url = "https://html.duckduckgo.com/html/?q=" + encodeURIComponent(query);
    var res = await fetch(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"
      },
      redirect: "follow",
      signal: AbortSignal.timeout(20000)
    });
    if (!res.ok) return "ERR: search failed (HTTP " + res.status + ")";
    var html = await res.text();
    var results = [];
    var re = /<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>[\s\S]*?<a[^>]*class="result__snippet"[^>]*>([\s\S]*?)<\/a>/gi;
    var m;
    while ((m = re.exec(html)) !== null && results.length < 8) {
      var rawUrl = m[1];
      var decoded = decodeURIComponent((rawUrl.split("uddg=")[1] || rawUrl).split("&")[0]);
      results.push({
        title: cleanText(m[2]),
        url: decoded,
        snippet: cleanText(m[3]).substring(0, 250)
      });
    }
    if (!results.length) return 'No results found for "' + query + '". Try rewording.';
    var lines = ["Search: " + query, "Results: " + results.length, ""];
    results.forEach(function (r, i) {
      lines.push((i + 1) + ". " + r.title);
      lines.push("   " + r.url);
      if (r.snippet) lines.push("   " + r.snippet);
      lines.push("");
    });
    return lines.join("\n");
  } catch (e) { return "ERR: " + e.message; }
}

/** web_close — shut the headless browser down. */
export async function webClose() {
  await closeBrowser();
  return "browser closed";
}

/** web_status — which engine is live, current URL, session info. */
export async function webStatus() {
  var engine = ctx ? "playwright/chromium" : (hasPlaywright() ? "playwright (not launched yet)" : "fetch fallback");
  return "engine: " + engine +
    "\nsession dir: " + SESSION_DIR + (existsSync(SESSION_DIR) ? " (persisted logins)" : " (empty)") +
    (page && page.url() && page.url() !== "about:blank" ? "\ncurrent page: " + page.url() : "") +
    "\nelements in last snapshot: " + refs.length;
}

// ── internals ────────────────────────────────────────────────
var MAX_WEB_OUT = 14000;

function pgReady() { return hasPlaywright(); }

function needPW() {
  return "ERR: this action needs a real browser. Run: npm install playwright && npx playwright install chromium  (or use web_raw / web_open for read-only access)";
}

function cssEscape(s) { return String(s).replace(/([^\w\u00C0-\uFFFF-])/g, "\\$1"); }

function locatorFor(pg, el) {
  // Re-find the element robustly from its descriptor rather than a stale handle.
  if (el.id) return pg.locator("#" + cssEscape(el.id)).first();
  if (el.name && el.tag !== "a") return pg.locator(el.tag + '[name="' + el.name.replace(/"/g, '\\"') + '"]').first();
  if (el.ph) return pg.getByPlaceholder(el.ph).first();
  if (el.label) return pg.getByLabel(el.label).first();
  if (el.tag === "a" && el.href) return pg.locator('a[href="' + el.href.replace(/"/g, '\\"') + '"]').first();
  if (el.text) return pg.locator(el.tag, { hasText: el.text }).first();
  return pg.locator(el.tag).first();
}

// Keep window.__broRefs fresh so web_submit can resolve elements inside evaluate().
if (hasPlaywright()) {
  var _origSnapshot = snapshot;
  snapshot = async function (pg, maxEls) {
    var out = await _origSnapshot(pg, maxEls);
    try {
      await pg.evaluate(function (sel) {
        window.__broRefs = Array.prototype.slice.call(document.querySelectorAll(sel));
      }, 'a[href],button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[contenteditable="true"],[onclick]');
    } catch (e) {}
    return out;
  };
}

// graceful shutdown so Chromium doesn't linger
process.on("exit", function () { try { if (ctx) ctx.close(); } catch (e) {} });
process.on("SIGINT", async function () { await closeBrowser(); process.exit(130); });

// The object merged into TOOLS by cli.mjs — keys must be lowercase (extractT lowercases).
export var WEB_TOOLS = {
  web_open: webOpen,
  web_text: webText,
  web_raw: webRaw,
  web_click: webClick,
  web_type: webType,
  web_select: webSelect,
  web_key: webKey,
  web_submit: webSubmit,
  web_scroll: webScroll,
  web_eval: webEval,
  web_screenshot: webShot,
  web_cookies: webCookies,
  web_search: webSearch,
  web_close: webClose,
  web_status: webStatus
};
