# Where Pastex stores list order and collapse state in Google Drive

## Short answer

**One small file in your library folder: `pastex-index.json`.**

Both facts live in that one file, so it never appears as a snippet row and never
counts as a snippet. It is Pastex's own metadata: deleting it is harmless, and
the library simply returns to Drive's own order with every folder open.

## The file

| File | Where | Contents |
| --- | --- | --- |
| `pastex-index.json` | directly inside the library folder | One JSON document holding the **whole** order and collapse state |

Code: `extension/drive.js` → `readIndexFile()` / `writeIndexFile()`. They are the
**only** readers and writer of order and collapse state. The older per-item
labels `pastex_order` / `pastex_collapsed` are neither written **nor read** any
more, and neither is the old folder property.

## Why not a folder property?

Because it cannot fit. A Google Drive custom property is limited to **124 bytes
per property, including the key name** (`pastex_index` alone is 12 of those
bytes). Two rows with real Drive ids already produce a longer label:

| Rows | Label size | Limit 124 B |
| --- | --- | --- |
| 1 | 118 B | fits |
| 2 | 154 B | **too long** |
| 4 | 226 B | **too long** |

So Drive answered `HTTP 400` to every reorder and every fold. The panel stored
the change locally, could not send it, and — because a 400 is a permanent
failure — eventually reported it as **refused**. A file has no such limit: the
same label for 400 rows is written in one request.

## What the file contains

```json
{
  "v": 2,
  "order": {
    "17Xk...rootId": ["idB", "idC", "idA"]
  },
  "collapsed": {
    "17Xk...folderId": "1",
    "17Xk...otherFolder": "0"
  }
}
```

- `v` is the shape version. Any other version is **discarded**, not guessed at.
- `order` is keyed by **parent folder id**. The `rootId` key means the contents
  of the root folder; other folders use their own id. The root is **not** keyed
  as `""` — that was a bug that made only subfolders orderable.
- `collapsed` is keyed by **folder id**, with `"1"` (folded) or `"0"` (open).

## Why one file instead of one entry per item?

Reorder and fold share one document, so:

- one reorder is **one** Drive write, not one write per row;
- one fold/unfold is **one** write;
- a fold never loses a pending reorder, because both are written together.

## If this is an older library

Older versions stored order and collapse **per item**, then in a folder property.
Both are now **ignored**: the reader only looks at `pastex-index.json`, and when
it is missing or carries an unknown version the library is shown in Drive's
order with every folder open. There is no migration and no per-item read — see
`DEVELOPMENT.md` ("state carries a version").

The practical effect: the manual order and the folded folders return to their
defaults, while **the snippet contents do not change at all**.

## How to look at it yourself in Drive

1. Open your library folder at <https://drive.google.com>.
2. Find `pastex-index.json` in the list of files.
3. Open or download it — that is the JSON above.

Through the API:

```
GET https://www.googleapis.com/drive/v3/files/<ROOT_FOLDER_ID>?q=name='pastex-index.json'
GET https://www.googleapis.com/drive/v3/files/<INDEX_FILE_ID>?alt=media
```

Or with the script in this repository:

```bash
./scripts/get-pastex-label.sh -k /path/to/service-account.json
```

## Notes

- Because it is a file, it **does** appear in a plain Drive listing. Pastex
  filters it out by name, so it is never shown as a row.
- It is not a snippet: Pastex never edits it as text and never counts it.
- Changing the configured folder means using a different index file; the old
  one stays intact in its own folder.
- `"1"`/`"0"` are used instead of `true`/`false` to keep the document stable
  across versions.
