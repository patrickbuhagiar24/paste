#!/usr/bin/env bash
# Find out which Drive write Pastex is not allowed to do, and why.
#
# Pastex writes two kinds of thing into your library folder:
#   - folders          (POST /files, JSON)
#   - text files       (POST /upload/drive/v3/files, multipart)
# and it keeps its own order/collapse index in a third one:
#   - pastex-index.json
#
# "A folder is created but a snippet is not" is a permission or quota problem
# on the Drive side, not a Pastex bug, and Drive's answer says which one. This
# script performs each write once, prints Drive's exact reply, and removes what
# it created. It never touches an existing snippet.
#
# Usage:
#   ./scripts/check-drive-write.sh                 # folder from pastex-google-drive.json
#   ./scripts/check-drive-write.sh -k ~/keys/service-account.json
#   ./scripts/check-drive-write.sh -k key.json <FOLDER_ID>
#
# Requires: curl, jq, openssl (installed by default on Linux Mint).

set -euo pipefail

KEY_FILE=""
FOLDER_ID=""
SCOPE="https://www.googleapis.com/auth/drive"

usage() {
  cat <<'USAGE'
Usage: check-drive-write.sh [options] [FOLDER_ID]

  FOLDER_ID           Google Drive folder id (default: the Pastex root folder)
  -k, --key FILE      service-account.json (default: ./service-account.json)
  -h, --help          show this help

Output: the exact result of listing the folder, creating a folder, uploading a
text file and writing pastex-index.json. Anything this script creates is removed
again at the end, and no existing file is modified.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -k|--key) KEY_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) FOLDER_ID="$1"; shift ;;
  esac
done

for tool in curl jq openssl; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Required tool '$tool' is missing. Install it: sudo apt install $tool" >&2; exit 1; }
done

if [ -z "$KEY_FILE" ]; then
  for candidate in service-account.json ./service-account.json "$HOME/service-account.json"; do
    if [ -f "$candidate" ]; then KEY_FILE="$candidate"; break; fi
  done
fi
[ -n "$KEY_FILE" ] && [ -f "$KEY_FILE" ] || {
  echo "No key file found. Point at one with: -k /path/service-account.json" >&2; exit 1; }

if [ -z "$FOLDER_ID" ]; then
  for candidate in pastex-google-drive.json ./pastex-google-drive.json; do
    [ -f "$candidate" ] || continue
    FOLDER_ID="$(jq -r '.folder_id // empty' "$candidate" 2>/dev/null || true)"
    if [ -z "$FOLDER_ID" ]; then
      url="$(jq -r '.folder_url // empty' "$candidate" 2>/dev/null || true)"
      FOLDER_ID="$(printf '%s' "$url" | sed -n 's#.*/folders/\([A-Za-z0-9_-]\{10,\}\).*#\1#p')"
    fi
    [ -n "$FOLDER_ID" ] && break
  done
