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
    execCommand(command, ignored, text) { inserted = text; return true; },
  };
  const messages = [];
  const runtime = {
    id: "pastex-extension-id",
    sendMessage: async (message) => { messages.push(message); return message.type === "pastex-site-allowed" ?
      (options.allowed ? options.allowed() : { allowed: true, capability: "token" }) : { ok: true }; },
    onMessage: { addListener(fn) { events.message = fn; } },
  };
  const window = {
    innerWidth: 1200, innerHeight: 800, addEventListener() {},
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const context = vm.createContext({ window, document, chrome: { runtime }, console, Event: class Event { constructor(type) { this.type = type; } } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../content.js"), "utf8"), context);
  const target = root.appendChild(new Element("textarea"));
  function runTimers(ms) {
    for (const [id, timer] of [...timers]) if (timer.ms === ms) { timers.delete(id); timer.fn(); }
  }
  return { root, target, events, runTimers, messages, get inserted() { return inserted; } };
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
  const text = "baris pertama\n\nbaris kedua  \r\nbaris ketiga";
  let result;
  page.events.message({ type: "pastex-insert", text }, { id: "pastex-extension-id" }, (response) => { result = response; });
  assert.equal(result.ok, true);
  assert.equal(page.inserted, text);
  assert.equal(page.target.value, text);
});
