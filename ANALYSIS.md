# Pastex — colour work and project analysis

This document accompanies the UI changes. Part 1 explains the visual work;
part 2 is the project analysis and the fixes I found but deliberately did
**not** change, so that each change stays focused.

Preview images of the new design are in `.arena/preview/panel.png` (light and
dark panel) and `.arena/preview/editor.png` (editor dialog).

---

## 1. Colour work: list and buttons

### What was wrong with the old design

The old palette was so pale that the list and the buttons barely read as
clickable. The numbers below are WCAG contrast ratios computed from the old
values:

| Pair | Old ratio | Status |
| --- | --- | --- |
| Selected row `#d9e9fa` vs background `#fff` | **1.24:1** | almost invisible |
| Hovered row `#eef4fc` vs background `#fff` | **1.11:1** | almost invisible |
| Selected row vs hovered row | **1.12:1** | the two states could not be told apart |
| Button border `#c6d8ec` vs background `#fff` | **1.45:1** | the button edge disappeared (min. 3:1) |
| Row action icons (opacity 0.5) | **2.48:1** | hard to read |
| Status text ok/error | identical | no result colour |

Every button — the collapse-all icon, Select, Sync, Config, Text, Folder, Save,
Cancel, Delete — also used one single style, so there was **no hierarchy**: "Save" and
"Delete" looked the same.

### What changed

**The palette is checked with numbers, not with feelings.** Every text pair
reaches WCAG AA, most of them AAA:

| Pair | New ratio | Status |
| --- | --- | --- |
| Body text on background | 17.85:1 | AAA |
| Row label on idle / hover / selected row | 16.00 / 14.56 / 11.54:1 | AAA |
| Muted text (Drive status, editor labels) | 7.19–7.58:1 | AAA |
| White on the primary button (gradient top–bottom) | 6.71–11.07:1 | AAA |
| White on the danger button | 5.38–6.47:1 | AA |
| Secondary button border | 3.77–3.97:1 | ≥ 3:1 (non-text) |
| Focus indicator | 6.70:1 | clearly visible |

**The list now has three clearly different states:**

- idle row: soft surface `#edf2f9` with rounded corners, so the list reads as a
  series of items rather than free text;
- hover: `#d9e7fa` (1.20:1 away from idle — always distinguishable);
- selected: `#b8d2f5` (1.39:1 from idle, 1.26:1 from hover) **plus** a dark blue
  accent bar on the left edge (7.37:1) and a heavier label. The selected state
  therefore does not rely on colour alone.

**Buttons now carry meaning:**

- **Primary** (indigo gradient, white text): `＋ Text`, `＋ Folder`,
  `＋ New snippet`, `Save` — safe actions that are the next step;
- **Danger** (red gradient): `🗑 Delete` and `🗑 Delete` in the selection bar;
- **Secondary** (strong 3.9:1 border): the collapse-all icon, Select, Sync,
  Config, Cancel and the row actions (newline/edit/delete);
- disabled primary/danger buttons lose the strong fill and return to a plain
  outline, so an action that cannot be clicked does not shout louder than one
  that can;
- `Sync` becomes primary automatically while Drive is unavailable, because it
  is the only action that helps in that state.

**Highlights elsewhere:**

- **Status line** in the header is coloured by result (green ok, red error) and
  made heavier. It is the panel's only report: a permanent connection label and
  a "checked N min ago" line were removed, because two more truncated labels
  pushed the one message that matters out of view, and a long Drive error now
  wraps instead of ending in an ellipsis;
- **Selection count** becomes a filled red chip ("2 selected") when items are
  selected, so the bottom bar is immediately visible;
- **Row actions** (✎ Edit with its Rename / Edit text / Move menu, and 🗑)
  appear on hover, on the selected row **and on keyboard focus**
  (`:focus-within`) — previously they never appeared for keyboard users at all.
  The delete button turns red under the pointer.
- **Dark theme** follows the system preference (`prefers-color-scheme`), with
  its contrast also tested (e.g. selected text 7.34:1).
- **`prefers-contrast: more`** thickens borders and adds a black frame to the
  selected row; **`prefers-reduced-motion`** turns every transition off.

**Consistency:** the ⚡ launcher on the page (`content.js`) now uses the same
indigo as the panel (`assets/launcher-preview.html` was updated too), so the
product feels like one system.

### Testing

