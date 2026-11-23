#!/usr/bin/env bash
# Periksa apakah tautan config Pastex benar-benar bisa diunduh TANPA login.
#
# Latar belakang: ekstensi mengambil ketiga berkas config lewat
#   https://drive.google.com/uc?export=download&id=<ID>
# TANPA header Authorization. Artinya berkas harus "Anyone with the link".
# Kalau tidak, yang kembali adalah halaman login HTML, dan ekstensi gagal
# dengan pesan "not valid JSON".
#
# Pemakaian:
#   ./cek-tautan-config.sh            # baca manifest.json di direktori ini
#   ./cek-tautan-config.sh LINKS...   # atau beri 1-3 tautan Drive langsung
#
# Butuh: curl, jq

set -euo pipefail

for t in curl jq; do
  command -v "$t" >/dev/null 2>&1 || { echo "Butuh '$t'. Pasang: sudo apt install $t" >&2; exit 1; }
done

urls=()

add_url() {
  local u="$1"
  case "$u" in
    *_*|*/file/d/*|*open?id=*|*uc?id=*|*uc?export=download*) ;;
  esac
  urls+=("$u")
}

if [ $# -gt 0 ]; then
  for a in "$@"; do add_url "$a"; done
else
  # Ambil tautan dari manifest.json bila ada.
  manifest="${PASTEX_MANIFEST:-manifest.json}"
  if [ ! -f "$manifest" ]; then
    echo "Tidak ada argumen dan $manifest tidak ditemukan." >&2
    echo "Pakai: $0 <tautan1> [tautan2] [tautan3]" >&2
    exit 1
  fi
  # Nilai bisa berupa tautan biasa atau [teks](tautan).
  while IFS= read -r line; do
    url="$(printf '%s' "$line" | sed -n 's/.*(\(https[^)]*\)).*/\1/p')"
    [ -n "$url" ] || url="$line"
    add_url "$url"
  done < <(jq -r 'to_entries[] | .value' "$manifest")
fi

[ "${#urls[@]}" -gt 0 ] || { echo "Tidak ada tautan yang bisa dibaca." >&2; exit 1; }

# Ubah tautan Drive jadi URL unduhan langsung.
to_download() {
  local u="$1" id
  id="$(printf '%s' "$u" | sed -n 's#.*/file/d/\([A-Za-z0-9_-]\{10,\}\).*#\1#p')"
  [ -n "$id" ] || id="$(printf '%s' "$u" | sed -n 's#.*[?&]id=\([A-Za-z0-9_-]\{10,\}\).*#\1#p')"
  if [ -n "$id" ]; then
    printf 'https://drive.google.com/uc?export=download&id=%s' "$id"
  else
    printf '%s' "$u"
  fi
}

fail=0
i=0
for u in "${urls[@]}"; do
  i=$((i + 1))
  dl="$(to_download "$u")"
  tmp="$(mktemp)"
  hdr="$(mktemp)"
  # Simpan kode HTTP yang sebenarnya (satu kali permintaan saja).
  code="$(curl --silent --show-error --location --max-time 30 \
            -o "$tmp" -D "$hdr" -w '%{http_code}' "$dl" 2>/dev/null || true)"
  code="${code:-000}"
  ctype="$(tr -d '\r' < "$hdr" | sed -n 's/^[Cc]ontent-[Tt]ype: *//p' | tail -1)"

  # Jumlah byte yang benar-benar diterima. jq mengembalikan 0 untuk berkas
  # KOSONG, jadi ukuran harus diperiksa lebih dulu.
  bytes="$(wc -c < "$tmp" | tr -d ' ')"

  status=""
  if [ "$code" = "000" ]; then
    status="JARINGAN GAGAL — tidak ada respons (DNS/proxy/blokir)"
    fail=1
  elif [ "$bytes" = "0" ]; then
    status="GAGAL — respons kosong (HTTP $code)"
    fail=1
  elif printf '%s' "$ctype" | grep -qi 'text/html' || head -c 400 "$tmp" | grep -qi '<!DOCTYPE html\|<html'; then
    status="GAGAL — yang kembali halaman HTML (perlu login / tidak publik)"
    fail=1
  elif jq -e . "$tmp" >/dev/null 2>&1; then
    status="OK — JSON terbaca tanpa login ($bytes byte)"
  else
    status="BUKAN JSON ($bytes byte, Content-Type: ${ctype:-kosong})"
    fail=1
  fi

  echo "Tautan $i"
  echo "  URL    : $u"
  echo "  HTTP   : $code   Content-Type: ${ctype:-(kosong)}"
  echo "  Hasil  : $status"
  if [ "$code" != "000" ] && [ -s "$tmp" ]; then
    preview="$(head -c 160 "$tmp" | tr '\n' ' ' | tr -s ' ')"
    echo "  Cuplikan: ${preview:0:160}"
  fi
  echo
  rm -f "$tmp" "$hdr"
done

if [ "$fail" -eq 0 ]; then
  echo "Semua tautan bisa diunduh tanpa login. Ekstensi seharusnya menerimanya."
  exit 0
else
  echo "Ada tautan yang gagal. Perbaiki di Drive: klik kanan berkas ->"
  echo "Bagikan -> 'Anyone with the link' -> Viewer, lalu jalankan lagi."
  exit 1
fi
