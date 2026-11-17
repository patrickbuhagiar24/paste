# Pastex

Pastex is a desktop app for organizing and pasting text snippets from `.txt` files on **Linux Mint** (Cinnamon, MATE and Xfce; X11 by default, and the Cinnamon Wayland session). Arrange files in folders, hover to preview, and click a text file once to paste it into the previously active app.

## Install on Linux Mint through APT only

Linux Mint is the only supported target. The project ships a Debian package, a deb822 APT source, and a GitHub Pages publishing workflow that builds and signs the archive. The repository URL is:

```text
https://patrickbuhagiar24.github.io/paste/
```

Linux Mint uses the repository suite **`mint`**. The suite is a fixed label for this archive (it is not a Mint codename), so one architecture-independent package serves Cinnamon, MATE and Xfce and every Mint release.

**Availability:** these commands work after the maintainer completes [APT repository publishing](apt-repo/README.md), including enabling GitHub Pages under **Settings → Pages → Build and deployment → Source: GitHub Actions**. Once configured, pushes to `main` build and publish the signed repository automatically; the workflow can also be started manually. Adding packaging files to the source tree does not itself configure Pages or create a signing key.

Obtain the **full 40-character signing-key fingerprint** from a trusted maintainer channel independent of the repository website. Do not use the fingerprint downloaded from the same website as your only verification. The commands below require Bash, `curl`, and `gpg` (provided by `gnupg`). They download the key into a private temporary directory and ask for the trusted fingerprint before changing system files. If it is unavailable or does not match, stop and contact the maintainer.

```bash
(
  set -euo pipefail
  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' EXIT
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    -o "$tmpdir/archive-keyring.gpg" \
    https://patrickbuhagiar24.github.io/paste/archive-keyring.gpg
  fingerprint=$(gpg --batch --with-colons --show-keys "$tmpdir/archive-keyring.gpg" |
    awk -F: '$1 == "pub" { count++; want = 1; next }
      want && $1 == "fpr" { print $10; want = 0 }
      END { if (count != 1) exit 1 }')
  [[ "$fingerprint" =~ ^[A-F0-9]{40}$ ]] || { echo 'Invalid key fingerprint.' >&2; exit 1; }
  printf 'Downloaded key fingerprint: %s\n' "$fingerprint"
  read -r -p 'Enter the independently verified fingerprint: ' expected
  expected=${expected//[[:space:]]/}
  [[ "${expected^^}" == "$fingerprint" ]] || { echo 'Fingerprint mismatch; nothing installed.' >&2; exit 1; }
  cat > "$tmpdir/pastex.sources" <<'EOF'
Types: deb
URIs: https://patrickbuhagiar24.github.io/paste/
Suites: mint
Components: main
Architectures: all
Signed-By: /etc/apt/keyrings/pastex.gpg
EOF
  sudo install -d -m 0755 /etc/apt/keyrings
  sudo install -m 0644 "$tmpdir/archive-keyring.gpg" /etc/apt/keyrings/pastex.gpg
  sudo install -m 0644 "$tmpdir/pastex.sources" /etc/apt/sources.list.d/pastex.sources
  sudo apt update
  sudo apt install pastex
)
```

The local source explicitly scopes the verified key to this repository. No unsigned fallback, `Trusted: yes`, or `apt-key` is used. If a command fails, the subshell stops and removes its temporary directory. The keyring belongs in `/etc/apt/keyrings` because that is local administrator configuration; `/usr/share/keyrings` is for keyrings shipped by packages (dpkg overwrites it). APT reads keyrings as the unprivileged `_apt` user, so `ls -l /etc/apt/keyrings/pastex.gpg` must show `-rw-r--r--`.

That is the whole installation. `apt update` reports `Hit:… https://patrickbuhagiar24.github.io/paste mint InRelease`, which proves the metadata is signed and verified.

