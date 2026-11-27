#!/usr/bin/env node
// Optional real-browser smoke test. Requires playwright-core and CHROME_BIN.
// It tests a local page only; it does not log in to Google Drive or WhatsApp.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright-core");

(async function main() {
  if (!process.env.CHROME_BIN) throw new Error("Set CHROME_BIN to a Chrome or Chromium executable.");
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN,
    headless: true, args: process.env.PASTEX_TEST_NO_SANDBOX === "1" ? ["--no-sandbox"] : [] });
  try {
    const page = await browser.newPage();
    await page.setContent('<textarea id="plain"></textarea><div id="chat" contenteditable="true"></div>');
    await page.evaluate(() => {
      window.testListener = null;
      window.chrome = { runtime: { id: "test-extension", sendMessage: async () => ({ allowed: false }),
        onMessage: { addListener(fn) { window.testListener = fn; } } } };
    });
    await page.evaluate(fs.readFileSync(path.join(__dirname, "../extension/content.js"), "utf8"));
    async function insert(text, sender = "test-extension") {
      return page.evaluate(({ text, sender }) => new Promise((resolve) => {
        window.testListener({ type: "pastex-insert", text }, { id: sender }, resolve);
      }), { text, sender });
    }
    await page.focus("#plain");
    assert.equal((await insert("first\n")).ok, true);
    assert.equal(await page.locator("#plain").inputValue(), "first\n");
    assert.equal((await insert("forbidden", "other-extension")).reason, "unauthorized");
    assert.equal(await page.locator("#plain").inputValue(), "first\n");
    await page.focus("#chat");
    assert.equal((await insert("first\nsecond\n")).ok, true);
    await page.keyboard.type("next");
    assert.equal(await page.locator("#chat").innerText(), "first\nsecond\nnext");
    await page.setContent('<button id="btn-review-conflict">Review</button><div id="conflict-overlay" hidden>' +
      '<p id="conflict-name"></p><p id="conflict-status"></p><textarea id="conflict-base"></textarea>' +
      '<textarea id="conflict-local"></textarea><textarea id="conflict-remote"></textarea>' +
      '<button id="conflict-copy-drive">Copy</button><button id="conflict-keep-drive">Use Drive</button>' +
      '<button id="conflict-keep-local">Use local</button><button id="conflict-close">Close</button></div>');
    await page.evaluate(fs.readFileSync(path.join(__dirname, "../extension/conflict-panel.js"), "utf8"));
    await page.evaluate(() => {
      window.reviewRequests = [];
      window.PastexConflictPanel.init({ get: (id) => document.getElementById(id),
        send: async (message) => {
          window.reviewRequests.push(message);
          return message.type === "pastex-conflict-preview"
            ? { ok: true, review: { id: "one", name: "Note", base: "old", local: "local", remote: "remote" } }
            : { ok: true };
        }, status: () => {}, clipboard: { writeText: async () => {} }, confirm: () => true, refresh: async () => {} });
    });
    await page.click("#btn-review-conflict");
    assert.equal(await page.locator("#conflict-local").inputValue(), "local");
    assert.equal(await page.locator("#conflict-remote").inputValue(), "remote");
    await page.click("#conflict-keep-local");
    assert.equal(await page.locator("#conflict-overlay").isHidden(), true);
    assert.equal(await page.evaluate(() => window.reviewRequests.at(-1).type), "pastex-conflict-keep-local");
    console.log("Browser smoke test passed: insertion, sender validation and conflict review.");
  } finally { await browser.close(); }
})().catch((err) => { console.error(err); process.exitCode = 1; });
