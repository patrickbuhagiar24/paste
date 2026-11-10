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
- 🔐 **Nol konfigurasi** — paket memberi ACL `/dev/uinput` (`uaccess`) untuk
  sesi aktif dan menyalakan `ydotoold` otomatis; tanpa grup `input` dan tanpa
  perlu logout.
- 🖥️ **Wayland & X11** — clipboard via `wl-copy`/`xclip`/`xsel`, paste otomatis
  via `ydotool`/`xdotool`, fallback clipboard internal Tk bila tool tidak ada.
- 🧩 **CLI juga** — `paste-teks --list`, `--copy FILE`, `--paste FILE`, serta
  `--test-paste` untuk diagnosis, `--check` untuk laporan lingkungan.

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
  **Akses ini diatur otomatis saat paket dipasang** (ACL `uaccess`, tanpa
  logout) — lihat [Paste otomatis: nol konfigurasi](#paste-otomatis-nol-konfigurasi).
- Di X11, `xdotool` tidak butuh izin khusus.

## Paste otomatis: nol konfigurasi

Sejak **1.2.0** tidak ada langkah manual. Saat `apt install`/`apt upgrade`:

1. **udev rule dengan `TAG+="uaccess"`** — systemd-logind memberi ACL `rw`
   pada `/dev/uinput` untuk pengguna sesi grafis yang aktif. **Berlaku
   seketika, tanpa logout** dan tanpa masuk grup apa pun.
2. **`ydotoold.service`** (unit systemd pengguna) disertakan dalam paket dan
   langsung di-`enable --now` untuk pengguna yang sedang login; bila belum
   login, unit ditautkan agar menyala otomatis pada login berikutnya.
3. **`postinst` memverifikasi** hasilnya (`runuser … test -w /dev/uinput`) dan
   melaporkan `siap: '…' bisa menulis /dev/uinput — paste otomatis aktif.`

Sebagai lapisan cadangan (mis. sesi SSH, container, sistem tanpa logind),
paket tetap membuat grup sistem sempit `paste-teks` dan menambahkan akun
Anda — grup ini hanya membuka `/dev/uinput`, **bukan** seluruh perangkat
input seperti grup `input`.

### Jadi apa yang perlu saya lakukan?

Biasanya: **tidak ada**. Pasang, lalu langsung pakai.

| Situasi | Tindakan |
|---|---|
| `apt install` / `apt upgrade` lewat terminal | Tidak ada — langsung aktif |
| Dipasang lewat GNOME Software | Buka aplikasi sekali; bila perlu klik **🔐 Izin Paste Otomatis** |
| `--check` menunjukkan ❌ | Baca blok **Langkah berikutnya** — isinya perintah siap tempel |
| Daemon mati setelah reboot | `systemctl --user enable ydotoold` (postinst sudah mencoba ini) |

Cek kapan saja:

```bash
paste-teks --check
```

Contoh keluaran saat semuanya beres:

```
Izin paste otomatis (grup 'paste-teks', akun 'patrick'):
  ✅ grup 'paste-teks': anggota, aktif di sesi ini
  ✅ tulis /dev/uinput: /dev/uinput bisa ditulis
  ✅ daemon ydotoold: berjalan (proses ydotoold)

Langkah berikutnya:
  Paste otomatis siap digunakan.
```

Bila ACL yang dipakai (tanpa grup), barisnya menjadi
`✅ akses /dev/uinput: ACL sesi aktif (tanpa grup, tanpa logout)`.

### Kalau ingin manual (opsional)

```bash
sudo groupadd --system paste-teks
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess", GROUP="paste-teks", MODE="0660"' \
  | sudo tee /usr/lib/udev/rules.d/70-paste-teks-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput
systemctl --user enable --now ydotoold
```

Saat paket dihapus (`apt purge`), grup `paste-teks` ikut dihapus bila tidak
ada anggota tersisa.

## Struktur repo

```
paste_teks/          Kode Python (GUI + logika + clipboard)
  app.py             GUI Tkinter hierarki + CLI
  snippets.py        Pindai/kelola pohon berkas .txt (tanpa GUI, mudah diuji)
  clipboard.py       Salin (wl-copy/xclip/xsel) + paste otomatis (ydotool/xdotool)
  uinput.py          Deteksi izin /dev/uinput + aktivasi grup 'paste-teks'
usr/lib/paste-teks/  Helper root (dipanggil via pkexec untuk menambah grup)
usr/lib/udev/rules.d/  udev rule uaccess (+grup cadangan) untuk /dev/uinput
usr/lib/systemd/user/  ydotoold.service (daemon paste otomatis)
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
- **Paste otomatis tidak bekerja**: lakukan dua uji ini untuk memisahkan
  penyebabnya.

  1. **Apakah ydotool benar-benar mengirim ke aplikasi lain?**
     ```bash
     paste-teks --test-paste
     ```
     Klik jendela tujuan dalam 3 detik. Bila teks `paste-teks-ok` muncul,
     ydotool + izin + daemon sudah benar → masalahnya di clipboard.
  2. **Apakah Ctrl+V sampai?**
     ```bash
     paste-teks --test-paste --ctrl-v
     ```
     Bila tidak ada yang tertempel, isi clipboard mungkin kosong/basi — lihat
     baris **Clipboard:** pada `paste-teks --check` (salin + baca kembali).

  Kedua uji ini memakai jalur kode yang sama dengan tombol di GUI, jadi
  hasilnya mewakili aplikasi.

  Catatan teknis: `ydotool` versi ≥ 1.0 **hanya** menerima keycode pada
  `key` (`29:1 47:1 47:0 29:0` = Ctrl+V), sedangkan 0.1.x memakai nama
  kunci (`ctrl+v`). Aplikasi mencoba keycode lebih dulu lalu nama kunci.
- **Pesan "ydotoold belum berjalan"**: jalankan `systemctl --user start ydotoold`
  (ydotool versi baru memakai daemon).

## Uji & build

```bash
python3 -m pytest tests/ -q
./scripts/build-deb.sh          # → dist/paste-teks_1.2.1-1_all.deb
./scripts/setup-apt-repo.sh     # → apt-repo/dist/ (Packages + Release)
```

## Lisensi

MIT — lihat [LICENSE](LICENSE).
