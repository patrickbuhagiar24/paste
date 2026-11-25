# Pastex — architecture for a reader who does not program

This document explains **how Pastex works** without technical terms. The point
is that whoever holds this project (or asks for the next fix) can tell which
part to open and what it means for the user.

For the technical version (file names, function names, data shapes) see
[`extension/README.md`](extension/README.md), [`ANALYSIS.md`](ANALYSIS.md) and
[`DEVELOPMENT.md`](DEVELOPMENT.md).

---

## 1. One sentence

Pastex is a **paste box** that lives on the right side of Chrome: short texts
you use often (greetings, addresses, replies) are stored in **your own Google
Drive**, and one click puts them into the field you are filling in on a
specific website.

## 2. Four "rooms" to imagine

Think of Pastex as a small office with four rooms:

| Room | Contains | Visible to the user? |
| --- | --- | --- |
| **Shop window** (side panel) | The list of notes, the add/edit/delete/search buttons | Yes — this is what gets clicked |
| **Button on the page** (⚡) | Appears next to the field being filled, to open the panel | Yes, only on allowed sites |
| **Back office** (background worker) | Saves changes to Google Drive once you have left the site | No |
| **Warehouse** (Google Drive) | The actual note files | Yes, through Drive as usual |

Everything in the shop window is only a **fast copy** of the warehouse. When
the copy and the warehouse disagree, the warehouse is right.

## 3. The journey of one note (the easiest part to grasp)

**When adding a note on a website:**

```
The user presses "＋ Text" and saves
        │
        ▼
The panel shows the new row at once             ← instant feedback
        │
        ▼
The change is written to an "order book" (queue) ← not sent to Drive yet
        │
        ▼
The user closes that site's tab
        │
        ▼
The back office sends the orders to Google Drive one by one
        │
        ▼
Afterwards it re-reads the warehouse so the real Drive ids replace the
temporary ones
```

Why wait? So that **nothing waits for the network** while the user is working.
The same principle applies to every edit:

| What the user does | Visible immediately | Sent to Drive |
| --- | --- | --- |
| Add a note or folder | the new row appears | one upload |
| Edit the body or rename | the new name/body | one write |
| Delete | the row disappears | moved to Trash (batched) |
| Drag/reorder | the new order | **one** write (the order label) |
| Fold/unfold a folder | the folder folds | **one** write (the same label) |

If the browser is closed before the orders are sent, **the order book is not
lost** — it is stored and sent on the next start.

## 4. When Pastex looks at Drive

There are three triggers, no more:

1. **The first time after picking the configuration file** — once, reading the whole
   warehouse.
2. **When the machine is idle** (no typing or mouse movement for about a
   minute, or the screen is locked) — it checks whether anything changed. If
   nothing changed, only the freshness stamp moves. The interval between checks
   grows (2 → 4 → … → 30 minutes) while nothing changes, and returns to the fast
   value as soon as a change is seen.
3. **The ↻ Sync button** — the only button that deliberately sends orders.

Pastex **never** downloads while the user is typing or has the panel open. That
is why the panel feels instant.

## 5. Configuration: one file of links

The user presses **Config** once and picks **one file**, of any name — for
example `pastex-storage.json`, the name used throughout this document:

| Key | Contents | Analogy |
| --- | --- | --- |
| `pastex-folder` | the shared link of the Drive library folder | **warehouse address** |
| `pastex-index` | the shared link of the order/collapse file | **the map on the wall** |
| `pastex-sites` | the site list, inline or as a link | **guest list** |
| `client_id` | the public OAuth client id of this installation | **who you are** |

The picked file is identified by these keys, never by its name: any filename is
accepted, and a missing key is reported by key name. No folder is scanned for
the file. Each
link is reduced to the id the API needs, so the value can be whatever Drive's
Share button produced (a bare id, a `/folders/<id>` link, a `/file/d/<id>/view`
link or an `?id=<id>` link). Because the index is named, reading the order costs
one GET instead of a listing.
After the file is picked, its parsed contents are **copied into Pastex's
memory**, so the file is never opened again (not even after the browser
restarts). When something is wrong, Pastex reports **every** problem at once and
the previous configuration stays in use.

**The file holds no secret.** A client id is public by design; the credential
lives with Google and Chrome. Nothing in the file is a key, so a leaked
configuration file grants nobody access to Drive.

## 6. The guest list (allowed sites)

Pastex is installed with broad permissions but is **only active** on the sites
listed in `pastex-sites.json`. Elsewhere the ⚡ button does not appear and insert
requests are refused.

The ⚡ button also uses a **30-second ticket** bound to that page. A forged or
expired ticket is refused, so a hostile page cannot make Pastex open the panel
or write to Drive.

## 7. File map: "to change X, open Y"

This is the most useful part when asking for a fix.

