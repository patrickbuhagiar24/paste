# Pastex

Pastex is a desktop app for organizing and pasting text snippets from `.txt` files on Ubuntu (GNOME Wayland and X11). Arrange files in folders, hover to preview, and click a text file once to paste it into the previously active app.

## Install

Pastex is installed through APT on Ubuntu 26.10. Choose either the Launchpad PPA or the GitHub Pages APT repository. Each command is in a separate code block so GitHub shows an individual copy button for it.

### Launchpad PPA

```bash
sudo add-apt-repository ppa:unisys/pastex
```

```bash
sudo apt update
```

```bash
sudo apt install pastex
```

PPA uploads are started manually by a maintainer when requested; pushing ordinary source changes does not upload a package to the PPA. Launchpad must finish building and publishing the package before APT can install it.

### GitHub Pages APT repository

The published archive is a standard APT repository at `https://patrickbuhagiar24.github.io/paste/` with the suite **`stonking`** (the Ubuntu 26.10 codename), a signed `dists/stonking/InRelease`, and packages under `pool/main/p/pastex/`.

Verify [the signing-key fingerprint](https://patrickbuhagiar24.github.io/paste/archive-keyring.fingerprint.txt) against the fingerprint published by the maintainer before installing the key:

```bash
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://patrickbuhagiar24.github.io/paste/archive-keyring.fingerprint.txt
curl -fsSL --proto '=https' -o /tmp/pastex.gpg https://patrickbuhagiar24.github.io/paste/archive-keyring.gpg
gpg --show-keys --with-fingerprint /tmp/pastex.gpg
sudo install -m 0644 /tmp/pastex.gpg /etc/apt/keyrings/pastex.gpg
curl -fsSL -o /tmp/pastex.sources https://patrickbuhagiar24.github.io/paste/pastex.sources
sudo install -m 0644 /tmp/pastex.sources /etc/apt/sources.list.d/pastex.sources
sudo apt update
sudo apt install pastex
```

Both fingerprints must match. The keyring belongs in `/etc/apt/keyrings` because that is local administrator configuration; `/usr/share/keyrings` is for keyrings shipped by packages (dpkg overwrites it), and `/etc/apt/trusted.gpg.d` would trust this key for every repository. APT reads keyrings as the unprivileged `_apt` user, so `ls -l /etc/apt/keyrings/pastex.gpg` must show `-rw-r--r--`; a file only root can read makes `apt update` reject the repository. Downloading to `/tmp` first also keeps a failed `curl` from writing an empty keyring as root.

`apt update` then reports `Hit:… https://patrickbuhagiar24.github.io/paste stonking InRelease`, which proves the metadata is signed and verified. If the archive is published without a signing key, install `pastex-unsigned.sources` instead (it sets `Trusted: yes`).

Upgrading from the old flat `./` repository: delete `/etc/apt/sources.list.d/pastex.list` and the keyring it used, then follow the steps above.

```bash
sudo rm -f /etc/apt/sources.list.d/pastex.list /usr/share/keyrings/pastex.gpg
```

## Use

1. Launch Pastex and choose **Choose Source Folder**.
2. Click a text file once to paste its contents into the app that was active before Pastex.
3. Right-click a text file or folder to select it, edit it, delete it, or move it up or down within its folder. Editing a folder changes its name.
4. Click the displayed source-folder name to open that location in the file manager.

Press `Enter` to paste the selected text file, `F5` to refresh, `F2` to edit the selected item, and `Delete` to remove it. Folders are not pasted.

Pasting minimizes Pastex so the dock entry stays visible, and the two slow halves of the paste run in parallel: the window is minimized first (the window manager needs a few milliseconds to hand focus back) while the text is copied in a worker thread, and Ctrl+V is sent as soon as both are done — normally on the window manager's unmap signal rather than after a fixed delay. Clipboard and paste tools are resolved once at startup, and on Wayland `ydotoold` is started then too, so the first click is as fast as the rest. Pastex never unmaps its window, so the dock icon cannot disappear: if a window manager refuses to minimize, Pastex keeps the window visible, leaves the text on the clipboard, and asks you to press `Ctrl+V` instead of pasting for you. `restore_delay` in `~/.config/pastex/settings.json` (seconds, default `0.03`) only controls how long Pastex waits before its window comes back; lower it to return sooner, raise it if a target app loses the paste.
Use the `Up` and `Down` arrow keys to move through the list without pasting, and `Ctrl+Up` / `Ctrl+Down` to move the current item up or down inside its folder.
The darker highlight follows the row you last clicked, right-clicked, edited, or reached with the arrow keys; a right-click in Select mode leaves it unchanged.

### Snippet picker (`--pick`)

`pastex --pick` (also the **Paste a Snippet** desktop action) opens a frameless
list at the pointer. Type to filter, `Up`/`Down` to move, `Enter` to paste,
`Esc` to cancel. Because the picker is a popup with no dock entry, selecting a
snippet never minimizes or unmaps the main window: the application behind the
popup keeps the focus and receives `Ctrl+V`. Bind it to a key of your choice,
for example Super+V:

```bash
# GNOME: Settings -> Keyboard -> Custom Shortcuts, Command: pastex --pick
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings \
  "['/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/pastex/']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/pastex/ name 'Pastex picker'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/pastex/ binding '<Super>v'
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/pastex/ command 'pastex --pick'
```

### One running instance

The first Pastex process takes a lock in `$XDG_RUNTIME_DIR/pastex/` and listens
on a socket there, so later launches (`pastex`, `pastex --pick`) hand the
request to it and exit immediately instead of starting a second Tk process
(~200-400 ms) or a second dock entry. `--dir` sessions stay separate on
purpose, because they use different folders.

### Faster copies (experimental, opt-in)

Every copy normally runs `wl-copy`, which forks, connects to the compositor and
daemonises: 5-20 ms on the click path. Pastex can instead own the Wayland
selection itself through the data-control protocol
(`ext-data-control-v1`, GNOME 47+/mutter), which turns a copy into a couple of
marshalled messages:

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
refresh. If inotify is unavailable, `F5` still works as before.

## Features

- Nested folders and `.txt` snippets.
- One-click paste with hover highlighting and a live preview.
- Search by file name or contents.
- Create folders and text files; edit snippets or rename folders.
- Select and reorder text files and folders from the context menu.
- Choose one source folder; new and edited snippets are saved there.
- Persistent folder expansion state and custom item order.
- Clipboard tools: `wl-copy`, `xclip`, or `xsel`; paste simulation: `ydotool` or `xdotool`.
- Frameless snippet picker (`--pick`) that pastes without touching the main window.
- One running instance with instant re-launch, inotify-driven live refresh,
  and repaints that update rows in place.
- Rootless Ctrl+V fallback through the desktop portal when `ydotool` is unavailable.
- Optional (experimental) clipboard owner that copies without spawning a
  process, with a self-test and automatic fallback.

## Clipboard and permissions

Pastex uses `wl-clipboard` on Wayland where available, and `xclip` or `xsel` on X11. Automatic paste on Wayland uses `ydotool` and `/dev/uinput`; X11 can use `xdotool`.

On first launch, Pastex checks for clipboard and paste tools, the current display session, `/dev/uinput` access, and the `ydotoold` service before you select a snippet. The check does not write a test value to the clipboard. If permission setup is available, Pastex asks before requesting administrator authorization; missing packages are reported with install instructions.

If paste does not work, inspect the environment:

```bash
pastex --check
pastex --permissions
pastex --enable-permissions
```

If `ydotool` fails (for example the udev rule was never installed), Pastex
falls back to the desktop portal (`org.freedesktop.portal.RemoteDesktop`), which
injects `Ctrl+V` without root and without `/dev/uinput`; the desktop asks for
permission once per session. The portal needs `python3-dbus` (a recommended
package), and `pastex --check` reports whether it is available.

On Wayland, `ydotoold` may also be started for the current user with:

```bash
systemctl --user enable --now ydotoold
```

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

The application is distributed to users through APT. To run the test suite on Ubuntu, install the test prerequisites with APT and run the tests from the checkout:

```bash
sudo apt install python3-pytest python3-tk xvfb xauth x11-utils
python3 -m pytest tests/ -q
```
