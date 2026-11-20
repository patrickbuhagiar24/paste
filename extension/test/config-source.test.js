"use strict";
const assert = require("node:assert/strict");
const Discovery = require("../config-import.js");
const Sites = require("../sites.js");
const Manifest = require("../config-manifest.js");
const { create } = require("../config-source.js");

const account = { type: "service_account", client_email: "a@example.com", private_key: "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n" };
function driveLink(id) { return "[file](https://drive.google.com/file/d/" + id + "/view?usp=sharing)"; }
function manifestText() {
  return JSON.stringify({
    "pastex-google-drive.json": driveLink("drive1"),
    "pastex-sites.json": driveLink("sites1"),
    "service-account.json": driveLink("key1"),
  });
}
function manifestBodies() {
  return {
    [Manifest.downloadUrl("drive1")]: JSON.stringify({ folder_url: "https://drive.google.com/drive/folders/linkedroot" }),
    [Manifest.downloadUrl("sites1")]: JSON.stringify({ allowed_sites: ["https://example.org"] }),
    [Manifest.downloadUrl("key1")]: JSON.stringify(account),
  };
}
function manifestHarness(bodies, initial) {
  let stored = initial;
  const store = { get: async () => stored, set: async (value) => { stored = value; } };
  const fetchImpl = async (url) => {
    if (!(url in bodies)) throw new Error("unexpected URL " + url);
    return { ok: true, status: 200, headers: { get: () => undefined }, text: async () => bodies[url] };
  };
  return { source: create(store, Discovery, Sites, Manifest, fetchImpl), get stored() { return stored; } };
}

test("a link manifest stores only URLs and loads all three configurations", async () => {
  const h = manifestHarness(manifestBodies());
  const info = await h.source.chooseManifestText(manifestText());
  assert.deepEqual(info, {
    drive: "linked pastex-google-drive.json",
    account: "linked service-account.json",
    sites: "linked pastex-sites.json",
  });
  assert.equal(h.stored.kind, "manifest");
  assert.deepEqual(Object.keys(h.stored.files), Manifest.NAMES);
  assert.ok(!JSON.stringify(h.stored).includes("private_key"));
  assert.equal((await h.source.readConfig()).drive.folderId, "linkedroot");
  assert.deepEqual((await h.source.readSites()).allowedSites, ["https://example.org"]);
});

test("invalid linked sites fail closed without replacing a working manifest", async () => {
  const h = manifestHarness(manifestBodies());
  await h.source.chooseManifestText(manifestText());
  const before = h.stored;
  const bodies = manifestBodies();
  bodies[Manifest.downloadUrl("sites1")] = JSON.stringify({ allowed_sites: ["<all_urls>"] });
  const broken = manifestHarness(bodies, before);
  await assert.rejects(broken.source.chooseManifestText(manifestText()), /Invalid site origin/);
  assert.equal(broken.stored, before);
  await assert.rejects(broken.source.readSites(), /Invalid site origin/);
});

test("a missing or malformed manifest is rejected before any request", async () => {
  const h = manifestHarness(manifestBodies());
  await assert.rejects(h.source.readConfig(), /Choose a configuration manifest/);
  await assert.rejects(h.source.chooseManifestText("{"), /not valid JSON/);
  assert.equal(h.stored, undefined);
});

test("linked files are downloaded in parallel and memoized per manifest", async () => {
  const bodies = manifestBodies();
  let stored;
  const store = { get: async () => stored, set: async (value) => { stored = value; } };
  const calls = [];
  let active = 0, max = 0;
  const fetchImpl = async (url) => {
    calls.push(url);
    active++; max = Math.max(max, active);
    await new Promise(setImmediate);
    active--;
    return { ok: true, status: 200, headers: { get: () => undefined }, text: async () => bodies[url] };
  };
  const source = create(store, Discovery, Sites, Manifest, fetchImpl);
  await source.chooseManifestText(manifestText());
  assert.equal(calls.length, 3);
  assert.equal(max, 3, "the three linked files must not be fetched sequentially");
  await source.readConfig();
  await source.readSites();
  await source.readConfig();
  assert.equal(calls.length, 3, "repeat reads must use the in-memory linked files");
  source.invalidate();
  await source.readConfig();
  assert.equal(calls.length, 5, "invalidation refetches only the two config links");
});
