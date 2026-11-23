#!/usr/bin/env bash
# Diagnosis izin tautan Drive untuk berkas config Pastex.
#
# Menjawab satu pertanyaan: "tautan ini menghasilkan JSON, atau halaman login?"
#
# Berbeda dari cek-tautan-config.sh, skrip ini juga melaporkan tanda-tanda
# spesifik tipe respons Drive supaya Anda tahu APA yang perlu diubah.
#
# Pemakaian:
#   ./cek-izin-tautan.sh <FILE_ID_ATAU_TAUTAN> [<...> ...]
#
# Butuh: curl

set -uo pipefail

command -v curl >/dev/null 2>&1 || { echo "Butuh curl." >&2; exit 1; }

[ $# -gt 0 ] || {
  echo "Pakai: $0 <FILE_ID_ATAU_TAUTAN> ..." >&2
  echo "Contoh: $0 1AbCdEfGhIjKlMnOpQrStUvWxYz012345" >&2
  exit 2
}

to_id() {
  local u="$1"
  # Terima ID mentah, /file/d/ID/..., open?id=ID, atau uc?id=ID
  if printf '%s' "$u" | grep -qE '^[A-Za-z0-9_-]{10,}$'; then
    printf '%s' "$u"; return
  fi
  printf '%s' "$u" | sed -n 's#.*/file/d/\([A-Za-z0-9_-]\{10,\}\).*#\1#p' | head -1 | grep . && return
  printf '%s' "$u" | sed -n 's#.*[?&]id=\([A-Za-z0-9_-]\{10,\}\).*#\1#p' | head -1 | grep . && return
  printf ''
}

n=0
for arg in "$@"; do
  n=$((n + 1))
  id="$(to_id "$arg")"
  echo "── Tautan $n ────────────────────────────────────────"
  if [ -z "$id" ]; then
    echo "  ID tidak ditemukan pada: $arg"
    echo "  (ID kosong seperti /file/d//view memang tidak valid.)"
    echo
    continue
  fi
  echo "  ID      : $id"

  url="https://drive.google.com/uc?export=download&id=${id}"
  body="$(mktemp)"; hdr="$(mktemp)"
  code="$(curl --silent --location --max-time 25 -o "$body" -D "$hdr" \
            -w '%{http_code}' "$url" 2>/dev/null || true)"
  code="${code:-000}"
  bytes="$(wc -c < "$body" | tr -d ' ')"
  ctype="$(tr -d '\r' < "$hdr" | sed -n 's/^[Cc]ontent-[Tt]ype: *//p' | tail -1)"
  final="$(grep -ioE '^location: .*' "$hdr" | tail -1 | sed 's/^[Ll]ocation: *//' | tr -d '\r')"

  echo "  HTTP    : $code   ($bytes byte, Content-Type: ${ctype:-kosong})"
  [ -n "$final" ] && echo "  Dialihkan ke: ${final:0:90}"

  if [ "$code" = "000" ]; then
    echo "  DIAGNOSIS: tidak ada respons — periksa jaringan/proxy/firewall."
  elif printf '%s' "$ctype" | grep -qi 'text/html' || head -c 300 "$body" | grep -qi '<!DOCTYPE html\|<html'; then
    echo "  DIAGNOSIS: yang kembali HALAMAN HTML, bukan JSON."
    echo "             Artinya tautan ini tidak bisa diunduh tanpa login."
    if grep -qi 'accounts.google.com\|ServiceLogin' "$body" "$hdr" 2>/dev/null; then
      echo "             Google mengalihkan ke halaman masuk akun."
    fi
    echo "             PERBAIKAN: di Drive buka berkas -> Bagikan ->"
    echo "             'Anyone with the link' -> Viewer -> Salin tautan."
  elif [ "$bytes" = "0" ]; then
    echo "  DIAGNOSIS: respons kosong."
  else
    # Cek apakah isinya JSON yang masuk akal
    if command -v jq >/dev/null 2>&1 && jq -e . "$body" >/dev/null 2>&1; then
      echo "  DIAGNOSIS: BERHASIL — JSON terbaca tanpa login."
      keys="$(jq -r 'if type=="object" then (keys|join(", ")) else "(array)" end' "$body" 2>/dev/null | head -c 120)"
      echo "             Kunci teratas: $keys"
    else
      echo "  DIAGNOSIS: bukan JSON (tetapi bukan halaman login)."
      echo "             Cuplikan: $(head -c 100 "$body" | tr '\n' ' ')"
    fi
  fi
  rm -f "$body" "$hdr"
  echo
done
