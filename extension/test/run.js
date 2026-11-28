// Minimal test runner (no dependencies): test files register cases with
// global test(), the runner executes them sequentially and exits nonzero
// on failure. Usage: node extension/test/run.js
"use strict";

const path = require("node:path");

const cases = [];
globalThis.test = function test(name, fn) {
  cases.push({ name, fn });
};

let finished = false;
process.on("exit", function (code) {
  // A hanging test must never look like a successful run.
  if (!finished && code === 0) {
    console.error("FAIL - the test run ended before every case finished");
    process.exitCode = 1;
  }
});

(async function main() {
  for (const file of ["store.test.js", "hash.test.js", "drive.test.js", "library.test.js", "library-partial.test.js", "library-integration.test.js", "cache.test.js", "queue.test.js", "conflict.test.js", "commands.test.js", "config-source.test.js", "config-cache-reader.test.js", "sidepanel.test.js", "appearance.test.js", "content.test.js", "sites.test.js", "background.test.js", "dead-code.test.js"]) {
    require(path.join(__dirname, file));
  }
  let passed = 0;
  for (const { name, fn } of cases) {
    try {
      await fn();
      passed++;
      console.log("ok - " + name);
    } catch (err) {
      console.error("FAIL - " + name);
      console.error(err && err.stack ? err.stack : err);
      process.exit(1);
    }
  }
  finished = true;
  console.log(passed + " tests passed");
})().catch(() => process.exit(1));
