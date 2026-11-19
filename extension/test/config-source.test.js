"use strict";
const assert = require("node:assert/strict");
const Discovery = require("../config-import.js");
const Sites = require("../sites.js");
const { create } = require("../config-source.js");

function directory(values, granted = true) {
  const handles = {};
  for (const [name, value] of Object.entries(values)) {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    handles[name] = { kind: "file", name, getFile: async () => ({ name, size: Buffer.byteLength(text), text: async () => text }) };
  }
  return { name: "selected", queryPermission: async () => granted ? "granted" : "prompt",
    async *values() { yield* Object.values(handles); },
    async getFileHandle(name) { if (!handles[name]) { const err = new Error("missing"); err.name = "NotFoundError"; throw err; } return handles[name]; },
  };
}
function harness(initial) {
  let folder = initial;
  const store = { get: async () => folder, set: async (value) => { folder = value; } };
  return { source: create(store, Discovery, Sites), get folder() { return folder; } };
}
const account = { type: "service_account", client_email: "a@example.com", private_key: "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----" };
function valid() { return directory({ "pastex-google-drive.json": { folder_url: "https://drive.google.com/drive/folders/abcd" }, "pastex-sites.json": { allowed_sites: ["https://example.org"] }, "download.json": account }); }

test("selected folder is the only source; only its handle is remembered", async () => {
  const h = harness(null), selected = valid();
  await assert.rejects(h.source.readSites(), /Choose a configuration folder/);
  const info = await h.source.choose(selected);
  assert.equal(h.folder, selected);
  assert.deepEqual(info, { drive: "pastex-google-drive.json", account: "download.json", sites: "pastex-sites.json" });
  assert.equal((await h.source.readConfig()).drive.folderId, "abcd");
  assert.deepEqual((await h.source.readSites()).allowedSites, ["https://example.org"]);
});

test("permission loss and invalid sites fail closed without changing previous handle", async () => {
  const selected = valid(), h = harness(selected);
  const denied = directory({}, false);
  await assert.rejects(h.source.choose(denied), /folder access is unavailable/);
  assert.equal(h.folder, selected);
  const invalid = directory({ "pastex-google-drive.json": { folder_id: "root" }, "pastex-sites.json": { allowed_sites: ["<all_urls>"] }, "service-account.json": account });
  await assert.rejects(h.source.choose(invalid), /Invalid site origin/);
  assert.equal(h.folder, selected);
  await assert.rejects(harness(denied).source.readSites(), /folder access is unavailable/);
});

test("sites can be checked without reading service-account key", async () => {
  const dir = directory({ "pastex-sites.json": { allowed_sites: [] } });
  dir.values = async function* () { throw new Error("key enumeration is forbidden"); };
  assert.deepEqual((await harness(dir).source.readSites()).allowedSites, []);
});
