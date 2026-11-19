"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

test("offscreen config reader rejects pages and responds only to the extension worker", async () => {
  let listener, reads = 0;
  const chrome = { runtime: { id: "abc", getURL: (p) => "chrome-extension://abc/" + p,
    onMessage: { addListener: (fn) => { listener = fn; } } } };
  const context = vm.createContext({ chrome, PastexHandleStore: {}, PastexConfigImport: {}, PastexSites: {},
    PastexConfigSource: { create: () => ({ readConfig: async () => { reads++; return { secret: "example" }; },
      readSites: async () => { reads++; return { allowedSites: [] }; } }) } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../config-reader.js"), "utf8"), context);
  const message = { target: "pastex-config-reader", operation: "drive" };
  assert.equal(listener(message, { id: "abc", url: "chrome-extension://abc/sidepanel.html" }, () => {}), false);
  assert.equal(listener(message, { id: "abc", url: "https://example.org" }, () => {}), false);
  assert.equal(listener(message, { id: "other", url: "chrome-extension://abc/background.js" }, () => {}), false);
  assert.equal(reads, 0);
  const response = await new Promise((resolve) => {
    assert.equal(listener(message, { id: "abc", url: "chrome-extension://abc/background.js" }, resolve), true);
  });
  assert.equal(response.ok, true);
  assert.equal(response.data.secret, "example");
  assert.equal(reads, 1);
});
