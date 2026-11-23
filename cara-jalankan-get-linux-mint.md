# Menjalankan Pastex di Linux Mint

Panduan ini menjawab dua kemungkinan arti "menjalankan get":

1. **[Menjalankan perintah `GET` Drive API](#1-menjalankan-perintah-get-drive-api)** — untuk melihat isi label `pastex_index` (urutan & collapse).
2. **[Memasang dan menjalankan Pastex](#2-memasang-dan-menjalankan-pastex)** — ekstensi Chrome-nya sendiri di Linux Mint.

---

## 1. Menjalankan perintah GET Drive API

### Yang perlu disiapkan

| Kebutuhan | Cara memasang di Linux Mint |
| --- | --- |
| `curl` | sudah terpasang |
| `jq` | `sudo apt install jq` |
| `openssl` | sudah terpasang |

Juga perlu dua hal yang sama dengan yang dipakai ekstensinya:

- **`service-account.json`** — kunci akun layanan (berisi `client_email` dan `private_key`).
- **ID folder** Perpustakaan di Drive (folder yang di-share ke `client_email` tadi).

### Cara termudah: pakai skrip yang sudah disediakan

Repo ini punya `scripts/get-pastex-label.sh`. Ia membuat token OAuth sendiri dari kunci service account (JWT RS256 ditandatangani `openssl`), lalu memanggil Drive API.

```bash
cd ~/paste                       # folder checkout repo
chmod +x scripts/get-pastex-label.sh

# lihat label folder utama (ID dibaca dari pastex-google-drive.json bila ada)
./scripts/get-pastex-label.sh -k ~/kunci/service-account.json

# atau sebutkan ID folder/berkas secara eksplisit
./scripts/get-pastex-label.sh -k ~/kunci/service-account.json 1AbCdEfGhIjKlMnOpQrStUv
```

Keluaran contoh:

```text
Berkas : Perpustakaan  (1AbCdEfGhIjKlMnOpQrStUv)
Properti mentah:
{
  "pastex_index": "{\"order\":{\"1AbCd...\":[\"idB\",\"idC\"]},\"collapsed\":{\"idF\":\"1\"}}"
}

pastex_index (dirapikan):
{
  "order": {
    "1AbCd...": ["idB", "idC"]
  },
  "collapsed": {
    "idF": "1"
  }
}
```

Kalau Anda ingin tahu label **satu berkas** (bukan folder utama), sebutkan ID berkasnya — skrip akan menampilkan `pastex_order` / `pastex_collapsed` bila masih memakai format lama.

### Cara manual (tanpa skrip)

Ini yang sebenarnya dijalankan skrip di atas, kalau Anda ingin melakukannya sendiri:

```bash
# 1. Buat JWT yang ditandatangani private key, tukar jadi access token
#    (lihat isi scripts/get-pastex-label.sh untuk langkah lengkapnya)
TOKEN="$(...)"   # access_token dari https://oauth2.googleapis.com/token

# 2. GET properti folder
curl --silent --show-error \
  -H "Authorization: Bearer $TOKEN" \
  "https://www.googleapis.com/drive/v3/files/FOLDER_ID?fields=id,name,properties" | jq .
```

Inti permintaannya hanya satu baris:

```text
GET https://www.googleapis.com/drive/v3/files/<FILE_ID>?fields=properties
```

### Tanpa terminal sama sekali

Kalau hanya ingin *melihat* nilainya, tidak perlu API:

1. Buka <https://drive.google.com>
2. Klik kanan folder Perpustakaan → **Detail**
3. Bagian **Properti** → ada `pastex_index`

### Status pengujian skrip ini

Alur JWT-nya sudah saya uji sampai tanda tangannya **terverifikasi** dengan
`openssl dgst -verify` (hasil: `Verified OK`). Yang belum bisa saya uji adalah
panggilan jaringannya: sandbox tempat saya bekerja memblokir
`oauth2.googleapis.com`. Jadi langkah pertama yang akan Anda jalankan di Linux
Mint adalah uji nyata pertama untuk bagian itu — kalau ada yang meleset,
kemungkinan besar di tabel "Kalau gagal" di bawah.

### Kalau gagal

| Gejala | Sebab dan solusi |
| --- | --- |
| `Berkas kunci tidak ditemukan` | tunjuk dengan `-k /path/ke/service-account.json` |
| `Gagal mendapat token akses` | Drive API belum diaktifkan di Google Cloud, atau kunci salah |
| `curl: (35) SSL_connect` | jaringan/proxy memblokir `oauth2.googleapis.com` |
| `Permintaan gagal. Pastikan folder dibagikan ke: …` | folder belum di-share ke `client_email` sebagai **Editor** |
| `pastex_index tidak ada di objek ini` | folder ini belum pernah di-reorder/di-fold, atau masih memakai label lama per-item |

---

## 2. Memasang dan menjalankan Pastex

### Cara yang dianjurkan: repositori APT (bertanda tangan)

Archive-nya memakai suite bernama **`stable`** (itu nama tetap arsip proyek, bukan nama rilis Linux Mint), dan mendukung semua arsitektur karena paketnya `Architecture: all`.

Langkah lengkap beserta verifikasi fingerprint ada di [`apt-repo/README.md`](../apt-repo/README.md). Ringkasnya, setelah key dan sumber dipasang:

```bash
sudo apt update
sudo apt install pastex
```

Lalu **keluar sepenuhnya** dari Chrome/Chromium dan buka lagi. Pada peluncuran berikutnya browser menawarkan mengaktifkan Pastex — setujui.

> Penting: Chrome membaca berkas pendaftaran ekstensi **saat proses browser dimulai**, bukan saat jendela ditutup. Setelah pemasangan atau upgrade, benar-benar keluar dari browser (atau reboot) sebelum memeriksa versinya.

### Memeriksa versi yang terpasang

```bash
dpkg -s pastex | grep -i version          # versi paket, mis. 2.13.3-1
```

Versi ekstensi yang aktif terlihat di panel Pastex. Kalau paket sudah baru tetapi ekstensi masih lama, browser belum dimulai ulang sepenuhnya.

### Kebutuhan browser

Chrome atau Chromium **116 atau lebih baru**. Di Linux Mint, Chromium tersedia sebagai paket Snap atau Flatpak; keduanya berjalan di sandbox sendiri dan **tidak membaca** `/usr/share/chromium/extensions`, sehingga paket `.deb` tidak bisa mendaftarkan ekstensinya di sana. Untuk Chromium jenis ini, muat manual:

1. Buka `chrome://extensions`
2. Aktifkan **Developer mode**
3. **Load unpacked** → pilih `/usr/share/pastex/extension`

Google Chrome (`.deb` resmi dari Google) tidak punya masalah ini.

### Sesudah terpasang: menyiapkan Drive

Ekstensi belum berguna sebelum tiga berkas konfigurasi disiapkan. Urutannya (detail di [`extension/README.md`](../extension/README.md)):

1. Aktifkan **Drive API**, buat service account, unduh kuncinya.
2. Bagikan **folder snippet** ke `client_email` akun layanan itu sebagai **Editor**.
3. Buat satu folder di komputer Anda dan taruh tiga berkas di dalamnya:
   `service-account.json`, `pastex-google-drive.json` (`{"folder_id": "..."}`),
   dan `pastex-sites.json` (`{"allowed_sites": [...]}`).
4. Klik **Config** di panel, lalu pilih **folder** itu. Ketiga berkas dikenali
   dari isinya, jadi namanya tidak harus persis dan berkas lain boleh ada.

Tidak ada yang perlu diunggah ke Drive dan tidak ada tautan yang perlu
dibagikan: kunci akun layanan tidak pernah keluar dari komputer Anda.

### Membangun dari kode (pengembangan saja)

```bash
sudo apt install debhelper devscripts python3 openssl
./scripts/build-deb.sh                    # menghasilkan dist/pastex_<versi>-1_all.deb
sudo ./scripts/install.sh                 # build + apt install
```

Peringatan: tanpa variabel `PASTEX_CRX_KEY` yang menunjuk kunci rilis, build lokal menghasilkan **extension ID sementara** — ID dan penyimpanan browser berubah setiap build. Ini hanya untuk pengujian, bukan pemakaian sehari-hari.

### Menghapus

```bash
sudo apt remove pastex
```

Data snippet Anda tetap aman di Google Drive; yang terhapus hanya ekstensinya.
