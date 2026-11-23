#!/usr/bin/env bash
# Perbaiki salinan get-pastex-label.sh yang barisnya berawalan '+'
# (gejala: "line 1: +#!/usr/bin/env: No such file or directory")
#
# Pemakaian:
#   ./perbaiki-skrip.sh get-pastex-label.sh
#
# Skrip ini membuat cadangan .bak lebih dulu, lalu memeriksa hasilnya.

set -euo pipefail

f="${1:-get-pastex-label.sh}"

[ -f "$f" ] || { echo "Berkas tidak ditemukan: $f" >&2; exit 1; }

# Hitung baris yang diawali '+'. Kalau nol, berkas sudah bersih.
plus=$(grep -c '^+' "$f" || true)
if [ "${plus:-0}" -eq 0 ]; then
  echo "Tidak ada baris berawalan '+' di $f — berkas tampak sudah bersih."
  head -1 "$f" | sed 's/^/Baris pertama: /'
  exit 0
fi

echo "Ditemukan $plus baris berawalan '+' — membuang awalan itu."

# Hati-hati: baris '+++' (header diff) juga ikut kehilangan tanda '+',
# jadi buang dulu baris header diff sebelum membersihkan awalan '+'.
cp -p "$f" "$f.bak"
echo "Cadangan: $f.bak"

grep -v '^+++ ' "$f" | sed 's/^+//' > "$f.baru"
mv "$f.baru" "$f"
chmod +x "$f"

echo
echo "Sisa baris berawalan '+': $(grep -c '^+' "$f" || echo 0)"
echo "Baris pertama sekarang: $(head -1 "$f")"
echo
echo "Uji cepat:"
./"$f" --help | head -4
