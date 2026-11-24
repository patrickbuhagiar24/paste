"use strict";
/* The background worker reads configuration from chrome.storage only. These
 * tests pin the behaviour that replaced the offscreen folder reader. */
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

function load(store) {
  const context = vm.createContext({ self: {}, console });
  context.self = context;
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../config-cache-reader.js"), "utf8"), context);
  return { create: context.PastexConfigCacheReader.create };
}

function storage(initial) {
  let data = initial || {};
  return {
    get: async (key) => (key in data ? { [key]: data[key] } : {}),
    set: async (values) => { data = Object.assign({}, data, values); },
    peek: () => data,
  };
}

const oauthFile = { client_id: "123456789012-abcdefghijklmnop.apps.googleusercontent.com" };
function record(files) {
  return {
    "pastex-config-cache": { version: 3, savedAt: "2026-01-01T00:00:00.000Z", folder: "config", files: files },
  };
}
function goodFiles() {
  return {
    "pastex-google-drive.json": { folder_url: "https://drive.google.com/drive/folders/rootid" },
    "pastex-sites.json": { allowed_sites: ["https://example.org"] },
    "pastex-oauth.json": oauthFile,
  };
}

test("the worker reads configuration from storage without any folder access", async () => {
  const store = storage(record(goodFiles()));
  const config = load(store).create(store);
  const drive = await config.readConfig();
  assert.equal(drive.drive.folderId, "rootid");
  assert.equal(drive.oauth.client_id, oauthFile.client_id);
  assert.equal(drive.oauthError, "");
  assert.equal(drive.driveError, "");
  assert.deepEqual((await config.readSites()).allowedSites, ["https://example.org"]);
});

test("folder_id wins over folder_url and a missing id is reported, not thrown at read time", async () => {
  const files = goodFiles();
  files["pastex-google-drive.json"] = { folder_id: "explicit", folder_url: "https://drive.google.com/drive/folders/other" };
  const explicit = load(storage(record(files))).create(storage(record(files)));
  assert.equal((await explicit.readConfig()).drive.folderId, "explicit");

  const broken = goodFiles();
  broken["pastex-google-drive.json"] = { folder_url: "https://drive.google.com/drive/folders/" };
  const store = storage(record(broken));
  const config = load(store).create(store);
  const result = await config.readConfig();
  assert.equal(result.drive, null);
  assert.match(result.driveError, /pastex-google-drive\.json/);
});

test("an unusable client id is reported through oauthError", async () => {
  const files = goodFiles();
  files["pastex-oauth.json"] = { client_id: "123456789012-abcdefghijklmnop" };
  const store = storage(record(files));
  const config = load(store).create(store);
  const result = await config.readConfig();
  assert.equal(result.oauth, null);
  assert.match(result.oauthError, /pastex-oauth\.json/);
});

test("sites fail closed and a missing configuration explains what to do", async () => {
  const files = goodFiles();
  files["pastex-sites.json"] = { allowed_sites: "nope" };
  const store = storage(record(files));
  const config = load(store).create(store);
  await assert.rejects(config.readSites(), /allowed_sites must be an array/);

  const empty = storage({});
  const none = load(empty).create(empty);
  await assert.rejects(none.readConfig(), /Choose a configuration folder/);
  await assert.rejects(none.readSites(), /Choose a configuration folder/);
});

test("invalidate clears the stored configuration", async () => {
  const store = storage(record(goodFiles()));
  const config = load(store).create(store);
  await config.invalidate();
  await assert.rejects(config.readConfig(), /Choose a configuration folder/);
});
