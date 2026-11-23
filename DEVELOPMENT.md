# Development policy

Pastex is **in development**. There is no installed base to keep compatible
with, so the rule is simple: the project moves forward. It does not carry two
implementations of the same thing, and it does not adapt new code to old state.

That is only safe because of what is *not* allowed to move: the snippets
themselves. Everything below is written to keep those untouched.

## The three classes of state

| Class | Examples | Rule |
| --- | --- | --- |
| **Content** | the `.txt` files in the Google Drive folder, their names and folders | **Never** changed, migrated or deleted by a format change. It is the user's data, readable by any app. |
| **Presentation metadata** | order and collapse state (`pastex_index`), the folder's own labels | **May** be dropped. The worst case is that rows appear in Drive's order again and folders reopen. |
| **Cache** | `pastex.drive.local.v1`, `pastex-config-cache`, the queue | **Always** disposable. Rebuilt from Drive: a cache is never a source of truth. |

A change is acceptable when it only touches the last two rows. It is not
acceptable when it touches the first row, even if the change looks cosmetic.

## The rules

1. **No backward compatibility.** When a mechanism is replaced, the old one is
   deleted in the same commit. No fallbacks, no feature detection for old
   browser versions, no "still read for older libraries".
2. **State carries an explicit version.** Every persisted shape has a version
   number (`pastex_index` → `"v": 2`, `pastex-config-cache` → `version: 1`).
   An unknown version is **discarded and rebuilt**, never guessed at field by
   field.
3. **Content is out of bounds.** A format change never reads, rewrites or
   removes the snippet files. If a change would require touching them, it is a
   data migration and needs its own written plan.
4. **One suite, one name, one path.** Distribution identifiers (APT suite,
   extension id, package name) have exactly one current value. Renaming means
   deleting the old one, not publishing both.
5. **Deletions are visible.** Anything that can change what a user sees is a
   `BREAKING:` entry in `debian/changelog`, with what was dropped and what the
   user may notice. `apt changelog pastex` is the delivery channel.

Rules 1 and 2 are enforced by tests (`tests/test_development_policy.py`).
Rules 3–5 are reviewed, and the changelog convention is checked too.

## Why not keep compatibility while it is cheap?

Because "cheap now" is how a codebase ends up with two ways to store the same
fact. That is not hypothetical here: `pastex_order`/`pastex_collapsed` per item
were still being read as a second source of truth next to `pastex_index`, and
`handle-store.js`/`config-import.js` were still shipped for a design that had
already been replaced. They were removed in 2.16–2.18.

When a user is on an older state, the honest answer is:

- presentation and cache state: it resets, and the panel says so implicitly by
  showing Drive's order;
- content: it is untouched and always will be.

## If a real installed base ever exists

Then this policy changes **deliberately**, with a written migration path and a
test that exercises the old format. Until then, adding a fallback "just in
case" is the thing that needs justification, not deleting one.

## For contributors and agents

- Before adding a compatibility branch, read the three classes above. If the
  data is content, stop. If it is metadata or cache, delete instead.
- Prefer one explicit version check over many defensive `typeof` checks.
- When deleting a path, delete its test at the same time, and add a test that
  the path stays gone (see `extension/test/dead-code.test.js`).
- Scratching a broken local state during development:
  `./scripts/reset-pastex-state.sh` (cache only; Drive is never touched).
