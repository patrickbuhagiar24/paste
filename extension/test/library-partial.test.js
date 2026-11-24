"use strict";
const assert = require("node:assert/strict");
const Library = require("../library.js");

test("an unreadable Drive file does not hide other files", async () => {
  const drive = {
    FOLDER_MIME: "folder", TEXT_MIME: "text/plain",
    getAuthToken: async () => "tok", getFolder: async () => ({ mimeType: "folder" }),
    readIndexFile: async () => ({ id: "", index: null }),
    listChildren: async () => [
      { id: "bad", name: "Denied.txt", mimeType: "text/plain" },
      { id: "good", name: "Visible.txt", mimeType: "text/plain" },
      { id: "doc", name: "Native.txt", mimeType: "application/vnd.google-apps.document" },
    ],
    downloadText: async (token, id) => { if (id === "bad") throw new Error("403 denied"); return "visible content"; },
  };
  const native = { readConfig: async () => ({ drive: { folderId: "root" }, oauth: { client_id: "123-abc.apps.googleusercontent.com" } }) };
  const result = await Library.load(native, drive);
  assert.equal(result.tree.length, 2);
  assert.match(result.tree[0].loadError, /403/);
  assert.equal(result.tree[1].text, "visible content");
  assert.match(result.warnings[0], /Denied.*403/);
});
