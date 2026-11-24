#!/usr/bin/env bash
# Inspect the order/collapse index Pastex stores in a library folder.
#
# Usage:
#   ./scripts/get-pastex-label.sh                     # label on the library root folder
#   ./scripts/get-pastex-label.sh <FILE_ID>           # label on any file or folder
#   ./scripts/get-pastex-label.sh --help
#
# Requires: curl, jq, openssl (all installed by default on Linux Mint).
# The service-account key is read from its JSON file; it is never copied
# elsewhere and never printed.

set -euo pipefail

KEY_FILE=""
FILE_ID=""
SCOPE="https://www.googleapis.com/auth/drive.readonly"

usage() {
  cat <<'USAGE'
Usage: get-pastex-label.sh [options] [FOLDER_ID]

  FOLDER_ID            Google Drive folder id (default: the Pastex root folder)
  -k, --key FILE       service-account.json (default: ./service-account.json)
  -h, --help           show this help

Examples:
  ./scripts/get-pastex-label.sh -k ~/keys/service-account.json
  ./scripts/get-pastex-label.sh -k ~/keys/service-account.json 17Xk...folderId

Output: the library folder, then the decoded pastex-index.json it contains.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -k|--key) KEY_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) FILE_ID="$1"; shift ;;
  esac
done

for tool in curl jq openssl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Required tool '$tool' is missing. Install it: sudo apt install $tool" >&2; exit 1; }
done

if [ -z "$KEY_FILE" ]; then
  for candidate in service-account.json ./service-account.json "$HOME/service-account.json"; do
    if [ -f "$candidate" ]; then KEY_FILE="$candidate"; break; fi
  done
fi
[ -n "$KEY_FILE" ] && [ -f "$KEY_FILE" ] || {
  echo "No key file found. Point at one with: -k /path/service-account.json" >&2; exit 1; }

# Without an id, the root folder is read from pastex-google-drive.json nearby.
if [ -z "$FILE_ID" ]; then
  for candidate in pastex-google-drive.json ./pastex-google-drive.json; do
    [ -f "$candidate" ] || continue
    FILE_ID="$(jq -r '.folder_id // empty' "$candidate" 2>/dev/null || true)"
    if [ -z "$FILE_ID" ]; then
      url="$(jq -r '.folder_url // empty' "$candidate" 2>/dev/null || true)"
      FILE_ID="$(printf '%s' "$url" | sed -n 's#.*/folders/\([A-Za-z0-9_-]\{10,\}\).*#\1#p')"
    fi
    [ -n "$FILE_ID" ] && break
  done
fi
[ -n "$FILE_ID" ] || {
  echo "No folder id was given and none was found in pastex-google-drive.json." >&2
  echo "Use: $0 -k key.json <FOLDER_ID>" >&2; exit 1; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# --- JWT signature (RS256) with openssl -----------------------------------
b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

header="$(printf '%s' '{"alg":"RS256","typ":"JWT"}' | b64url)"
now="$(date +%s)"
claim="$(jq -cn --arg scope "$SCOPE" --arg aud "https://oauth2.googleapis.com/token" \
  --argjson iat "$((now - 30))" --argjson exp "$((now + 3600))" \
  --arg sub "$(jq -r '.client_email' "$KEY_FILE")" \
  '{iss: $sub, scope: $scope, aud: $aud, iat: $iat, exp: $exp}' | b64url)"
signing_input="$header.$claim"

jq -r '.private_key' "$KEY_FILE" > "$tmpdir/key.pem"
chmod 600 "$tmpdir/key.pem"
printf '%s' "$signing_input" | openssl dgst -sha256 -sign "$tmpdir/key.pem" | b64url > "$tmpdir/sig"
assertion="$signing_input.$(cat "$tmpdir/sig")"

token="$(curl --fail --silent --show-error \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer' \
  --data-urlencode "assertion=$assertion" \
  https://oauth2.googleapis.com/token | jq -r '.access_token // empty')"
[ -n "$token" ] || { echo "Could not obtain an access token. Check the key and the Drive API." >&2; exit 1; }

# --- read the folder and find its index file -------------------------------
# The index is a plain file in the library folder, not a folder property: a
# Drive property is limited to 124 bytes including its key, which the index
# always exceeds.
folder="$(curl --fail --silent --show-error -H "Authorization: Bearer $token" \
  "https://www.googleapis.com/drive/v3/files/$(printf '%s' "$FILE_ID" | jq -sRr @uri)?fields=id,name,mimeType")" || {
  echo "The request failed. Make sure the folder is shared with: $(jq -r '.client_email' "$KEY_FILE")" >&2; exit 1; }

echo "Folder   : $(printf '%s' "$folder" | jq -r '.name // "?"')  ($(printf '%s' "$folder" | jq -r '.id'))"

listing="$(curl --fail --silent --show-error -H "Authorization: Bearer $token" \
  --get --data-urlencode "q='$(printf '%s' "$FILE_ID")' in parents and trashed = false" \
  --data-urlencode 'fields=files(id,name)' \
  https://www.googleapis.com/drive/v3/files)" || {
  echo "Could not list the folder." >&2; exit 1; }

index_id="$(printf '%s' "$listing" | jq -r '.files[] | select(.name == "pastex-index.json") | .id' | head -n 1)"
echo
if [ -z "$index_id" ]; then
  echo "This folder has no pastex-index.json."
  echo "That means the library is shown in Drive's own order with every folder"
  echo "open; Pastex writes the file on the next reorder or fold."
else
  index="$(curl --fail --silent --show-error -H "Authorization: Bearer $token" \
    "https://www.googleapis.com/drive/v3/files/$index_id?alt=media")" || {
    echo "Could not read pastex-index.json ($index_id)." >&2; exit 1; }
  echo "pastex-index.json (decoded):"
  printf '%s' "$index" | jq . 2>/dev/null || printf '%s\n' "$index"
fi
