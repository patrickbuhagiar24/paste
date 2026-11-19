"use strict";
const assert = require("node:assert/strict");
const ConfigImport = require("../config-import.js");
function file(name, value, root = "chosen") {
  const content = typeof value === "string" ? value : JSON.stringify(value);
  return { name, size: Buffer.byteLength(content), webkitRelativePath: root + "/" + name, text: async () => content };
}

test("folder detection prefers new names and accepts a downloaded service-account key", async () => {
  const result = await ConfigImport.discover([
    file("google-drive.json", { folder_id: "old" }),
    file("pastex-google-drive.json", { folder_id: "new" }),
    file("sites.json", { allowed_sites: ["https://a.example"] }),
    file("my-cloud-key.json", { type: "service_account", client_email: "svc", private_key: "key" }),
    file("other.json", { x: 1 }),
    file("service-account.json", { type: "service_account" }, "chosen/nested"),
  ]);
  assert.equal(result.files["pastex-google-drive.json"].folder_id, "new");
  assert.equal(result.files["pastex-sites.json"].allowed_sites[0], "https://a.example");
  assert.equal(result.sources["service-account.json"], "my-cloud-key.json");
  assert.deepEqual(result.missing, []);
});

test("folder detection accepts partial sets, named keys, and ignores nested configuration", async () => {
  const named = file("service-account.json", { type: "service_account", client_email: "named" });
  const result = await ConfigImport.discover([named, file("backup.json", { type: "service_account" }), file("pastex-sites.json", {}, "chosen/nested")]);
  assert.equal(result.files["service-account.json"].client_email, "named");
  assert.deepEqual(result.missing, ["pastex-google-drive.json", "pastex-sites.json"]);
});

test("folder detection rejects ambiguous keys, duplicate files, invalid or oversized named files", async () => {
  await assert.rejects(ConfigImport.discover([file("a.json", { type: "service_account" }), file("b.json", { type: "service_account" })]), /More than one/);
  await assert.rejects(ConfigImport.discover([file("sites.json", {}), file("sites.json", {})]), /Duplicate/);
  await assert.rejects(ConfigImport.discover([file("pastex-sites.json", "{")]), /not valid JSON/);
  const huge = file("pastex-google-drive.json", "{}"); huge.size = 256 * 1024 + 1;
  await assert.rejects(ConfigImport.discover([huge]), /too large/);
});
