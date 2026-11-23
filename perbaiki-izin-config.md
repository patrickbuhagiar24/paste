# Memperbaiki izin berkas config: dari "SA only" ke "Anyone with the link"

## Kondisi Anda sekarang

```text
service-account.json      → dibagikan ke SA, TANPA link sharing
pastex-sites.json         → dibagikan ke SA, TANPA link sharing
pastex-google-drive.json  → dibagikan ke SA, TANPA link sharing
```

**Hasilnya: konfigurasi tidak akan bisa dipakai.** Ini bukan soal Viewer atau
Editor — keduanya sama-sama gagal di kondisi ini, karena yang hilang adalah
*link sharing*-nya.

## Kenapa gagal

Ekstensi mengunduh ketiga berkas **tanpa kredensial**:

```text
Header yang dikirim:  { "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8" }
Ada Authorization?    false
```

Google melihat permintaan itu sebagai pengunjung anonim. Berkas yang hanya
dibagikan ke akun layanan tidak terlihat oleh pengunjung anonim, jadi Google
mengembalikan **halaman login HTML** dengan status `200`.

Ekstensi lalu mencoba `JSON.parse()` pada HTML itu dan gagal
(`config-manifest.js` baris 102):

```text
A linked configuration file is not valid JSON. Check that each Drive link is
downloadable without signing in.
```

Di side panel, pesannya muncul dibungkus (`sidepanel.js` baris 722):

```text
Could not use configuration: A linked configuration file is not valid JSON.
Check that each Drive link is downloadable without signing in.
```

Dan konfigurasi lama Anda tetap dipertahankan — tidak ada yang rusak.

## Kenapa berbagi ke akun layanan tidak menolong

Bukan karena tingkat izinnya kurang, tetapi karena **akun layanan tidak pernah
ikut dalam permintaan itu**. Rantainya:

```text
1. ekstensi unduh pastex-google-drive.json   ← anonim, saat ini gagal di sini
2. ekstensi unduh service-account.json       ← anonim
3. ekstensi unduh pastex-sites.json          ← anonim
4. baru sekarang kunci tersedia → token → folder snippet (Editor)
```

Langkah 4 adalah satu-satunya tempat akun layanan dipakai. Langkah 1–3 terjadi
sebelum kunci ada, jadi identitas SA belum mungkin dipakai — apalagi kalau
kuncinya sendiri ada di langkah 2.

## Yang harus diubah

Untuk **ketiga berkas**, ubah dari "dibagikan ke SA" menjadi
**"Anyone with the link"**.

Langkahnya untuk setiap berkas:

1. Buka <https://drive.google.com>
2. Klik kanan berkas → **Bagikan** (atau **Share**)
3. Di bagian **Akses umum** / *General access*, ubah dari **Dibatasi** /
   *Restricted* menjadi **Siapa saja yang memiliki tautan** /
   *Anyone with the link*
4. Pilih peran **Pelihat** / *Viewer*
5. **Salin tautan** → klik **Selesai**

> Di Drive berbahasa Indonesia, "Anyone with the link" muncul sebagai
> **"Siapa saja yang memiliki link"**. Perannya **"Pelihat"** (Viewer).
> Kalau peran yang muncul "Pengedit"/Editor, ganti ke "Pelihat".

**Peran Viewer sudah cukup, dan Editor tidak diperlukan.** Editor juga akan
membuat berkas bisa diunduh, tetapi memberi kuasa tulis kepada siapa pun yang
memegang tautannya — tanpa manfaat, karena ketiga berkas ini tidak pernah ditulis.

### Yang TIDAK perlu diubah

| Objek | Biarkan seperti apa adanya |
| --- | --- |
| Berbagi ke `client_email` SA | Boleh tetap ada, tidak mengganggu |
| **Folder snippet** | **Tetap berbagi ke SA sebagai Editor** — ini wajib |
| Izin folder config | Tidak menurun ke berkas; atur per berkas |

## Setelah mengubah: verifikasi

### Cara 1 — skrip

```bash
chmod +x cek-izin-tautan.sh
./cek-izin-tautan.sh \
  "https://drive.google.com/file/d/ID_PASSTEX-GOOGLE-DRIVE/view" \
  "https://drive.google.com/file/d/ID_PASTEX-SITES/view" \
  "https://drive.google.com/file/d/ID_SERVICE-ACCOUNT/view"
```

Keluaran yang Anda harapkan:

```text
  HTTP    : 200   (xxx byte, Content-Type: application/json)
  DIAGNOSIS: BERHASIL — JSON terbaca tanpa login.
```

Keluaran yang menandakan belum beres:

```text
  HTTP    : 200   (xxxx byte, Content-Type: text/html)
  DIAGNOSIS: yang kembali HALAMAN HTML, bukan JSON.
             PERBAIKAN: di Drive buka berkas -> Bagikan ->
             'Anyone with the link' -> Viewer -> Salin tautan.
```

### Cara 2 — tanpa skrip: jendela privat

Ini meniru kondisi ekstensi (tanpa login):

1. Buka jendela **Incognito/Privat** di browser
2. Tempel tautan `https://drive.google.com/uc?export=download&id=<ID>`
3. **Berhasil** = yang muncul teks JSON (`{"folder_id": ...}`)
4. **Gagal** = muncul halaman masuk akun Google

### Cara 3 — langsung di ekstensi

1. Buka side panel → **Config**
2. Pilih ulang `manifest.json`
3. Berhasil = daftar snippet muncul
4. Gagal = pesan *"A linked configuration file is not valid JSON…"*

## Kalau sudah diubah tapi masih gagal

| Gejala | Penyebab |
| --- | --- |
| Masih halaman HTML | Perubahan belum tersimpan; cek ulang di **Bagikan** |
| `HTTP 404` | ID berkas di manifest salah, atau berkas sudah dihapus |
| JSON terbaca tapi daftar kosong | `folder_id` menunjuk folder salah, atau folder belum di-share ke SA |
| `must be an https://drive.google.com/file/d/... link` | ID di manifest kosong/salah bentuk |
| `service-account.json` tidak dikenali | Isinya bukan kunci SA (`type` bukan `service_account`) |

## Catatan keamanan

Mengubah ke "Anyone with the link" berarti **siapa pun yang memegang tautan
bisa membaca berkas itu**. Untuk `service-account.json` ini serius — isinya
kunci privat.

Yang bisa dilakukan untuk memperkecil risiko:

1. Jangan sebar tautan config di chat, forum, atau tangkapan layar.
2. Pakai kunci **khusus Pastex** (jangan pakai ulang kunci proyek lain seperti
   `yt-unsub-detector`).
3. Rotasi kunci bila tautan pernah bocor.
4. Pastikan akun layanan hanya punya akses ke **folder snippet**, bukan seluruh Drive.

Tanpa "Anyone with the link", ekstensi tidak bisa bekerja sama sekali — itu
kompromi desain dari alur berbasis tautan, bukan pilihan konfigurasi.