If you installed a 1.8.x package before, the archive suite was renamed from `stonking` to `mint`. A `pastex.sources` written by the older instructions still asks for `stonking`, so `apt update` reports `404` for that suite and `apt upgrade` sees nothing new. Re-run the block above, or change `Suites: stonking` to `Suites: mint` in `/etc/apt/sources.list.d/pastex.sources`; the keyring does not need to be installed again.

On Linux Mint, automatic paste needs only `xclip` (or `xsel`) and `xdotool`, which the package **recommends**; the Wayland tools (`wl-clipboard`, `ydotool` and `python3-dbus` for the portal) are only **suggested**, because a default X11 session never uses them. Nothing else has to be configured, and installing the package changes nothing on the system: the optional Wayland udev rule, the `pastex` group and the per-user `ydotoold` unit ship as inert templates under `/usr/lib/pastex` and are only installed by `pastex --enable-permissions`.

### Upgrade

```bash
sudo apt update
sudo apt upgrade
```

Settings under `~/.config/pastex` and your snippets are preserved.

### Remove

```bash
sudo apt remove pastex
```

User settings and snippets under `~/.config/pastex` and `~/.local/share/pastex` are left in place.

### Local package installation

If you have a built package instead of the published repository:

```bash
sudo apt install ./dist/pastex_1.9.0-1_all.deb
```

## Use

1. Launch Pastex and choose **Choose Source Folder**.
2. Click a text file once to paste its contents into the app that was active before Pastex.
3. Right-click a text file or folder to select it, edit it, delete it, or move it up or down within its folder. Editing a folder changes its name.
4. Click the displayed source-folder name to open that location in the file manager.

Press `Enter` to paste the selected text file, `F5` to refresh, `F2` to edit the selected item, and `Delete` to remove it. Folders are not pasted.

Pasting minimizes Pastex so its panel entry stays visible, and the two slow halves of the paste run in parallel: the window is minimized first (the window manager needs a few milliseconds to hand focus back) while the text is copied in a worker thread, and Ctrl+V is sent as soon as both are done — normally on the window manager's unmap signal rather than after a fixed delay. Clipboard and paste tools are resolved once at startup, and on Wayland `ydotoold` is started then too, so the first click is as fast as the rest. Pastex never unmaps its window, so the panel icon cannot disappear: if a window manager refuses to minimize, Pastex keeps the window visible, leaves the text on the clipboard, and asks you to press `Ctrl+V` instead of pasting for you. Because a compositor answers `iconify()` asynchronously, the state is re-checked every 20 ms for up to 900 ms — Cinnamon animates the minimize — with one repeated request for window managers that ignore the first. Tk/X11 silently drops `iconify()` for a maximized window, so Pastex unmaximizes before the paste and puts the zoom back when the window returns; if the state still says `normal` after 200 ms, the window manager is asked to minimize through xdotool (EWMH `--sync`), and from then on the deciding question is who holds the input focus: an iconified window cannot keep it, so `xdotool getactivewindow` is re-read every 120 ms for the rest of the budget (a frame that still belongs to Pastex is recognised by its `WM_CLASS`). That is what actually matters to `Ctrl+V` — Muffin on a real Cinnamon session kept reporting `wm state: normal` while the focus had already left — and `WM_STATE`/`xwininfo` are only the fallback when `xdotool` cannot answer. The `ready:` line of the log names the window manager (or `none`), the session type and the desktop, and `pastex --check` prints the same line, because without a window manager nothing can minimize Pastex and automatic paste is not available. Bringing the window back is checked the same way: `deiconify()` is dropped when it lands while that animation is still running, so the state is re-read every 60 ms for up to 1.2 s and the request is repeated until the window is really on screen. If the window still cannot be confirmed as hidden, Pastex restores it and leaves the text on the clipboard instead of staying minimized with nothing pasted. `restore_delay` in `~/.config/pastex/settings.json` (seconds, default `0.03`) only controls how long Pastex waits before its window comes back; lower it to return sooner, raise it if a target app loses the paste.
Use the `Up` and `Down` arrow keys to move through the list without pasting, and `Ctrl+Up` / `Ctrl+Down` to move the current item up or down inside its folder.
The darker highlight follows the row you last clicked, right-clicked, edited, or reached with the arrow keys; a right-click in Select mode leaves it unchanged.

