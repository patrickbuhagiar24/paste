// Exercise the floating launcher with a minimal simulated browser DOM.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function setupContent(options = {}) {
  const events = {};
  const timers = new Map();
  let timerId = 0;
  let inserted = "";
  let document;
  class Element {
    constructor(tag) {
      this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {};
      this.style = {}; this.classList = { add() {} }; this.value = ""; this.type = "";
      this.parent = null; this.isContentEditable = false;
      this.selectionStart = 0; this.selectionEnd = 0;
    }
    appendChild(el) { if (el.parent) el.remove(); el.parent = this; this.children.push(el); return el; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; }
    setAttribute(name, value) { this[name] = value; }
    dispatchEvent() { return true; }
    setRangeText(text, start, end) {
      inserted = text;
      this.value = this.value.slice(0, start) + text + this.value.slice(end);
      this.selectionStart = this.selectionEnd = start + text.length;
    }
    getAttribute(name) { return this[name]; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    attachShadow() { const root = new Element("shadow"); root.host = this; root.parent = this; return root; }
    getBoundingClientRect() { return { left: 20, right: 240, top: 40, bottom: 80 }; }
    focus() {
      if (document.activeElement && events.focusout) events.focusout({ target: document.activeElement });
      let el = this;
      while (el.parent && !el.host) el = el.parent;
      const target = el.host || this;
      document.activeElement = target;
      if (events.focusin) events.focusin({ target });
    }
    closest() { return this; }
    set textContent(value) { this.text = value; this.children = []; }
    get textContent() { return this.text || ""; }
  }
  const root = new Element("html");
  document = {
    documentElement: root, body: new Element("body"), activeElement: null,
    createElement: (tag) => new Element(tag),
    addEventListener: (name, fn) => { events[name] = fn; },
    contains(el) { while (el) { if (el === root) return true; el = el.parent; } return false; },
    hasFocus: () => true,
    visibilityState: "visible",
    execCommand(command, ignored, text) { inserted = text; return true; },
  };
  const messages = [];
  const runtime = {
    id: "pastex-extension-id",
    sendMessage: async (message) => { messages.push(message); return message.type === "pastex-site-allowed" ?
      (options.allowed ? options.allowed() : { allowed: true, capability: "token" }) : { ok: true }; },
    onMessage: { addListener(fn) { events.message = fn; } },
  };
  const windowEvents = {};
  const window = {
    innerWidth: 1200, innerHeight: 800, addEventListener(name, fn) { windowEvents[name] = fn; },
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const context = vm.createContext({ window, document, chrome: { runtime }, console, Event: class Event { constructor(type) { this.type = type; } } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../content.js"), "utf8"), context);
  const target = root.appendChild(new Element("textarea"));
  function runTimers(ms) {
    for (const [id, timer] of [...timers]) if (timer.ms === ms) { timers.delete(id); timer.fn(); }
  }
  return { root, target, events, windowEvents, document, runTimers, messages,
    element: (tag) => new Element(tag), get inserted() { return inserted; } };
}
async function tick() { await new Promise(setImmediate); }

test("lightning opens side panel without an in-page picker", async () => {
  const page = setupContent();
  page.target.focus();
  await tick();
  const host = page.root.children.find((el) => el.id === "pastex-float-host");
  assert.ok(host);
  assert.equal(host.__pastexButton.style.display, "block");
  host.__pastexButton.listeners.mousedown({ isTrusted: true, preventDefault() {} });
  await tick();
  assert.equal(page.messages.filter((m) => m.type === "pastex-open-panel").length, 1);
  assert.equal(page.messages.at(-1).capability, "token");
  assert.ok(!page.root.children.some((el) => el.id === "pastex-picker-host"));
  host.__pastexButton.listeners.mousedown({ isTrusted: false, preventDefault() {} });
  assert.equal(page.messages.filter((m) => m.type === "pastex-open-panel").length, 1);
  assert.equal(page.events.keydown, undefined);
});

test("denied sites cannot display the launcher", async () => {
  const page = setupContent({ allowed: () => ({ allowed: false }) });
  page.target.focus(); await tick();
  assert.ok(!page.root.children.some((el) => el.id === "pastex-float-host"));
});

test("side panel insertion uses the previously focused field", async () => {
  const page = setupContent();
  page.target.focus(); await tick();
  let result;
  page.events.message({ type: "pastex-insert", text: "Drive content" }, { id: "pastex-extension-id" }, (response) => { result = response; });
  assert.equal(result.ok, true);
  assert.equal(page.inserted, "Drive content");
});

test("another extension cannot ask a page to insert text", async () => {
  const page = setupContent();
  page.target.focus(); await tick();
  let result;
  page.events.message({ type: "pastex-insert", text: "evil" }, { id: "another-extension" }, (response) => { result = response; });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "unauthorized");
  assert.equal(page.inserted, "");
});

test("multiline snippets are inserted into a textarea verbatim", async () => {
  const page = setupContent();
  page.target.focus(); await tick();
  const text = "first line\n\nsecond line  \r\nthird line";
  let result;
  page.events.message({ type: "pastex-insert", text }, { id: "pastex-extension-id" }, (response) => { result = response; });
  assert.equal(result.ok, true);
  assert.equal(page.inserted, text);
  assert.equal(page.target.value, text);
});

test("a trailing newline reaches a textarea, and a one-line input is told so", async () => {
  // The newline buttons append "\n". In a textarea that is a real line; in a
  // single-line input there is no line to break to, and pretending otherwise is
  // what made the buttons look broken.
  const page = setupContent();
  page.target.focus(); await tick();
  let result;
  page.events.message({ type: "pastex-insert", text: "snippet\n" }, { id: "pastex-extension-id" }, (response) => { result = response; });
  assert.equal(result.ok, true);
  assert.equal(page.target.value, "snippet\n", "the textarea keeps the appended newline");

  // The same message against an <input> answers honestly instead.
  const input = page.root.appendChild(page.element("input"));
  input.type = "text";
  input.focus(); await tick();
  page.events.message({ type: "pastex-insert", text: "snippet\n" }, { id: "pastex-extension-id" }, (response) => { result = response; });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "single-line");
});

test("hiding a tab reports visibility but never sends the queue", async () => {
  const page = setupContent();
  page.target.focus();
  await tick();
  page.messages.length = 0;
  // Switching away from the tab is not closing it.
  page.document.visibilityState = "hidden";
  page.events.visibilitychange();
  await tick();
  assert.ok(page.messages.some((m) => m.type === "pastex-site-visible" && m.visible === false),
    "the worker still learns the site is not on screen");
  assert.equal(page.messages.some((m) => m.type === "pastex-flush-now"), false,
    "a hidden tab must not send the queue");
});

test("closing the tab sends the queue", async () => {
  const page = setupContent();
  page.target.focus();
  await tick();
  page.messages.length = 0;
  page.windowEvents.pagehide();
  await tick();
  assert.equal(page.messages.filter((m) => m.type === "pastex-flush-now").length, 1,
    "the page going away is the one moment the queue is sent");
  assert.ok(page.messages.some((m) => m.type === "pastex-site-visible" && m.visible === false));
});
