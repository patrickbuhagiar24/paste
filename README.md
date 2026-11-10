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
- 🔐 **Izin otomatis** — paket mengatur grup sempit `paste-teks` + udev rule
  agar `/dev/uinput` bisa dipakai tanpa masuk grup `input`. Bisa juga
  diaktifkan dari tombol di aplikasi.
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
  **Akses ini diatur otomatis saat paket dipasang** — lihat
  [Izin `/dev/uinput`](#izin-devuinput-grup-paste-teks) di bawah.
- Di X11, `xdotool` tidak butuh izin khusus.

## Izin `/dev/uinput` (grup `paste-teks`)

`/dev/uinput` dimiliki `root:input` dengan mode `0660`, sehingga pengguna biasa
tidak bisa memakainya. Paste Teks **tidak** memasukkan Anda ke grup `input`
(grup itu memberi akses ke *semua* perangkat input — setara keylogger).
Sebagai gantinya, paket membuat grup sistem sempit `paste-teks` plus udev rule
yang hanya membuka `/dev/uinput`:

```
KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="paste-teks", MODE="0660", OPTIONS+="static_node=uinput"
```

Tiga cara mengaktifkannya:

| Cara | Kapan dipakai |
|---|---|
| **Otomatis saat `apt install`/`upgrade`** | `postinst` menebak akun Anda dari `SUDO_USER`, lalu sesi grafis aktif (`loginctl`), lalu akun biasa tunggal — lalu menambahkannya ke grup. |
| **Tombol di aplikasi: 🔐 Izin Paste Otomatis** | Kapan saja, termasuk bila pemasangan lewat GNOME Software. Muncul prompt kata sandi (`pkexec`). |
| **Terminal** (manual) | Bila tidak ada helper polkit: |

  ```bash
  sudo groupadd --system paste-teks
  echo 'KERNEL=="uinput", SUBSYSTEM=="misc", GROUP="paste-teks", MODE="0660", OPTIONS+="static_node=uinput"' \
    | sudo tee /usr/lib/udev/rules.d/70-paste-teks-uinput.rules
  sudo usermod -aG paste-teks "$USER"
  sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput
  ```

⚠️ **Logout lalu login ulang** (atau reboot) setelah grup berubah — keanggotaan
grup hanya terbaca saat sesi baru dimulai. Sampai saat itu tombol *Paste
Otomatis* hanya menyalin teks ke clipboard (status bar akan memberi tahu).

Cek status kapan saja:

```bash
paste-teks --check    # status grup, izin /dev/uinput, ydotoold + langkah perbaikan
```

Contoh keluaran saat akun sudah ditambahkan tetapi sesi belum dimulai ulang:

```
Izin paste otomatis (grup 'paste-teks', akun 'patrick'):
  ❌ grup 'paste-teks': anggota di sistem — sesi belum memuat
  ❌ tulis /dev/uinput: /dev/uinput tidak bisa ditulis — muncul setelah login ulang
  ❌ daemon ydotoold: belum berjalan

Langkah berikutnya:
  1. Akun 'patrick' sudah menjadi anggota grup 'paste-teks' di sistem, tetapi
     SESI INI belum memuatnya. Logout lalu login ulang (atau reboot) — wajib.
  2. ydotoold belum berjalan. Jalankan `systemctl --user start ydotoold`.
```

Status dibedakan dengan cermat: **anggota di sistem** (`/etc/group`) vs
**aktif di sesi berjalan** (dibaca dari `/proc/self/status`), sehingga saran
"login ulang" hanya muncul bila memang itu masalahnya.

Saat paket dihapus (`apt purge`), grup `paste-teks` ikut dihapus bila tidak ada
anggota tersisa.

## Struktur repo

```
paste_teks/          Kode Python (GUI + logika + clipboard)
  app.py             GUI Tkinter hierarki + CLI
  snippets.py        Pindai/kelola pohon berkas .txt (tanpa GUI, mudah diuji)
  clipboard.py       Salin (wl-copy/xclip/xsel) + paste otomatis (ydotool/xdotool)
  uinput.py          Deteksi izin /dev/uinput + aktivasi grup 'paste-teks'
usr/lib/paste-teks/  Helper root (dipanggil via pkexec untuk menambah grup)
usr/lib/udev/rules.d/  udev rule grup 'paste-teks' untuk /dev/uinput
debian/postinst      Deteksi akun & penambahan grup otomatis saat instalasi
data/snippets/       Contoh teks bawaan per kategori
debian/              Kemasan Debian (.deb)
scripts/             build-deb.sh, setup-apt-repo.sh, install.sh
apt-repo/            Hasil repo APT + contoh sources.list
tests/               Uji pytest (snippets, clipboard, izin uinput, CLI)
.github/workflows/   CI: uji → bangun .deb → terbitkan repo APT ke gh-pages
```

## Pemecahan masalah

- **Jendela tidak muncul saat klik ikon**: jalankan di terminal untuk melihat error:
  ```bash
  paste-teks --check   # laporan lingkungan (tkinter, display, tool, folder)
  paste-teks           # jalankan langsung — traceback tampil di terminal
  cat ~/.config/paste-teks/error.log   # log error terakhir
  ```
- **Paste otomatis tidak bekerja (Wayland)**: klik **🔐 Izin Paste Otomatis** di
  aplikasi, lalu logout & login ulang. Teks tetap tersalin sebelum itu — cukup
  tekan `Ctrl+V` manual di aplikasi tujuan. Detail di
  [Izin `/dev/uinput`](#izin-devuinput-grup-paste-teks).
- **Pesan "ydotoold belum berjalan"**: jalankan `systemctl --user start ydotoold`
  (ydotool versi baru memakai daemon).

## Uji & build

```bash
python3 -m pytest tests/ -q
./scripts/build-deb.sh          # → dist/paste-teks_1.1.1-1_all.deb
./scripts/setup-apt-repo.sh     # → apt-repo/dist/ (Packages + Release)
```

## Lisensi

MIT — lihat [LICENSE](LICENSE).