### Snippet picker (`--pick`)

`pastex --pick` (also the **Paste a Snippet** desktop action) opens a frameless
list at the pointer. Type to filter, `Up`/`Down` to move, `Enter` to paste,
`Esc` to cancel. Because the picker is a popup with no panel entry, selecting a
snippet never minimizes or unmaps the main window: the application behind the
popup keeps the focus and receives `Ctrl+V`. Bind it to a key of your choice,
for example Super+V:

In Linux Mint, open **Menu → Preferences → Keyboard → Shortcuts → Custom
Shortcuts**, add an entry with the command `pastex --pick`, and assign `Super+V`
(or any free combination). The same shortcut works in MATE and Xfce; in Xfce it
lives under **Settings → Keyboard → Application Shortcuts**.

### One running instance

The first Pastex process takes a lock in `$XDG_RUNTIME_DIR/pastex/` and listens
on a socket there, so later launches (`pastex`, `pastex --pick`) hand the
request to it and exit immediately instead of starting a second Tk process
(~200-400 ms) or a second panel entry. `--dir` sessions stay separate on
purpose, because they use different folders.

### Faster copies (experimental, opt-in)

Every copy normally runs `wl-copy`, which forks, connects to the compositor and
daemonises: 5-20 ms on the click path. Pastex can instead own the Wayland
selection itself through the data-control protocol (`ext-data-control-v1`),
which turns a copy into a couple of marshalled messages. This only applies to a
Wayland session whose compositor implements that protocol; in the default X11
session `xclip` already runs without a compositor round trip:

```bash
pastex --wayland-clipboard      # opt in; remembered in settings.json
```

The owner runs in a **separate helper process**
(`python3 /usr/share/pastex/pastex/wlserver.py --serve`), so a compositor or
protocol problem cannot take the window down with it. Pastex
only starts using it after a self-test: it copies a nonce, reads it back with
`wl-paste`, and puts your previous clipboard back. If anything fails - no
data-control support, no read-back, the helper exiting - Pastex keeps using
`wl-copy` for the rest of the session, and every individual copy falls back too.
`--check` shows whether it is available, wanted, or off.

It is off by default because the protocol layer cannot be exercised in CI: it
needs a live compositor. Turn it on and check the log if you want the last few
milliseconds; nothing breaks if it does not work here.

### Live refresh

The snippet folders are watched with inotify, so files added, edited, renamed or
deleted by another program appear without pressing `F5`. The tree and file
contents are cached in memory: searches filter that cache instead of re-walking
the folders or re-reading every file on each keystroke. Repainting the tree
patches the rows that actually changed instead of clearing and rebuilding it, so
the scroll position, the expanded folders and the selection survive every
refresh. If inotify cannot be used at all (a low `fs.inotify.max_user_instances`, a
container, a non-Linux system) the folders are polled every 2 s instead, so the
tree still refreshes by itself, and the log names the reason; `F5` works too.

## Features

- Nested folders and `.txt` snippets.
- One-click paste with hover highlighting and a live preview.
- Search by file name or contents.
- Create folders and text files; edit snippets or rename folders.
- Select and reorder text files and folders from the context menu.
- Choose one source folder; new and edited snippets are saved there.
- Persistent folder expansion state and custom item order.
- Clipboard tools: `xclip` or `xsel` (X11, the Mint default) and `wl-copy` (Wayland); paste simulation: `xdotool` (X11) or `ydotool` (Wayland).
- Frameless snippet picker (`--pick`) that pastes without touching the main window.
- One running instance with instant re-launch, inotify-driven live refresh,
  and repaints that update rows in place.
- Rootless Ctrl+V fallback through the desktop portal when `ydotool` is unavailable.
- Optional (experimental) clipboard owner that copies without spawning a
  process, with a self-test and automatic fallback.

