#!/usr/bin/env node
// Repeatable in-memory benchmark; it does not measure Google Drive or Chrome.
"use strict";
const { performance } = require("node:perf_hooks");
const Library = require("../extension/library.js");
const folders = 40, perFolder = 50;
let listings = 0, downloads = 0;
const native = { readConfig: async () => ({ drive: { folderId: "root", indexId: "" }, oauth: { client_id: "benchmark" } }) };
const drive = {
  FOLDER_MIME: "folder", TEXT_MIME: "text/plain",
  getAuthToken: async () => "token",
  getFolder: async () => ({ id: "root", mimeType: "folder" }),
  readIndexFile: async () => ({ id: "", index: null }),
  listChildren: async (_token, parent) => {
    listings++;
    if (parent === "root") return Array.from({ length: folders }, (_, n) => ({ id: "f" + n, name: "Folder " + n, mimeType: "folder", modifiedTime: "1" }));
    return Array.from({ length: perFolder }, (_, n) => ({ id: parent + "-" + n, name: "Snippet " + n + ".txt", mimeType: "text/plain", modifiedTime: "1" }));
  },
  downloadText: async (_token, id) => { downloads++; return "Text for " + id; },
};
(async () => {
  let start = performance.now();
  const first = await Library.load(native, drive);
  const firstMs = performance.now() - start;
  const firstLists = listings, firstDownloads = downloads;
  listings = downloads = 0;
  start = performance.now();
  await Library.load(native, drive, null, { tree: first.tree, bodies: first.bodies, texts: first.texts, metadata: first.metadata, signatures: first.signatures });
  console.log(JSON.stringify({ folders, snippets: folders * perFolder,
    cold: { ms: +firstMs.toFixed(1), listings: firstLists, downloads: firstDownloads },
    warm: { ms: +(performance.now() - start).toFixed(1), listings, downloads },
    note: "Synthetic in-memory Drive, no network or browser rendering" }, null, 2));
})().catch((err) => { console.error(err); process.exitCode = 1; });