`extension/test/appearance.test.js` (new, 3 cases) computes contrast from the
real CSS and fails when a text pair drops below AA, a border drops below 3:1,
the selected row becomes too similar to idle/hover, or a CSS rule for a state
that `sidepanel.js` emits (ready/loading/failed, ok/error, selected) goes
missing. Colour regressions are therefore caught automatically.

| Check | Before | After |
| --- | --- | --- |
| `node extension/test/run.js` | 119 passing | **122 passing** |
| `python3 -m pytest tests/ -q` | 68 passing | **68 passing** |

Version bumped to **2.11.0** (manifest + `debian/changelog`) to satisfy the
workflow's "Check version alignment" rule.

---

## 1b. Second round: cursor, icon buttons, and a complete cache

### Cursor highlights (list and buttons)

| State | Before | After |
| --- | --- | --- |
| Hovered vs idle row | 1.11:1 | **1.18:1** + an 8.58:1 accent edge |
| Selected vs hovered row | 1.12:1 | **1.28:1** |
| Button focus ring | 2px `#1d4ed8` | **3px + gap**; primary/danger use `--focus-strong` (12.79:1 against the surface) |
| Nesting depth | indentation only | **guide lines** left of a folder's children |
| Row label under the pointer | — | **subtle underline** as a "this is clickable" cue |

The same numbers are kept in the dark theme (hover vs idle 1.30:1, selected vs
hover 1.41:1, accent 9.95:1). Row action icons now use
`--border-strong` (not the pale `--border`) and the delete button turns red only
under the pointer. Disabled buttons are fully greyed (`grayscale`) so an emoji
never looks active.

### The ⚡ button on the page: it opens **and** closes

`sidePanel.close()` only exists from **Chrome 141**, so at the time closing had
to be detected rather than assumed.

- The worker sent `toggle: true/false` with the capability; `content.js` used
  **⚡** (open) or **×** (close) accordingly.
- The panel announced its own state (`pastex-panel-state`), so the icon knew
  when to change without guessing.
- Closing used the **same capability** as opening (a 30-second token bound to
  the tab URL plus the allowlist revision); a forged or expired token was
  refused, and a page outside the allowlist could not close the panel.

### Cache: the whole folder list and every body, no sync in front of a click

The causes of the slowness I found:

1. **Collapsed folders were never cached.** The cache sent
   `deferCollapsed: true`, so the contents of a folded folder were not
   downloaded until it was opened — opening a "cold" folder meant waiting for
   Drive.
2. **No quota limit.** `storage.local` is bounded (about 10 MB without
   `unlimitedStorage`), and a failed write was swallowed (`catch {}`). For a
   large library the local cache could vanish without a trace.
3. **The background check always did a full listing** even when nothing had
   changed.

The fixes:

- **Complete snapshot**: the cache asks for `deferCollapsed: false`, so texts
  inside folded folders are stored too. The panel opens everything from the
  cache without calling Drive on the click path.
- **Staged degradation when the quota is tight**: (1) every stored library
  with this one complete, (2) this library alone complete, (3) this library
  alone compacted (each body kept once), (4) nothing — the worker's session
  memory. A library that is on screen keeps its bodies before another
  account's snapshot keeps its place, and a complete snapshot is **never**
  replaced by a thinner one while it still fits.
- **Reporting and filling**: the cache result reports `pending` (rows whose
  body is not stored yet). The panel still shows the list at once, then the
  worker fills those rows **without listing Drive** (`library.fillMissing`),
  stores the snapshot again, and the panel repaints once.
- **Metadata-only `verify()`**: the background check compares folder/item
  signatures and the index label without downloading a body; only a real
  difference falls back to the full listing. An unchanged library is therefore
  much cheaper, and the panel never "waits for a sync" because it always paints
  from the cache first.

What deliberately **did not** change: Drive remains the source of truth; writes
(create/edit/delete/reorder) still go to Drive and the cache is invalidated
before and after; the offline queue only arrived in the third round. So "put everything
in the cache" means **all data is available from the cache for reading and
opening**, not that Drive stops being the source of truth.

### Second round test summary

| Suite | Result |
| --- | --- |
| `node extension/test/run.js` | **133 passing** (from 122) |
| `pytest tests/ -q` | **68 passing** |

New tests covered: the snapshot keeps bodies from folded folders, opening from
cache without download or listing, `verify()` without bodies, reporting and
filling missing rows, a tight quota not destroying a useful snapshot, the close
capability (and its refusals), and the Close button appearing only when
supported.

---

## 1c. Third round: read from cache, write when the tab is left

### The toolbar icon becomes a real toggle