fi
[ -n "$FOLDER_ID" ] || {
  echo "No folder id was given and none was found in pastex-google-drive.json." >&2
  echo "Use: $0 -k key.json <FOLDER_ID>" >&2; exit 1; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# --- token (RS256, signed locally; the key is never uploaded) --------------
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

service_email="$(jq -r '.client_email' "$KEY_FILE")"
created=()

# drive <method> <url> [body-file] [content-type] -> prints "status<TAB>body"
drive() {
  local method="$1" url="$2" body="${3:-}" ctype="${4:-application/json; charset=UTF-8}"
  if [ -n "$body" ]; then
    curl --silent --show-error -X "$method" -H "Authorization: Bearer $token" \
      -H "Content-Type: $ctype" --data-binary @"$body" \
      -w '\n%{http_code}' "$url"
  else
    curl --silent --show-error -X "$method" -H "Authorization: Bearer $token" \
      -w '\n%{http_code}' "$url"
  fi
}

# report <label> <status> <body>
report() {
  local label="$1" status="$2" body="$3"
  if [ "$status" = "200" ] || [ "$status" = "201" ]; then
    echo "  OK    $label (HTTP $status)"
  else
    echo "  FAIL  $label (HTTP $status)"
    printf '%s' "$body" | jq -r '.error.message // empty' 2>/dev/null | sed 's/^/        /'
    [ -n "$(printf '%s' "$body" | jq -r '.error.message // empty' 2>/dev/null)" ] || printf '%s\n' "$body" | head -c 300 | sed 's/^/        /'
  fi
}

echo "Service account : $service_email"
echo "Library folder  : $FOLDER_ID"
echo

echo "1. Is the folder a Shared Drive folder?"
out="$(drive GET "https://www.googleapis.com/drive/v3/files/$FOLDER_ID?fields=id,name,driveId&supportsAllDrives=true")"
status="$(printf '%s' "$out" | tail -n 1)"; body="$(printf '%s' "$out" | sed '$d')"
if [ "$status" = "200" ]; then
  drive_id="$(printf '%s' "$body" | jq -r '.driveId // empty')"
  if [ -n "$drive_id" ]; then
    echo "  YES   this folder lives in a Shared Drive ($drive_id)"
  else
    echo "  NO    this folder is in My Drive, where a service account may not"
    echo "        create files. Move the library into a Shared Drive."
  fi
else
  echo "  FAIL  could not read the folder (HTTP $status)"
fi
echo

echo "2. Can the service account see the folder?"
out="$(drive GET "https://www.googleapis.com/drive/v3/files/$FOLDER_ID?fields=id,name,mimeType,capabilities/canAddChildren&supportsAllDrives=true")"
status="$(printf '%s' "$out" | tail -n 1)"; body="$(printf '%s' "$out" | sed '$d')"
report "read the folder" "$status" "$body"
if [ "$status" != "200" ]; then
  echo
  echo "Share the folder with $service_email (Editor) and try again."
  exit 1
fi
echo "        name: $(printf '%s' "$body" | jq -r '.name')"
echo "        can add children: $(printf '%s' "$body" | jq -r '.capabilities.canAddChildren // "unknown"')"
echo

echo "3. Can it create a folder?"
printf '%s' "{\"name\":\"pastex-write-check-folder\",\"mimeType\":\"application/vnd.google-apps.folder\",\"parents\":[\"$FOLDER_ID\"]}" > "$tmpdir/folder.json"
out="$(drive POST "https://www.googleapis.com/drive/v3/files?supportsAllDrives=true&fields=id" "$tmpdir/folder.json")"
status="$(printf '%s' "$out" | tail -n 1)"; body="$(printf '%s' "$out" | sed '$d')"
report "create a folder" "$status" "$body"
made_folder="$(printf '%s' "$body" | jq -r '.id // empty' 2>/dev/null)"
[ -n "$made_folder" ] && created+=("$made_folder")
echo

echo "4. Can it upload a text file? (this is what adding a snippet does)"
printf 'Pastex write check.' > "$tmpdir/body.txt"
boundary="pastex-check-$$"
{
  printf -- '--%s\r\n' "$boundary"
  printf 'Content-Type: application/json; charset=UTF-8\r\n\r\n'
  printf '%s\r\n' "{\"name\":\"pastex-write-check.txt\",\"mimeType\":\"text/plain\",\"parents\":[\"$FOLDER_ID\"]}"
  printf -- '--%s\r\n' "$boundary"
  printf 'Content-Type: text/plain; charset=UTF-8\r\n\r\n'
  cat "$tmpdir/body.txt"
  printf '\r\n--%s--' "$boundary"
} > "$tmpdir/multipart.bin"
out="$(drive POST "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&fields=id" \
  "$tmpdir/multipart.bin" "multipart/related; boundary=\"$boundary\"")"
status="$(printf '%s' "$out" | tail -n 1)"; body="$(printf '%s' "$out" | sed '$d')"
report "upload a text file" "$status" "$body"
made_file="$(printf '%s' "$body" | jq -r '.id // empty' 2>/dev/null)"
[ -n "$made_file" ] && created+=("$made_file")
echo

echo "5. Can it write the order/collapse index?"
if [ -n "$made_file" ]; then
  printf '%s' '{"v":2,"order":{},"collapsed":{}}' > "$tmpdir/index.json"
  out="$(drive PATCH "https://www.googleapis.com/upload/drive/v3/files/$made_file?uploadType=media&supportsAllDrives=true&fields=id" \
    "$tmpdir/index.json" "text/plain; charset=UTF-8")"
  status="$(printf '%s' "$out" | tail -n 1)"; body="$(printf '%s' "$out" | sed '$d')"
  report "replace a file body" "$status" "$body"
else
  echo "  SKIP  no file was created in step 4"
fi
echo

echo "6. Cleaning up what this check created"
for id in "${created[@]}"; do
  printf '%s' '{"trashed":true}' > "$tmpdir/trash.json"
  out="$(drive PATCH "https://www.googleapis.com/drive/v3/files/$id?supportsAllDrives=true&fields=id,trashed" "$tmpdir/trash.json")"
  status="$(printf '%s' "$out" | tail -n 1)"
  [ "$status" = "200" ] && echo "  removed $id" || echo "  could not remove $id (HTTP $status)"
done

echo
cat <<'NEXT'
If step 4 failed with "Service Accounts do not have storage quota":
  A service account has no storage of its own and cannot own new files in a
  personal folder. Folders it creates are owned by the folder owner, which is
  why creating a folder appears to work. Fix it in one of two ways:
    - use a Shared Drive folder (the service account must be a member with
      Content manager or better), or
    - keep the folder in My Drive but create the snippets there yourself; Pastex
      can read, edit, rename and move them, it just cannot create new ones.
NEXT
