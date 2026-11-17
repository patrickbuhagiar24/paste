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
```

```bash
curl -fsSL https://patrickbuhagiar24.github.io/paste/archive-keyring.gpg | sudo tee /etc/apt/keyrings/pastex.gpg > /dev/null
```

```bash
sudo chmod 0644 /etc/apt/keyrings/pastex.gpg
```

```bash
curl -fsSL https://patrickbuhagiar24.github.io/paste/pastex.sources | sudo tee /etc/apt/sources.list.d/pastex.sources > /dev/null
```

```bash
sudo apt update
```

```bash
sudo apt install pastex
```

`apt update` then reports `Hit:… https://patrickbuhagiar24.github.io/paste stonking InRelease`, which proves the metadata is signed and verified. If the archive is published without a signing key, install `pastex-unsigned.sources` instead (it sets `Trusted: yes`).

## Use

1. Launch Pastex and choose **Choose Source Folder**.
2. Click a text file once to paste its contents into the app that was active before Pastex.
3. Right-click a text file or folder to select it, edit it, delete it, or move it up or down within its folder. Editing a folder changes its name.
4. Click the displayed source-folder name to open that location in the file manager.

Press `Enter` to paste the selected text file, `F5` to refresh, `F2` to edit the selected item, and `Delete` to remove it. Folders are not pasted.

Pasting hides Pastex by minimizing it, so the dock entry stays visible, and Ctrl+V is sent as soon as the window manager reports the window hidden instead of after a fixed delay. Lower `restore_delay` in `~/.config/pastex/settings.json` (seconds, default `0.05`) to bring the window back sooner.
Use the `Up` and `Down` arrow keys to move through the list without pasting, and `Ctrl+Up` / `Ctrl+Down` to move the current item up or down inside its folder.
The darker highlight follows the row you last clicked, right-clicked, edited, or reached with the arrow keys; a right-click in Select mode leaves it unchanged.

## Features

- Nested folders and `.txt` snippets.
- One-click paste with hover highlighting and a live preview.
- Search by file name or contents.
- Create folders and text files; edit snippets or rename folders.
- Select and reorder text files and folders from the context menu.
- Choose one source folder; new and edited snippets are saved there.
- Persistent folder expansion state and custom item order.
- Clipboard tools: `wl-copy`, `xclip`, or `xsel`; paste simulation: `ydotool` or `xdotool`.

## Clipboard and permissions

Pastex uses `wl-clipboard` on Wayland where available, and `xclip` or `xsel` on X11. Automatic paste on Wayland uses `ydotool` and `/dev/uinput`; X11 can use `xdotool`.

On first launch, Pastex checks for clipboard and paste tools, the current display session, `/dev/uinput` access, and the `ydotoold` service before you select a snippet. The check does not write a test value to the clipboard. If permission setup is available, Pastex asks before requesting administrator authorization; missing packages are reported with install instructions.

If paste does not work, inspect the environment:

```bash
pastex --check
pastex --permissions
pastex --enable-permissions
```

On Wayland, `ydotoold` may also be started for the current user with:

```bash
systemctl --user enable --now ydotoold
```

## Command-line options

```text
pastex [--dir FOLDER] [--list] [--copy FILE] [--paste FILE]
       [--check] [--permissions] [--enable-permissions]
       [--export-data [FOLDER]] [--test-paste] [--ctrl-v] [--version]
```

- `--dir FOLDER`: use a folder for this session; can be repeated.
- `--list`: list available text files.
- `--copy FILE`: copy a file to the clipboard.
- `--paste FILE`: copy a file and send `Ctrl+V`.
- `--check`: print environment and installation diagnostics.
- `--permissions`: report `/dev/uinput` permission status.
- `--enable-permissions`: request permission setup through `pkexec`.
- `--export-data [FOLDER]`: create sample snippets; defaults to the user snippets folder.
- `--test-paste [--ctrl-v]`: run a paste diagnostic.

## Data locations

- Settings and error log: `~/.config/pastex/`
- User snippets: `~/.local/share/pastex/snippets/`
- System-installed app files: `/usr/share/pastex/`

A chosen source folder is remembered between sessions. The `--dir` option only affects the current run and does not change that saved choice.

## Maintainer checks

The application is distributed to users through APT. To run the test suite on Ubuntu, install the test prerequisites with APT and run the tests from the checkout:

```bash
sudo apt install python3-pytest python3-tk xvfb xauth x11-utils
python3 -m pytest tests/ -q
```
