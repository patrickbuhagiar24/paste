// Unit tests for extension/native.js with a stubbed chrome.runtime.
"use strict";

const assert = require("node:assert/strict");

function installStub(behavior) {
  const calls = [];
  globalThis.chrome = {
    runtime: {
      lastError: undefined,
      sendNativeMessage(host, message, callback) {
        calls.push({ host, message });
        behavior(host, message, callback, calls.length);
      },
    },
  };
  return calls;
}

function uninstallStub() {
  delete globalThis.chrome;
}

const Native = require("../native.js");

test("send resolves the host response", async () => {
  installStub((host, message, callback) => {
    assert.equal(host, "com.pastex.native");
    callback({ ok: true, echo: message.type });
  });
  try {
    Native.resetCache();
    const res = await Native.send({ type: "pastex-status" });
    assert.equal(res.echo, "pastex-status");
  } finally {
    uninstallStub();
  }
});

test("send rejects on host-reported errors", async () => {
  installStub((host, message, callback) => callback({ ok: false, error: "nope" }));
  try {
    Native.resetCache();
    await assert.rejects(Native.send({ type: "x" }), /nope/);
  } finally {
    uninstallStub();
  }
});

test("send rejects when the host is missing", async () => {
  const calls = installStub((host, message, callback) => {
    globalThis.chrome.runtime.lastError = { message: "No such native host" };
    callback(undefined);
    globalThis.chrome.runtime.lastError = undefined;
  });
  try {
    Native.resetCache();
    await assert.rejects(Native.status(), /No such native host/);
    // Negative cache: the second probe fails without calling again.
    await assert.rejects(Native.status(), /unavailable/);
    assert.equal(calls.length, 1);
  } finally {
    uninstallStub();
  }
});

test("send rejects without chrome.runtime", async () => {
  uninstallStub();
  Native.resetCache();
  await assert.rejects(Native.send({ type: "x" }), /unavailable/);
});

test("helpers send the right message types", async () => {
  const calls = installStub((host, message, callback) => callback({ ok: true }));
  try {
    Native.resetCache();
    await Native.readConfig();
    await Native.readSites();
    await Native.importConfigs({ "pastex-sites.json": { allowed_sites: [] } });
    assert.deepEqual(
      calls.map((c) => c.message.type),
      ["pastex-read-config", "pastex-read-sites", "pastex-import-configs"]
    );
  } finally {
    uninstallStub();
  }
});