I checked the **yousub** repository (`patrickbuhagiar24/youtube`). Its icon
button is **not** a manual `action.onClicked`; it is one line in the service
worker:

```js
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
```

With that, **Chrome itself** handles the click: open the panel on the first
click, close it on the next. Pastex used `chrome.action.onClicked`, which can
only open. The changes:

- `action.onClicked` was removed; `setPanelBehavior` is installed when the
  worker starts.
- No listener competes for the click, so the toggle behaviour stays Chrome's.
- This also removed the need for a **Close** button in the panel.

### Synchronisation: once when the configuration loads, then the cache

| Event | Before | After |
| --- | --- | --- |
| Open the panel | could list Drive when the snapshot was stale | **always from cache**, zero requests |
| **↻ Sync** button | full listing | **send the queue only, without reading Drive** (held while a site is open) |
| A new configuration loads | the next listing | **one full sync**, then the cache |
| Background check | metadata listing while idle | unchanged, and the only path to Drive while idle |
| Browser start | 3-second cache warm-up | **zero requests** when the queue is empty; otherwise the queue is sent |
| Edit on a site | written straight to Drive | **written to the cache + queue**, flushed when the tab is **closed** (not merely hidden) |

### A write queue that survives a restart

The queue lives in `chrome.storage.local` (`extension/queue.js`), so:

- a browser closed before the flush **does not lose edits**: the queue is sent
  on the next start;
- one edit is one entry. A new row carries a local id (`local-…`) until the
  upload lands; after the flush one full read replaces the local ids with
  Drive ids;
- the index (order + collapse state) is queued as **one** `order` entry and
  written in a single request, not one per row;
- a transient failure (offline, quota) keeps the entry; a permanent failure
  (HTTP 400/403/404/410, "already exists", "not found") drops it from the queue
  **and** from the cache, so it is not retried forever and no row is shown that
  Drive does not have;
- the Drive bar shows how many changes are still waiting.

Honest note: because Drive stays the source of truth, a flush ends with one
full read. So "send everything when the tab closes" really happens, but it
costs one full listing per flush (not per edit). New rows keep their `local-`
id until that read; **↻ Sync** therefore deliberately does **not** read, and
real Drive ids arrive the next time the panel opens.

### Third round test summary

| Suite | Result |
| --- | --- |
| `node extension/test/run.js` | **149 passing** (from 133) |
| `pytest tests/ -q` | **68 passing** |

The new tests covered local edit behaviour (create/edit/rename/reorder/collapse/
delete), local ids and remapping, a queue that survives a worker restart,
`indexOf` skipping rows Drive does not have yet, the toolbar toggle through
`setPanelBehavior`, a create on a site that does not touch Drive, a flush that
writes to Drive and then gives rows their Drive id, and the refusal of
`flush-now` from a page that is not an allowed site or not the main frame.

---

## 2. Project analysis and suggested improvements

### What is already good (and should stay)

1. **A data-frugal architecture.** Snippet bodies are cached by the SHA-256 of
   their content (`hash.js`, `library.js`, `cache.js`), so an unchanged file is
   never downloaded again; order and collapse state live in one
   `pastex-index.json` file in the library folder, so a reorder is one Drive
   write instead of one per item. This is a strong design decision.
2. **Fail-closed security.** The site allowlist refuses an empty or broken
   configuration; `content.js` verifies `sender.id`; the panel uses a
   30-second capability token bound to the tab URL; MV3 CSP without remote
   code; workflow actions pinned to commit SHAs. The security documentation
   (`SECURITY.md`, the OAuth trade-off) is honest and does not sell safety it
   does not have.
