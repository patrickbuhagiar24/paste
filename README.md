# Paste Teks

**GUI paste teks hierarki dari berkas `.txt` untuk Ubuntu 26.10** — atur teks
per kategori/folder, lalu **tempel langsung ke input aplikasi lain dalam satu
klik** (salin + `Ctrl+V` otomatis).

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![Ubuntu](https://img.shields.io/badge/Ubuntu-26.10-orange) ![Lisensi](https://img.shields.io/badge/Lisensi-MIT-green)

## Fitur

- 🌳 **Hierarki kategori/folder** — struktur folder di disk menjadi pohon di GUI.
  Setiap folder = kategori, setiap berkas `.txt` = satu teks siap paste.
- ⚡ **Paste satu klik** — double-click / `Enter` / tombol *Paste ke Aplikasi
  Lain*: teks disalin ke clipboard, jendela dikecilkan, lalu `Ctrl+V` dikirim
  otomatis ke aplikasi yang sedang fokus.
- 📋 **Salin saja** — tombol *Salin* / `Ctrl+C` untuk clipboard tanpa paste otomatis.
- 🔍 **Pencarian** — cari berdasarkan nama berkas *atau isi teksnya*.
- ✏️ **Kelola dari GUI** — tambah kategori, tambah teks, edit (editor internal),
  hapus, buka folder di Nautilus. Perubahan tersimpan di folder pengguna.
- ⚙️ **Pengaturan** — jeda sebelum paste otomatis (0,5–5 detik), mode
  double-click, dan *selalu di atas*. Tersimpan di `~/.config/paste-teks/`.
- 🖥️ **Wayland & X11** — clipboard via `wl-copy`/`xclip`/`xsel`, paste otomatis
  via `ydotool`/`xdotool`, fallback clipboard internal Tk bila tool tidak ada.
- 🧩 **CLI juga** — `paste-teks --list`, `--copy FILE`, `--paste FILE` untuk skrip.

## Cara pakai

1. Buka **Paste Teks** dari menu Aplikasi (atau jalankan `paste-teks`).
2. Di panel kiri, klik folder untuk mengembang/menyusut; klik berkas `.txt`
   untuk melihat pratinjau isinya di panel kanan.
3. **Double-click** berkas (atau tekan `Enter`) → teks disalin dan ditempel
   otomatis. Dalam jeda yang diatur, **klik aplikasi tujuan** (mis. browser,
   editor, form) agar `Ctrl+V` terkirim ke sana.
4. Tambah teks sendiri: tombol **＋ Teks** / **＋ Kategori**, atau buat berkas
   `.txt` langsung di `~/.local/share/paste-teks/snippets/` lalu tekan `F5`.

Contoh bawaan (dari `/usr/share/paste-teks/snippets`):

```
Umum/            Sapaan.txt, Tanda Tangan.txt
Email/           Balas Lamaran.txt, Tindak Lanjut.txt
Kerja/           Format Laporan.txt
Alamat & Kontak/ Alamat.txt
Perintah Terminal/ Perintah Umum.txt
```

### Pintasan keyboard

| Tombol | Aksi |
|---|---|
| Double-click / `Enter` | Paste otomatis (satu klik) |
| `Ctrl+C` | Salin ke clipboard |
| `F5` | Muat ulang pohon |
| `F2` | Edit berkas terpilih |
| `Delete` | Hapus berkas/kategori terpilih |
| `Ctrl+F` | Fokus ke pencarian |

## Instalasi

### A. Lewat APT sources (disarankan)

Repo APT menyediakan `Packages` + `Release` sehingga `apt install paste-teks`
berfungsi seperti paket resmi, termasuk update otomatis via `apt upgrade`.

**Repo lokal (file://)** — bangun dari source:

```bash
./scripts/setup-apt-repo.sh
echo "deb [trusted=yes] file://$(pwd)/apt-repo/dist ./" | sudo tee /etc/apt/sources.list.d/paste-teks.list
sudo apt update
sudo apt install paste-teks
```

**Repo lokal bertanda tangan GPG** (lebih aman, disarankan):

```bash
GPG_KEY="KunciAnda" ./scripts/setup-apt-repo.sh
gpg --armor --export KunciAnda | sudo gpg --dearmor -o /usr/share/keyrings/paste-teks.gpg
echo "deb [signed-by=/usr/share/keyrings/paste-teks.gpg] file://$(pwd)/apt-repo/dist ./" | sudo tee /etc/apt/sources.list.d/paste-teks.list
sudo apt update && sudo apt install paste-teks
```

**Repo HTTP (GitHub Pages)** — CI di `.github/workflows/build-deb.yml`
membangun dan menerbitkan repo ke `https://patrickbuhagiar24.github.io/paste/apt/`
setiap push ke `main`. Panduan lengkap (termasuk mengaktifkan Pages dan
tanda tangan GPG) ada di [apt-repo/README.md](apt-repo/README.md). Ringkasnya:

1. GitHub → **Settings → Pages → Source: Deploy from a branch → `gh-pages` / `(root)`** (sekali saja).
2. Gabungkan kode ke `main` (workflow berjalan otomatis), atau jalankan manual
   dari tab **Actions**.
3. Di Ubuntu:

```bash
echo "deb [trusted=yes] https://patrickbuhagiar24.github.io/paste/apt/ ./" \
  | sudo tee /etc/apt/sources.list.d/paste-teks.list
sudo apt update && sudo apt install paste-teks
```

Bila repo ditandatangani GPG, ganti `[trusted=yes]` dengan
`[signed-by=/usr/share/keyrings/paste-teks.gpg]` setelah memasang kunci publik
(`curl .../apt/paste-teks.gpg | sudo gpg --dearmor -o /usr/share/keyrings/paste-teks.gpg`).

### B. Langsung dari .deb

```bash
./scripts/build-deb.sh
sudo apt install ./dist/paste-teks_1.0.0-1_all.deb
```

### C. Dari source (tanpa .deb)

```bash
sudo apt install python3-tk wl-clipboard xclip xdotool ydotool
python3 -m paste_teks          # dari root repo
# atau: ./bin/paste-teks
```

Dependensi: `python3 (>= 3.10)`, `python3-tk`. Direkomendasikan:
`wl-clipboard` (Wayland), `xclip` (X11), `xdotool` (X11), `ydotool` (Wayland).

## Catatan Wayland (Ubuntu 26.10 / GNOME)

- Clipboard Wayland memakai `wl-copy` (dari `wl-clipboard`); X11 memakai `xclip`.
- Paste otomatis di Wayland memakai `ydotool`, yang butuh akses `/dev/uinput`.
  Bila paste otomatis gagal, tambahkan pengguna ke grup `input` lalu login ulang:

  ```bash
  sudo usermod -aG input "$USER"
  ```

  Teks tetap tersalin — cukup tekan `Ctrl+V` manual di aplikasi tujuan.
- Di X11, `xdotool` tidak butuh izin khusus.

## Struktur repo

```
paste_teks/          Kode Python (GUI + logika + clipboard)
  app.py             GUI Tkinter hierarki + CLI
  snippets.py        Pindai/kelola pohon berkas .txt (tanpa GUI, mudah diuji)
  clipboard.py       Salin (wl-copy/xclip/xsel) + paste otomatis (ydotool/xdotool)
data/snippets/       Contoh teks bawaan per kategori
debian/              Kemasan Debian (.deb)
scripts/             build-deb.sh, setup-apt-repo.sh, install.sh
apt-repo/            Hasil repo APT + contoh sources.list
tests/               Uji pytest (snippets + clipboard)
.github/workflows/   CI: uji → bangun .deb → terbitkan repo APT ke gh-pages
```

## Uji & build

```bash
python3 -m pytest tests/ -q
./scripts/build-deb.sh          # → dist/paste-teks_1.0.0-1_all.deb
./scripts/setup-apt-repo.sh     # → apt-repo/dist/ (Packages + Release)
```

## Lisensi

MIT — lihat [LICENSE](LICENSE).
