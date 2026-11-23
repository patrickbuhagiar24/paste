#!/usr/bin/env bash
# Reset Pastex's local extension storage. Drive is never touched.
#
# During development the stored shape changes often, and an old shape is
# discarded rather than migrated (see DEVELOPMENT.md). This script clears the
# local, disposable state so the next panel open rebuilds it from Drive.
#
# What is cleared (Chrome's extension storage for the Pastex extension only):
#   - the cached library snapshot and its freshness stamps
#   - the cached configuration folder (so Config must be picked again)
#   - the deferred write queue
#
# What is NOT touched:
#   - anything in Google Drive: the snippets are content, and content is out of
#     bounds for a reset (DEVELOPMENT.md)
#   - the extension itself, its settings, or any other extension's storage
#
# Usage:
#   ./scripts/reset-pastex-state.sh            # dry run: list the storage files
#   ./scripts/reset-pastex-state.sh --apply    # delete them
#
# Requires: nothing but a POSIX shell. Works on Linux (Chrome, Chromium,
# Chrome Beta and Chrome Dev channel profiles).
set -euo pipefail

APPLY=0
case "${1:-}" in
  --apply) APPLY=1 ;;
  ""|--dry-run) APPLY=0 ;;
  -h|--help)
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) echo "Unknown option: $1 (use --apply or --help)" >&2; exit 2 ;;
esac

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

# Chrome and Chromium keep extension storage under these profile roots. A
# profile directory is "Default", "Profile 1", "Profile 2", …
roots=(
  "$CONFIG_HOME/google-chrome"
  "$CONFIG_HOME/google-chrome-beta"
  "$CONFIG_HOME/google-chrome-unstable"
  "$CONFIG_HOME/chromium"
)

# The extension id is derived from the CRX signing key, so it is not hard-coded
# here. Any directory that holds extension storage and mentions Pastex counts.
found=0
for root in "${roots[@]}"; do
  [[ -d "$root" ]] || continue
  while IFS= read -r -d '' extension_dir; do
    found=$((found + 1))
    echo "extension storage: $extension_dir"
    if [[ $APPLY -eq 1 ]]; then
      # LevelDB keeps several files; remove the directory's contents, not the
      # directory, so the browser recreates storage cleanly.
      rm -rf -- "$extension_dir"/Local\ Storage/leveldb \
             "$extension_dir"/Session\ Storage \
             "$extension_dir"/IndexedDB 2>/dev/null || true
      echo "  cleared"
    fi
  done < <(find "$root" -maxdepth 6 -type d -name "Local Extension Settings" -prune -print0 2>/dev/null)
done

if [[ $found -eq 0 ]]; then
  echo "No extension storage found under $CONFIG_HOME."
  echo "Start the browser once with Pastex enabled, then run this again."
  exit 0
fi

echo
echo "Google Drive was not touched: snippets are content and are never reset."
if [[ $APPLY -eq 0 ]]; then
  echo "Dry run. Re-run with --apply to clear the storage listed above."
else
  echo "Done. Restart the browser; the next panel open rebuilds the cache from Drive."
fi
