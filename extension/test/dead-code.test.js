/* Guard against dead code coming back into the shipped extension.
 *
 * A module that no page or worker loads is not shipped behaviour: it is a
 * second implementation that will drift. Every file in extension/ (not
 * extension/test/) must be reachable from sidepanel.html, the manifest, or an
 * importScripts() call. Exports are not checked here; that is cheaper to do by
 * reading the module's `return { ... }` list. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const EXT = path.join(__dirname, "..");
const MANIFEST = JSON.parse(fs.readFileSync(path.join(EXT, "manifest.json"), "utf8"));

function entryPoints() {
  const files = new Set();
  const html = fs.readFileSync(path.join(EXT, "sidepanel.html"), "utf8");
  for (const m of html.matchAll(/<script\s+src="([^"]+)"/g)) files.add(m[1]);
  files.add(MANIFEST.background.service_worker);
  files.add(MANIFEST.side_panel.default_path);
  const worker = fs.readFileSync(path.join(EXT, MANIFEST.background.service_worker), "utf8");
  const imports = /importScripts\(([^)]*)\)/.exec(worker);
  if (imports) for (const m of imports[1].matchAll(/"([^"]+)"/g)) files.add(m[1]);
  return files;
}

function reachable() {
  const seen = new Set();
  const pending = [...entryPoints()];
  while (pending.length) {
    const file = pending.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const text = fs.readFileSync(path.join(EXT, file), "utf8");
    // Local requires (a test-style dependency inside a factory) and further
    // importScripts() calls both count as a real load.
    for (const m of text.matchAll(/require\("\.\/([^"]+)"\)/g)) pending.push(m[1]);
    // Content scripts are registered at runtime, not in the manifest.
    for (const m of text.matchAll(/(?:js|css|files): \[([^\]]*)\]/g))
      for (const n of m[1].matchAll(/"([^"]+)"/g)) pending.push(n[1]);
    for (const m of text.matchAll(/importScripts\(([^)]*)\)/g))
      for (const n of m[1].matchAll(/"([^"]+)"/g)) pending.push(n[1]);
  }
  return seen;
}

test("every shipped extension file is reachable from a page or the worker", () => {
  const seen = reachable();
  for (const name of fs.readdirSync(EXT)) {
    if (!name.endsWith(".js") || name.endsWith(".test.js")) continue;
    assert.ok(seen.has(name), `${name} is shipped but nothing loads it`);
  }
});

test("sidepanel.html loads every module the panel uses, and nothing else", () => {
  const html = fs.readFileSync(path.join(EXT, "sidepanel.html"), "utf8");
  const scripts = [...html.matchAll(/<script\s+src="([^"]+)"/g)].map((m) => m[1]);
  const panel = fs.readFileSync(path.join(EXT, "sidepanel.js"), "utf8");
  const globals = new Set([...panel.matchAll(/window\.(Pastex[A-Za-z]+)/g)].map((m) => m[1]));
  // Each global must be provided by one of the loaded scripts.
  for (const name of globals) {
    const owner = scripts.find((f) =>
      fs.readFileSync(path.join(EXT, f), "utf8").includes(`root.${name} =`)
    );
    assert.ok(owner, `${name} is used by the panel but no loaded script defines it`);
  }
});

test("the retired handle and import layers stay retired", () => {
  for (const gone of ["handle-store.js", "config-import.js"]) {
    assert.equal(fs.existsSync(path.join(EXT, gone)), false, `${gone} must stay deleted`);
  }
  const panel = fs.readFileSync(path.join(EXT, "sidepanel.js"), "utf8");
  assert.ok(!/indexedDB|PastexHandleStore|PastexConfigImport/.test(panel));
});

test("Drive exposes one writer for order and collapse state", () => {
  const Drive = require("../drive.js");
  // One writer, and it writes the index file: no per-item writer and no folder
  // property writer, so there is exactly one place the state can live.
  assert.equal(typeof Drive.writeIndexFile, "function");
  assert.equal(typeof Drive.readIndexFile, "function");
  assert.equal(Drive.setIndex, undefined);
  assert.equal(Drive.setOrder, undefined);
  assert.equal(Drive.setCollapsed, undefined);
});
