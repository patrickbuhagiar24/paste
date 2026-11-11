# Pastex

Pastex is a lightweight desktop app for browsing and pasting text snippets from `.txt` files on Ubuntu (GNOME Wayland and X11). Organize files in folders, hover over an item to highlight it, then click a text file once to paste its contents into the previously active app.

## Features

- Folder hierarchy displayed as categories and text snippets.
- One-click paste: Pastex copies the selected file, briefly hides its window, sends `Ctrl+V`, then reappears.
- Hover highlighting and a live text preview.
- Search by file name or contents.
- Create categories and snippets, edit or delete entries, and open the source folder.
- Choose one source folder; new and edited snippets are saved there.
- Optional always-on-top setting and persistent folder expansion state.
- Clipboard support through `wl-copy`, `xclip`, or `xsel`; paste simulation through `ydotool` or `xdotool`.

## Use

1. Launch Pastex and choose **Choose Source Folder**.
2. Move the pointer over a `.txt` entry to highlight it.
3. Click a text entry once. Pastex pastes it into the app that was active before Pastex.

Press `Enter` to paste the selected file, `F5` to refresh, `F2` to edit, and `Delete` to remove the selected item. Selecting a folder only navigates the tree; it does not paste.

## Install

### Build a Debian package

```bash
./scripts/build-deb.sh
sudo apt install ./dist/pastex_*_all.deb
```

The package installs the `pastex` command, desktop launcher, application icon, udev rule, and optional `ydotoold` integration.

### Run from a checkout

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
pastex
```

Alternatively, run `./bin/pastex` from the repository root. Python 3.10+ and `python3-tk` are required.

## Clipboard and permissions

Pastex uses `wl-clipboard` on Wayland where available, and `xclip` or `xsel` on X11. Automatic paste on Wayland uses `ydotool` and `/dev/uinput`; X11 can use `xdotool`.

The Debian package installs a narrowly scoped udev rule for `/dev/uinput` and configures the `pastex` system group as a fallback. Usually, no manual setup is needed. If paste does not work, inspect the environment:

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

## Build and tests

```bash
python3 -m pip install -e '.[test]'
pytest -q
./scripts/build-deb.sh
```

See [`apt-repo/README.md`](apt-repo/README.md) for the APT repository publishing guide.
