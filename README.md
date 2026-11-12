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

After the GitHub Pages APT repository has been published, add it to your APT sources and install Pastex:

```bash
curl -fsSL https://patrickbuhagiar24.github.io/paste/apt/pastex.gpg | sudo gpg --dearmor -o /usr/share/keyrings/pastex.gpg
```

```bash
echo 'deb [signed-by=/usr/share/keyrings/pastex.gpg] https://patrickbuhagiar24.github.io/paste/apt/ ./' | sudo tee /etc/apt/sources.list.d/pastex.list
```

```bash
sudo apt update
```

```bash
sudo apt install pastex
```

This setup uses the repository's GPG key to verify signed APT metadata. The GitHub Pages repository must have been published with GPG signing enabled for these commands to work.

## Use

1. Launch Pastex and choose **Choose Source Folder**.
2. Click a text file once to paste its contents into the app that was active before Pastex.
3. Right-click a text file or folder to select it, edit it, delete it, or move it up or down within its folder. Editing a folder changes its name.
4. Click the displayed source-folder name to open that location in the file manager.

Press `Enter` to paste the selected text file, `F5` to refresh, `F2` to edit the selected item, and `Delete` to remove it. Folders are not pasted.

## Features

- Nested folders and `.txt` snippets.
- One-click paste with hover highlighting and a live preview.
- Search by file name or contents.
- Create folders and text files; edit snippets or rename folders.
- Select and reorder text files and folders from the context menu.
- Choose one source folder; new and edited snippets are saved there.
- Optional always-on-top setting and persistent folder expansion state.
- Clipboard tools: `wl-copy`, `xclip`, or `xsel`; paste simulation: `ydotool` or `xdotool`.

## Clipboard and permissions

Pastex uses `wl-clipboard` on Wayland where available, and `xclip` or `xsel` on X11. Automatic paste on Wayland uses `ydotool` and `/dev/uinput`; X11 can use `xdotool`.

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
