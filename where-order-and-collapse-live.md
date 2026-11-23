# Where Pastex stores list order and collapse state in Google Drive

## Short answer

**There is no file for either.** Both live in a single **label (custom file
property)** on the **root folder** of your library — the Google Drive folder
whose id is in `pastex-google-drive.json`.

Not a `.json` file, not a `.txt` file, not a hidden file. The label belongs to
the folder, so it never appears as an item in Drive and never counts as a
snippet.

## The label used

| Label | Attached to | Contents |
| --- | --- | --- |
| `pastex_index` | **the root folder** | One JSON string holding the **whole** order and collapse state |

Code: `extension/drive.js` → `setIndex()`. It is the **only** writer of order
and collapse state. The older per-item labels `pastex_order` / `pastex_collapsed`
are neither written **nor read** any more.

## What `pastex_index` contains

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
  of the root folder; other folders use their own id.
- `collapsed` is keyed by **folder id**, with `"1"` (folded) or `"0"` (open).

## Why one label instead of one per item?

Reorder and fold share a single label, so:

- one reorder is **one** Drive write, not one write per row;
- one fold/unfold is **one** write;
- a fold never loses a pending reorder, because both are written together.

## If this is an older library

Older versions stored order and collapse **per item**. Those labels are now
**ignored**: `parseIndex()` reads only `pastex_index` on the root folder, and
when it is missing or carries an unknown version the library is shown in
Drive's order with every folder open. There is no migration and no per-item
read — see `DEVELOPMENT.md` ("state carries a version").

The practical effect for a library arranged before 2.19: the manual order and
the folded folders return to their defaults, while **the snippet contents do
not change at all**.

## How to look at it yourself in Drive

1. Open your library folder at <https://drive.google.com>.
2. Right-click the folder → **Details** (or the info panel).
3. Look under **Properties** — `pastex_index` holds the JSON above.

Through the API:

```
GET https://www.googleapis.com/drive/v3/files/<ROOT_FOLDER_ID>?fields=properties
```

Or with the script in this repository:

```bash
./scripts/get-pastex-label.sh -k /path/to/service-account.json
```

## Notes

- The label is **metadata**, not file content, so Drive does not count it as a
  file and it does not appear in file search.
- Because it sits on the root folder, **changing the configured folder** means
  using a different set of order/collapse data; the old one stays intact in its
  own folder.
- `"1"`/`"0"` are used instead of `true`/`false` because Drive properties only
  hold strings.