## Clipboard and permissions

Linux Mint starts an **X11** session by default, where Pastex copies with `xclip` (or `xsel`) and sends `Ctrl+V` with `xdotool`. Neither tool needs `/dev/uinput` access, a group membership, or a daemon, so on a default Mint install automatic paste works as soon as the recommended packages are installed. In the Cinnamon Wayland session, clipboard access uses `wl-clipboard` and automatic paste uses `ydotool` with `/dev/uinput`.

On first launch, Pastex checks for clipboard and paste tools and the current display session (X11 or Wayland) before you select a snippet. On X11 that is the whole check: `--check`, `--permissions` and the paste-failure dialog all report the `/dev/uinput` permissions, the `pastex` group and `ydotoold` as **not needed**, because `xdotool` types through XTEST. Only in a Wayland session does Pastex inspect `/dev/uinput` and the `ydotoold` service, and only there does it offer to request administrator authorization; missing packages are reported with install instructions. The check never writes a test value to the clipboard.

If paste does not work, inspect the environment:

```bash
pastex --check
pastex --permissions
pastex --enable-permissions
```

In a Wayland session, `pastex --enable-permissions` installs the udev rule from
its template, adds your account to the `pastex` group and enables the per-user
`ydotoold` unit; from an X11 session the same command prints that nothing is
needed and exits. If `ydotool` fails (for example the udev rule was never
installed on that session), Pastex
falls back to the desktop portal (`org.freedesktop.portal.RemoteDesktop`), which
injects `Ctrl+V` without root and without `/dev/uinput`; the desktop asks for
permission once per session. The portal needs `python3-dbus` (a suggested
package), and `pastex --check` reports whether it is available.

In a Wayland session Pastex starts `ydotoold` for you: it asks the user unit
first, and when that unit does not exist (`ydotoold.service` is a *system* unit,
which needs root) it launches `ydotoold` with a socket it owns in
`$XDG_RUNTIME_DIR` and points `ydotool` at that socket. Everything still depends
on `/dev/uinput` access, so run `pastex --enable-permissions` once.

A click never leaves the window minimized when `Ctrl+V` cannot be delivered:
the send stage has a 9 s deadline, each tool call has its own timeout, and when
the deadline passes the window comes back with an explanation instead of
waiting on a wedged daemon or an unanswered portal prompt. After a failed
paste, the next click only copies the text and keeps the window visible until
`pastex --check` reports the paste path as ready.

## Command-line options

```text
pastex [--dir FOLDER] [--list] [--copy FILE] [--paste FILE] [--pick]
       [--check] [--permissions] [--enable-permissions]
       [--test-paste] [--ctrl-v] [--version]
```

- `--dir FOLDER`: use a folder for this session; can be repeated.
- `--list`: list available text files.
- `--copy FILE`: copy a file to the clipboard.
- `--paste FILE`: copy a file and send `Ctrl+V`.
- `--check`: print environment and installation diagnostics.
- `--permissions`: report `/dev/uinput` permission status.
- `--enable-permissions`: request permission setup through `pkexec`.
- `--test-paste [--ctrl-v]`: run a paste diagnostic.
- `--pick`: show the snippet picker (reuses a running Pastex).
- `--wayland-clipboard`: own the Wayland selection from a helper process
  (experimental; falls back to `wl-copy`).

## Data locations

- Settings and error log: `~/.config/pastex/`
- Instance lock and socket: `$XDG_RUNTIME_DIR/pastex/` (cleared on logout)
- User snippets: `~/.local/share/pastex/snippets/`
- System-installed app files: `/usr/share/pastex/`

A chosen source folder is remembered between sessions. The `--dir` option only affects the current run and does not change that saved choice.

## Maintainer checks

The application is distributed to users through APT. To run the test suite on Linux Mint, install the test prerequisites with APT and run the tests from the checkout:

```bash
sudo apt install python3-pytest python3-tk xvfb xauth x11-utils
python3 -m pytest tests/ -q
```
