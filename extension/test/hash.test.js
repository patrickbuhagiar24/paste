// The content hash of a body is the key of the body cache, so it has to be
// a real SHA-256 of the UTF-8 text.
"use strict";
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const Hash = require("../hash.js");

function reference(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

test("the body hash matches the SHA-256 of the text", () => {
  const samples = [
    "",
    "a",
    "abc",
    "hello world",
    "x".repeat(55),
    "y".repeat(56),
    "z".repeat(64),
    "w".repeat(1000),
    "ünïcödé ✓ 😀",
    "line one\nline two\ttab",
  ];
  for (const sample of samples) {
    assert.equal(Hash.sha256Hex(sample), reference(sample), JSON.stringify(sample.slice(0, 24)));
  }
});

test("the body hash is stable and differs for different content", () => {
  assert.equal(Hash.sha256Hex("same"), Hash.sha256Hex("same"));
  assert.notEqual(Hash.sha256Hex("same"), Hash.sha256Hex("other"));
  assert.match(Hash.sha256Hex("same"), /^[0-9a-f]{64}$/);
});
