"use strict";
const assert = require("node:assert/strict");
const Discovery = require("../config-import.js");
const Sites = require("../sites.js");
const { create } = require("../config-source.js");

const account = { type: "service_account", client_email: "a@example.com", private_key: "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n" };

/* A File System Access style directory handle: async iterator over entries,
 * each file entry yielding a File with text(). */
function fileEntry(name, contents) {
  return { kind: "file", name: name, getFile: async () => ({ name: name, size: contents.length, text: async () => contents }) };
}
function dirHandle(name, files, folders) {
  const entries = Object.keys(files).map((n) => fileEntry(n, files[n])).concat(folders || []);
  return { kind: "directory", name: name, values: async function* () { for (const e of entries) yield e; } };
}
function goodFiles() {
  return {
    "pastex-google-drive.json": JSON.stringify({ folder_url: "https://drive.google.com/drive/folders/rootfolder" }),
    "pastex-sites.json": JSON.stringify({ allowed_sites: ["https://example.org"] }),
    "service-account.json": JSON.stringify(account),
  };
}
function harness(files, folders) {
  let stored;
  const store = { get: async () => stored, set: async (value) => { stored = value; } };
  return { source: create(store, Discovery, Sites), get stored() { return stored; }, handle: dirHandle("config", files, folders) };
}

test("a chosen folder is discovered by content and stores only the handle", async () => {
  const h = harness(goodFiles());
  const info = await h.source.chooseFolder(h.handle);
  assert.deepEqual(info, {
    drive: "folder pastex-google-drive.json",
    account: "folder service-account.json",
    sites: "folder pastex-sites.json",
  });
  assert.equal(h.stored.kind, "folder");
  assert.equal(h.stored.handle, h.handle);
  assert.ok(!JSON.stringify(Object.keys(h.stored)).includes("private_key"));
  assert.equal((await h.source.readConfig()).drive.folderId, "rootfolder");
  assert.deepEqual((await h.source.readSites()).allowedSites, ["https://example.org"]);
});

test("files are detected regardless of name order, extra files and nested folders", async () => {
  const files = goodFiles();
  files["notes.txt"] = "ignore me";
  files["random.json"] = JSON.stringify({ hello: "world" });
  const h = harness(files, [dirHandle("nested", goodFiles())]);
  await h.source.chooseFolder(h.handle);
  assert.equal((await h.source.readConfig()).drive.folderId, "rootfolder");
});

test("the service-account key is found even when Google renamed it", async () => {
  const files = goodFiles();
  delete files["service-account.json"];
  files["my-project-1a2b3c.json"] = JSON.stringify(account);
  const h = harness(files);
  await h.source.chooseFolder(h.handle);
  assert.equal((await h.source.readConfig()).serviceAccount.client_email, "a@example.com");
});

test("two service-account keys are rejected when the name is not the expected one", async () => {
  // Discovery only scans other JSON names when service-account.json is absent.
  const files = goodFiles();
  delete files["service-account.json"];
  files["alpha-key.json"] = JSON.stringify(account);
  files["beta-key.json"] = JSON.stringify(account);
  const h = harness(files);
  await assert.rejects(h.source.chooseFolder(h.handle), /More than one service-account key/);
});

test("invalid sites fail closed without replacing a working folder", async () => {
  const h = harness(goodFiles());
  await h.source.chooseFolder(h.handle);
  const before = h.stored;
  const files = goodFiles();
  files["pastex-sites.json"] = JSON.stringify({ allowed_sites: ["<all_urls>"] });
  const broken = harness(files);
  broken.source = create({ get: async () => before, set: async () => { throw new Error("must not store"); } }, Discovery, Sites);
  await assert.rejects(broken.source.chooseFolder(broken.handle), /Invalid site origin/);
  assert.equal(broken.stored, undefined, "a broken folder must not be persisted");
});

test("a folder without the required files is rejected with a clear message", async () => {
  // Only sites present: the folder_id check fails first.
  const partial = harness({ "pastex-sites.json": JSON.stringify({ allowed_sites: [] }) });
  await assert.rejects(partial.source.chooseFolder(partial.handle),
    /needs a pastex-google-drive\.json with a valid folder_id/);
  // Folder id present but no key.
  const noKey = harness({
    "pastex-google-drive.json": JSON.stringify({ folder_id: "rootfolder" }),
    "pastex-sites.json": JSON.stringify({ allowed_sites: [] }),
  });
  await assert.rejects(noKey.source.chooseFolder(noKey.handle), /needs a valid service-account\.json key/);
  const empty = harness({});
  await assert.rejects(empty.source.chooseFolder(empty.handle), /No \.json files found/);
});

test("reading before choosing a folder explains what to do", async () => {
  const h = harness(goodFiles());
  await assert.rejects(h.source.readConfig(), /Choose a configuration folder in Config first/);
  await assert.rejects(h.source.readSites(), /Choose a configuration folder in Config first/);
});

test("a read folder is memoized until invalidated", async () => {
  const h = harness(goodFiles());
  await h.source.chooseFolder(h.handle);
  let scans = 0;
  const counting = dirHandle("config", goodFiles());
  // Assign the method on the object (not via shorthand) so `kind` survives.
  counting.values = async function* () { scans++; yield* dirHandle("config", goodFiles()).values(); };
  const source = create({ get: async () => ({ kind: "folder", handle: counting, name: "config" }), set: async () => {} }, Discovery, Sites);
  await source.readConfig();
  await source.readSites();
  await source.readConfig();
  assert.equal(scans, 2, "the second sites read must reuse the memoized scan");
  source.invalidate();
  await source.readConfig();
  assert.equal(scans, 3, "invalidation forces a fresh scan");
});
