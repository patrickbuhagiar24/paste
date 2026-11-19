// Exercise the injected picker with a minimal simulated browser DOM.
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
    }
    appendChild(el) { if (el.parent) el.remove(); el.parent = this; this.children.push(el); return el; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; }
    setAttribute(name, value) { this[name] = value; }
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
  const runtime = {
    sendMessage: async ({ type }) => type === "pastex-site-allowed" ?
      (options.allowed ? options.allowed() : { allowed: true }) :
      (options.load ? options.load() : { ok: true, tree: [{ id: "1", kind: "text", name: "Test", text: "Drive content" }], warnings: [] }),
    onMessage: { addListener(fn) { events.message = fn; } },
  };
  const window = {
    innerWidth: 1200, innerHeight: 800, addEventListener() {},
    setTimeout(fn, ms) { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  const context = vm.createContext({ window, document, chrome: { runtime }, console });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../content.js"), "utf8"), context);
  const target = root.appendChild(new Element("textarea"));
  function runTimers(ms) {
    for (const [id, timer] of [...timers]) if (timer.ms === ms) { timers.delete(id); timer.fn(); }
  }
  return { root, target, events, runTimers, get inserted() { return inserted; } };
}
async function tick() { await new Promise(setImmediate); }

test("lightning button toggles Drive picker; Escape is not intercepted", async () => {
  const page = setupContent();
  page.target.focus();
  await tick();
  const buttonHost = page.root.children.find((el) => el.id === "pastex-float-host");
  assert.ok(buttonHost);
  const button = buttonHost.__pastexButton;
  assert.equal(button.style.display, "block");
  const click = () => button.listeners.mousedown({ preventDefault() {} });
  click();
  await tick();
  let picker = page.root.children.find((el) => el.id === "pastex-picker-host");
  assert.ok(picker);
  assert.equal(page.root.children.at(-1), buttonHost, "toggle stays above overlapping picker");
  page.runTimers(180); // focus moved from the text field to the picker
  assert.equal(button.style.display, "block", "lightning remains clickable while picker is open");
  let prevented = false;
  picker.__pastexInput.listeners.keydown({ key: "Escape", preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.ok(page.root.children.includes(picker));
  assert.equal(page.events.keydown, undefined, "no global Escape handler");
  picker.__pastexInput.listeners.blur(); // a pending blur from the old picker
  click();
  assert.ok(!page.root.children.includes(picker));
  assert.equal(page.target, page.root.children[0]);
  await tick();
  click();
  await tick();
  picker = page.root.children.find((el) => el.id === "pastex-picker-host");
  assert.ok(picker);
  page.runTimers(150);
  assert.ok(page.root.children.includes(picker), "old blur must not close the new picker");
  const item = picker.__pastexList.children[0];
  picker.__pastexList.listeners.mousedown({ target: item, preventDefault() {} });
  await tick();
  assert.equal(page.inserted, "Drive content");
  assert.ok(!page.root.children.includes(picker));
});

test("old shortcut message cannot open the picker", () => {
  const page = setupContent();
  let responded = false;
  assert.equal(page.events.message({ type: "pastex-toggle-picker" }, {}, () => { responded = true; }), false);
  assert.equal(responded, false);
  assert.ok(!page.root.children.some((el) => el.id === "pastex-picker-host"));
});

test("closing during authorization or Drive loading does not reopen the picker", async () => {
  let releaseAuth, releaseLoad, count = 0;
  const page = setupContent({
    allowed: () => ++count === 2 ? new Promise((resolve) => { releaseAuth = resolve; }) : { allowed: true },
    load: () => new Promise((resolve) => { releaseLoad = resolve; }),
  });
  page.target.focus();
  await tick();
  const button = page.root.children.find((el) => el.id === "pastex-float-host").__pastexButton;
  const click = () => button.listeners.mousedown({ preventDefault() {} });
  click(); click();
  releaseAuth({ allowed: true });
  await tick();
  assert.ok(!page.root.children.some((el) => el.id === "pastex-picker-host"));
  click();
  await tick();
  assert.ok(page.root.children.some((el) => el.id === "pastex-picker-host"));
  click();
  releaseLoad({ ok: true, tree: [], warnings: [] });
  await tick();
  assert.ok(!page.root.children.some((el) => el.id === "pastex-picker-host"));
});
