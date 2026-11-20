"use strict";
const assert = require("node:assert/strict");
const Manifest = require("../config-manifest.js");

function link(id, markdown = false) {
  const url = "https://drive.google.com/file/d/" + id + "/view?usp=sharing";
  return markdown ? "[config](" + url + ")" : url;
}
function response(text, headers = {}) {
  return { ok: true, status: 200, headers: { get: (name) => headers[name] }, text: async () => text };
}
function manifestText(overrides = {}) {
  return JSON.stringify(Object.assign({
    "pastex-google-drive.json": link("drive1", true),
    "pastex-sites.json": link("sites1"),
    "service-account.json": link("key1", true),
  }, overrides));
}

test("manifest accepts raw and Markdown-wrapped Drive links", async () => {
  const parsed = Manifest.parse(manifestText());
  assert.equal(parsed.kind, "manifest");
  assert.equal(parsed.files["pastex-google-drive.json"], Manifest.downloadUrl("drive1"));
  assert.equal(parsed.files["pastex-sites.json"], Manifest.downloadUrl("sites1"));
  assert.equal(parsed.files["service-account.json"], Manifest.downloadUrl("key1"));
});

test("manifest rejects non-Drive, folder and malformed links", async () => {
  assert.throws(() => Manifest.parse("{"), /not valid JSON/);
  assert.throws(() => Manifest.parse("[]"), /must be a JSON object/);
  assert.throws(() => Manifest.parse(JSON.stringify({ "pastex-google-drive.json": link("a") })), /missing pastex-sites.json/);
  for (const bad of [
    "https://example.org/file/d/abcd/view",
    "http://drive.google.com/file/d/abcd/view",
    "https://drive.google.com/drive/folders/abcd",
    "not-a-url",
  ]) {
    assert.throws(() => Manifest.parse(manifestText({ "pastex-sites.json": bad })), /must be an https:\/\/drive.google.com\/file\/d\/\.\.\. link/);
  }
});

test("linked files are downloaded and parsed with bounded size", async () => {
  const urls = [];
  const bodies = {
    [Manifest.downloadUrl("drive1")]: { folder_id: "root" },
    [Manifest.downloadUrl("sites1")]: { allowed_sites: [] },
    [Manifest.downloadUrl("key1")]: { type: "service_account" },
  };
  const fetchImpl = async (url) => {
    urls.push(url);
    return response(JSON.stringify(bodies[url]));
  };
  for (const id of ["drive1", "sites1", "key1"]) {
    const result = await Manifest.fetchJson(Manifest.downloadUrl(id), fetchImpl);
    assert.ok(result.json && typeof result.text === "string");
  }
  assert.equal(urls.length, 3);
  assert.ok(urls.every((url) => url.startsWith("https://drive.google.com/uc?export=download&id=")));

  await assert.rejects(
    Manifest.fetchJson(Manifest.downloadUrl("x"), async () => response("{}", { "content-length": String(Manifest.MAX_BYTES + 1) })),
    /too large/
  );
  await assert.rejects(
    Manifest.fetchJson(Manifest.downloadUrl("x"), async () => response("<html>not json</html>")),
    /not valid JSON/
  );
  await assert.rejects(
    Manifest.fetchJson(Manifest.downloadUrl("x"), async () => ({ ok: false, status: 404, headers: { get: () => undefined }, text: async () => "" })),
    /HTTP 404/
  );
});
