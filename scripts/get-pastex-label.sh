#!/usr/bin/env bash
# Lihat label Pastex (pastex_index, pastex_order, pastex_collapsed) di Drive.
#
# Pemakaian:
#   ./scripts/get-pastex-label.sh                     # label di folder utama
#   ./scripts/get-pastex-label.sh <FILE_ID>           # label sebuah berkas/folder
#   ./scripts/get-pastex-label.sh --help
#
# Butuh: curl, jq, openssl (semua sudah terpasang di Linux Mint secara bawaan).
# Kunci service account dibaca dari berkas JSON, tidak pernah ditulis ke disk lain
# dan tidak pernah dicetak.

set -euo pipefail

KEY_FILE=""
FILE_ID=""
SCOPE="https://www.googleapis.com/auth/drive.readonly"

usage() {
  cat <<'USAGE'
Penggunaan: get-pastex-label.sh [opsi] [FILE_ID]

  FILE_ID              id folder/berkas Google Drive (default: folder utama Pastex)
  -k, --key BERKAS     service-account.json (default: ./service-account.json)
  -h, --help           tampilkan bantuan ini

Contoh:
  ./scripts/get-pastex-label.sh -k ~/kunci/service-account.json
  ./scripts/get-pastex-label.sh -k ~/kunci/service-account.json 17Xk...folderId

Keluaran: properti (label) mentah, lalu pastex_index yang sudah dirapikan.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -k|--key) KEY_FILE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Opsi tidak dikenal: $1" >&2; usage >&2; exit 2 ;;
    *) FILE_ID="$1"; shift ;;
  esac
done

for tool in curl jq openssl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Butuh '$tool'. Pasang: sudo apt install $tool" >&2; exit 1; }
done

if [ -z "$KEY_FILE" ]; then
  for candidate in service-account.json ./service-account.json "$HOME/service-account.json"; do
    if [ -f "$candidate" ]; then KEY_FILE="$candidate"; break; fi
  done
fi
[ -n "$KEY_FILE" ] && [ -f "$KEY_FILE" ] || {
  echo "Berkas kunci tidak ditemukan. Tunjuk dengan: -k /path/service-account.json" >&2; exit 1; }

# Tanpa id, folder utama dibaca dari pastex-google-drive.json bila ada di sekitar.
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
  echo "ID folder tidak diberikan dan tidak ditemukan di pastex-google-drive.json." >&2
  echo "Pakai: $0 -k kunci.json <FOLDER_ID>" >&2; exit 1; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# --- tanda tangan JWT (RS256) dengan openssl -------------------------------
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
[ -n "$token" ] || { echo "Gagal mendapat token akses. Periksa kunci dan Drive API." >&2; exit 1; }

# --- baca properti ---------------------------------------------------------
body="$(curl --fail --silent --show-error -H "Authorization: Bearer $token" \
  "https://www.googleapis.com/drive/v3/files/$(printf '%s' "$FILE_ID" | jq -sRr @uri)?fields=id,name,mimeType,properties")" || {
  echo "Permintaan gagal. Pastikan folder dibagikan ke: $(jq -r '.client_email' "$KEY_FILE")" >&2; exit 1; }

echo "Berkas : $(printf '%s' "$body" | jq -r '.name // "?"')  ($(printf '%s' "$body" | jq -r '.id'))"
echo "Properti mentah:"
printf '%s' "$body" | jq '.properties // {}' 

index="$(printf '%s' "$body" | jq -r '.properties.pastex_index // empty')"
echo
if [ -z "$index" ]; then
  echo "pastex_index tidak ada di objek ini."
  echo "Kalau ini folder utama, urutan/lipat mungkin masih memakai label lama"
  echo "per-item (pastex_order / pastex_collapsed) pada tiap berkas."
else
  echo "pastex_index (dirapikan):"
  printf '%s' "$index" | jq . 2>/dev/null || printf '%s\n' "$index"
fi