| To change… | Open | Note |
| --- | --- | --- |
| The panel's appearance (colours, buttons, layout) | `extension/sidepanel.css`, `extension/sidepanel.html` | Colours are checked numerically in `extension/test/appearance.test.js` |
| Panel behaviour (list, editor, search, selection) | `extension/sidepanel.js` | One file, about 800 lines |
| The ⚡ button on web pages | `extension/content.js` (+ `content.css`) | Only inserts text and asks for permission |
| The back office (queue, schedule, message rules) | `extension/background.js` | Every write to Drive goes through here |
| How it talks to Google Drive | `extension/drive.js` | Permission, upload, rename, Trash |
| Folder layout and order | `extension/library.js` | Order and fold state live in one file, `pastex-index.json` |
| The fast copy (cache) | `extension/cache.js` | The panel is always served from here |
| The order book | `extension/queue.js` | Survives a browser restart |
| The three-file configuration | `extension/config-source.js` (panel) and `extension/config-cache-reader.js` (worker) | Both read the same cache |
| The site list | `extension/sites.js` | Strict rules, fails closed |
| The Linux package | `debian/`, `scripts/`, `.github/workflows/build-deb.yml` | Released through APT, not the Chrome Web Store |
| The `.deb` contents (which file goes where) | `scripts/build-deb.sh` | The only builder CI uses |
| Package quality checks before a release | `tests/test_chrome_extension.py` (the "built .deb" section) + the *Lint the package* step in the workflow | Builds the package and inspects it |
| The APT archive "drawer" name | `packaging/pastex.sources` (`Suites: stable`) | The same value sits in three scripts plus the workflow; a test forces them to agree |
| User help text | `README.md`, `extension/README.md`, `running-on-linux-mint.md` | |

One rule matters above all: **only the back office may write to Drive.** The
panel sends requests, it does not write itself. If a change "does not save",
look at the queue in `queue.js` and the sending in `background.js` — not at
`sidepanel.js`.

## 8. Tests: what is guarded automatically

There are two gatekeepers before a release:

| Command | Guards |
| --- | --- |
| `node extension/test/run.js` | 173 behaviour checks: queue, cache, Drive, panel, colour contrast, message security, and **no dead files** |
| `python3 -m pytest tests/ -q` | 83 package checks: manifest version = changelog, `.deb` contents, APT signing, the development policy, and that the docs do not promise removed behaviour |

The "no dead files" guard (`extension/test/dead-code.test.js`) checks that every
shipped `.js` file is actually loaded by the panel, the manifest or the worker;
that the panel uses nothing undefined; and that per-item order writers never
come back.

The development-policy guard (`tests/test_development_policy.py`) checks that
shipped code keeps no compatibility path, that every stored shape carries a
version, and that an unknown version is rebuilt rather than guessed at.

## 8b. What a user can check after installing

| Command | Meaning |
| --- | --- |
| `dpkg -V pastex` | Compares the files on disk with the fingerprints inside the package. **No output = everything intact.** |
| `apt changelog pastex` | Shows the version history shipped inside the package. |
| `dpkg-deb -f … Installed-Size` | How much space apt reserved before installing. |
| rebuilding from the same tag | Produces a **byte-identical** `.deb`, so the published `checksums.txt` can be verified independently. |

## 8c. The "still in development" rule, in practice

This version has no installed base yet, so the project deliberately keeps **no
second way** of doing the same thing. What that means for a user:

| What changes | Effect on you |
| --- | --- |
| Order and folded folders | May return to Drive's order with folders open. Arrange them again once. |
| The snippet files in Google Drive | **Never** touched. Always intact, readable by any other app. |
| Configuration file name | Yours to choose: any `.json` name works; the four keys are what matter. |
| Browser requirement | Chrome/Chromium 142 or newer. |

If the panel looks odd after an update during development, run
`./scripts/reset-pastex-state.sh --apply` (it clears only the local cache; Drive
is not touched), then open the panel again. The full rule is in
`DEVELOPMENT.md`.

## 9. Mistakes that happen easily (and their symptoms)

| Symptom the user sees | Likely cause | Where to look |
| --- | --- | --- |
| "Drive: not connected." | the configuration file was not picked, or a key is missing | Config → the message names the key |
| The list appears but note bodies are empty | the copy shrank because storage was full; rows are filled while idle | `cache.js` (staged degradation) |
| Changes are not on another computer yet | orders are waiting because a site tab is still open | the Drive bar shows how many are waiting; `↻ Sync` |
| ⚡ does not appear | the site is not in `pastex-sites` | `sites.js`, then pick the file again |
| The panel is old after a package update | Chrome reads extension registration at start-up | quit Chrome completely, compare versions |
| `apt update`: *"…mint Release … does not have a Release file"* | an old machine still points at the retired `mint` suite | change `Suites: mint` to `Suites: stable` in `/etc/apt/sources.list.d/pastex.sources`, then `apt update` |

## 10. What was removed and must not come back

Several old layers were deliberately dropped so that there is never a second
way to do the same thing:

- **Folder-handle storage (IndexedDB)** and the **folder scanner** that searched
  a directory for the needed files — the panel now reads one picked file once
  and caches the parsed object.
- **Per-item order/fold writers in Drive** — order and fold state are written to
  one file, `pastex-index.json`. Older per-item labels are ignored, so a library
  from a previous version shows up in Drive's order.
- **Folder properties as the index** — Drive allows 124 bytes per property, so
  the index never fitted there and every write was refused.
- **Per-item labels as a fallback reader** — the index file is the only source;
  an unknown version is discarded and rebuilt.
- **Support for Chrome before 142** — the panel toggle, `sidePanel.close` and
  the `sidePanel.onOpened`/`onClosed` events are assumed, so no capability
  branch exists.
- **A second copy of the panel state in the page** — the worker owns whether the
  panel is showing, because a closed panel runs no code. The page asks for the
  answer instead of remembering the click it made.
- Helper exports nothing calls (the full list is in the 2.16.0 changelog entry).

If one of these features is needed again, add **one** new path with tests —
do not restore two at the same time.
