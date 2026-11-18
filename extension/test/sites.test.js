"use strict";
const assert = require("node:assert/strict");
const Sites = require("../sites.js");

test("site allowlist matches exact domains and explicit subdomains only", () => {
  const patterns = Sites.parse(["https://example.com", "https://*.work.test", "http://localhost"]);
  assert.deepEqual(patterns, ["https://example.com/*", "https://*.work.test/*", "http://localhost/*"]);
  assert(Sites.allows(patterns, "https://example.com/a"));
  assert(!Sites.allows(patterns, "https://sub.example.com/a"));
  assert(!Sites.allows(patterns, "http://example.com/"));
  assert(!Sites.allows(patterns, "https://example.com.attacker.test/"));
  assert(Sites.allows(patterns, "https://sub.work.test/a"));
  assert(Sites.allows(patterns, "https://work.test/"));
  assert(!Sites.allows(patterns, "https://notwork.test/"));
  assert(Sites.allows(patterns, "http://localhost:3000/"));
  assert(!Sites.allows(patterns, "chrome://extensions/"));
  assert(!Sites.allows([], "https://example.com/"));
});

test("invalid site lists fail closed", () => {
  for (const bad of [["https://*/*"], ["https://example.com/path"], ["https://example.com:443"],
    ["https://user@example.com"], ["https://.example.com"], ["https://-test.com"], [42], null]) {
    assert.throws(() => Sites.parse(bad));
  }
  assert.deepEqual(Sites.parse(["HTTPS://EXAMPLE.COM/", "https://example.com"]), ["https://example.com/*"]);
});