3. **Test discipline.** Over 250 checks across JS and Python, a dependency-free
   JS runner, and many assertions that lock down *behaviour* (e.g. "a failed
   insertion must not silently fall back to the clipboard"), not only syntax.
4. **Deep documentation.** The README explains *why*, not only how to use it.

### Suggested improvements (not yet applied)

**P1 — Technical / real risk**

1. **~~The service-account key can be exposed through links.~~** Resolved: the
   service account is gone. Pastex now uses the **OAuth user flow with the
   full `drive` scope** — the one scope Google publishes for complete access to
   the account's Drive, with the library folder in the configuration deciding
   what Pastex actually touches. No key is distributed, and the public client id
   is worthless on its own. The remaining property to keep is that a cached
   snapshot never carries the client id. Note the consequence: the scope is
   restricted, so publishing the app beyond test users would need Google's
   security assessment.
2. **`host_permissions` is too broad** (`http://*/*`, `https://*/*`) purely to
   support dynamic script registration. It could be narrowed with
   `optional_host_permissions` requested per site when the user picks the
   allowlist, so unused permissions are not held forever.
3. **`innerHTML` in `sidepanel.js`** (the "No snippets match…" message). The
   data is a constant, so it is safe today, but it should become
   `textContent`/DOM nodes so it never becomes a bad example to copy.

**P2 — Accessibility (continuing the colour work)**

4. **The tree does not announce its state to screen readers**: rows have no
   `aria-level`/`aria-expanded` for folders, and `#tree` does not use
   `aria-activedescendant`. Selection also moves the visual focus (the CSS
   `.selected` class) but not the DOM focus, so Tab takes many steps.
   Suggestion: add `aria-level={depth+1}`, `aria-expanded` on folders, and
   ↑/↓ treeview navigation.
5. **The editor dialog does not trap focus.** `role="dialog"` and `aria-modal`
   are already there, but focus is neither trapped nor returned to the trigger
   on close; Escape is deliberately unused (a test forbids it for the picker),
   yet for an editor dialog Escape is what users expect.
6. **The browser's built-in `confirm()`** is used for delete. Functional, but it
   cannot be styled and in some configurations blocks rendering. An in-panel
   confirmation dialog would be more consistent and easier to test.

**P3 — Code quality / maintenance**

7. **The new CSS has no automated visual test.** Contrast is checked
   numerically, but not the actual rendering (layout, long-text truncation).
   Playwright screenshot tests in CI would close that gap.
8. **`setStatus` now knows the "tone" of a result.** Status logic is still
   spread over many places; if it grows again, consider one helper
   `report(kind, message)` so no call site forgets the tone.
9. ~~**`config-reader.js` was only 15 lines** — the offscreen + handle-store +
   config-source layering meant three paths that had to stay consistent.~~
   **Fixed in 2.16.0:** `handle-store.js` and `config-import.js` were deleted,
   `config-source.js` is down to one path
   (`create(sites, cacheStore)` → `chooseFile`/`readConfig`/`readSites`), and
   `extension/test/dead-code.test.js` refuses a file nothing loads. See
   [ARCHITECTURE.md](ARCHITECTURE.md) for the plain-language summary.
10. **`debian/changelog` bumps the version on every UI change** — good for
    releases, but consider an "Unreleased" entry in a separate `CHANGELOG.md` so
    small commits do not force a version bump.

**Operational notes**

11. Running `./scripts/build-deb.sh` needs `dpkg-deb`/`openssl` on the build
    machine. That is verified in CI; locally the tests still cover the JS units
    and the Python packaging checks (CRX packing is skipped when `openssl` is
    absent).
12. CI runs `node extension/test/run.js` and `pytest`; adding a test file to
    `run.js` is enough for it to be included, so no CI change is needed per new
    suite.

---

## Summary of changed files (UI rounds)

| File | Change |
| --- | --- |
| `extension/sidepanel.css` | rewritten: theme variables, primary/danger/secondary buttons, idle/hover/selected rows, selection chip, dark theme, prefers-contrast, reduced-motion |
| `extension/sidepanel.html` | `primary`/`danger` classes on the right buttons |
| `extension/sidepanel.js` | `setStatus(text, tone)`, row `data-kind`, Drive state (the dot), selection chip, delete coloured on hover, primary Sync when Drive is down |
| `extension/content.js`, `assets/launcher-preview.html` | the ⚡ launcher aligned with the panel indigo |
| `extension/test/appearance.test.js` | **new** — contrast and state tests |
| `extension/test/run.js`, `extension/test/sidepanel.test.js` | register the new tests; the DOM stub learns `removeAttribute` |
| `extension/library.js` | `deferCollapsed`, the `signaturesOnly` path, `verifyTree`, `fillMissing` |
| `extension/cache.js` | whole-library cache, quota degradation, `pending`, `fill()`, `verify()` |
| `extension/background.js` | metadata-only background check, panel status relay, cache fill, open/close toggle |
| `extension/content.js` | ⚡/× icon (open/close toggle, same capability) |
| `extension/queue.js` | **new**: local edits, local ids and remapping, index, restart-proof queue |
| `extension/test/*` | complete-cache, quota, verify, toggle, queue and flush, appearance tests |
| `extension/README.md`, `debian/changelog`, `extension/manifest.json` | documentation, changelog entry, version |
