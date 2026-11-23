# Pastex — skema untuk pembaca yang tidak memprogram

Dokumen ini menjelaskan **bagaimana Pastex bekerja** tanpa istilah teknis.
Tujuannya: siapa pun yang memegang proyek ini (atau meminta perbaikan) bisa
tahu bagian mana yang harus dibuka, dan apa akibatnya bagi pengguna.

Untuk penjelasan teknis (nama berkas, nama fungsi, bentuk data) ada di
[`extension/README.md`](extension/README.md) dan [`ANALISIS.md`](ANALISIS.md).

---

## 1. Satu kalimat

Pastex adalah **kotak catatan tempel** yang tinggal di sisi kanan Chrome:
tulisan pendek yang sering Anda pakai (sapaan, alamat, balasan) disimpan di
**Google Drive Anda sendiri**, lalu dengan satu klik dimasukkan ke kolom yang
sedang Anda isi di situs web tertentu.

## 2. Empat "ruangan" yang bisa dibayangkan

Bayangkan Pastex sebagai sebuah kantor kecil dengan empat ruangan:

| Ruangan | Isi | Terlihat oleh pengguna? |
| --- | --- | --- |
| **Etalase** (panel samping) | Daftar catatan, tombol tambah/ubah/hapus/cari | Ya — ini yang diklik |
| **Tombol pintar di halaman** (⚡) | Muncul di dekat kolom yang sedang diisi, untuk membuka panel | Ya, hanya di situs yang diizinkan |
| **Petugas belakang** (latar) | Menyimpan perubahan ke Google Drive saat Anda sudah pergi dari situs | Tidak |
| **Gudang** (Google Drive) | Berkas catatan yang sebenarnya | Ya, lewat Drive seperti biasa |

Semua yang ada di etalase hanyalah **salinan cepat** dari gudang. Kalau salinan
dan gudang berbeda, gudang yang benar.

## 3. Perjalanan sebuah catatan (paling mudah dipahami)

**Saat menambah catatan** di halaman web:

```
Pengguna menekan "＋ Text" dan menyimpan
        │
        ▼
Etalase langsung menampilkan baris baru          ← pengguna melihat hasilnya seketika
        │
        ▼
Perubahan dicatat di "buku pesanan" (antrean)    ← belum dikirim ke Drive
        │
        ▼
Pengguna menutup tab situs itu
        │
        ▼
Petugas belakang mengirim pesanan ke Google Drive satu per satu
        │
        ▼
Setelah selesai, petugas membaca ulang gudang supaya nomor/ID resmi
dari Drive menggantikan nomor sementara
```

Kenapa ditunda? Supaya **tidak ada yang menunggu jaringan** saat pengguna
sedang bekerja. Prinsip ini berlaku untuk semua perubahan:

| Yang pengguna lakukan | Yang langsung terlihat | Yang dikirim ke Drive |
| --- | --- | --- |
| Tambah catatan / folder | baris baru muncul | satu unggahan |
| Ubah isi atau ganti nama | nama/isi baru | satu penulisan |
| Hapus | baris hilang | dipindah ke Trash (dikelompokkan) |
| Geser urutan | urutan baru | **satu** tulisan (label urutan) |
| Lipat / buka folder | folder terlipat | **satu** tulisan (label yang sama) |

Kalau browser ditutup sebelum pesanan terkirim, **buku pesanan tidak hilang** —
ia tersimpan dan dikirim saat browser dibuka lagi.

## 4. Kapan Pastex "menengok" ke Drive

Ada tiga pemicu, tidak lebih:

1. **Pertama kali setelah pengaturan folder** — sekali, membaca seluruh gudang.
2. **Saat komputer menganggur** (tidak ada ketikan/gerakan mouse ±1 menit, atau
   layar dikunci) — memeriksa apakah ada perubahan. Kalau tidak ada perubahan,
   hanya stempel waktu yang diperbarui. Jeda pemeriksaan makin lama makin
   panjang (2 → 4 → … → 30 menit) selama tidak ada perubahan, dan kembali cepat
   begitu ada perubahan.
3. **Tombol ↻ Sync** — satu-satunya tombol yang mengirim pesanan secara sengaja.

Pastex **tidak pernah** mengunduh saat pengguna sedang mengetik atau membuka
panel. Karena itu panel terasa instan.

## 5. Pengaturan: tiga berkas di satu folder

Pengguna hanya menekan **Config** satu kali, lalu memilih **satu folder** yang
berisi tiga berkas:

| Berkas | Isinya | Analogi |
| --- | --- | --- |
| `pastex-google-drive.json` | ID/tautan folder Drive | **Alamat gudang** |
| `service-account.json` | kunci akses dari Google | **Kunci gudang** |
| `pastex-sites.json` | daftar situs yang boleh memakai Pastex | **Daftar tamu** |

Nama berkas tidak harus tepat — Pastex mengenalinya dari **isi**. Setelah folder
dipilih, isinya **disalin ke dalam memori Pastex**, jadi folder itu tidak pernah
dibuka lagi (bahkan setelah browser ditutup). Kalau ada yang salah, Pastex
menyebutkan **semua** masalah sekaligus dan pengaturan lama tetap dipakai.

**Kunci gudang tidak pernah disimpan atau ditampilkan.** Ia hanya dipakai saat
itu juga untuk meminta izin ke Google.

## 6. Daftar tamu (situs yang diizinkan)

Pastex dipasang dengan izin luas, tetapi **hanya aktif** di situs yang
tercantum di `pastex-sites.json`. Di situs lain tombol ⚡ tidak muncul dan
perintah menyisipkan teks ditolak.

Tombol ⚡ juga memakai "tiket" berumur **30 detik** yang terikat pada halaman
itu. Tiket palsu atau kedaluwarsa ditolak, jadi halaman jahat tidak bisa
menyuruh Pastex membuka panel atau menulis ke Drive.

## 7. Peta berkas: "kalau ingin mengubah X, buka Y"

Ini bagian yang paling berguna saat meminta perbaikan.

| Kalau ingin mengubah… | Buka | Catatan |
| --- | --- | --- |
| Tampilan panel (warna, tombol, tata letak) | `extension/sidepanel.css`, `extension/sidepanel.html` | Warna diuji angkanya di `extension/test/appearance.test.js` |
| Perilaku panel (daftar, editor, cari, pilih) | `extension/sidepanel.js` | Satu berkas, ±800 baris |
| Tombol ⚡ di halaman web | `extension/content.js` (+ `content.css`) | Hanya menyisipkan teks dan minta izin |
| Petugas belakang (antrean, jadwal, izin pesan) | `extension/background.js` | Semua penyimpanan ke Drive lewat sini |
| Cara bicara ke Google Drive | `extension/drive.js` | Izin, unggah, ubah nama, Trash |
| Susunan folder & urutan | `extension/library.js` | Urutan disimpan di satu label `pastex_index` |
| Salinan cepat (cache) | `extension/cache.js` | Panel selalu dilayani dari sini |
| Antrean pesanan | `extension/queue.js` | Tahan restart browser |
| Pengaturan 3 berkas | `extension/config-source.js` (panel) dan `extension/config-cache-reader.js` (petugas) | Keduanya membaca cache yang sama |
| Daftar situs | `extension/sites.js` | Aturan ketat, gagal-tertutup |
| Paket instalasi Linux | `debian/`, `scripts/`, `.github/workflows/build-deb.yml` | Rilis lewat APT, bukan Chrome Web Store |
| Teks bantuan pengguna | `README.md`, `extension/README.md`, `cara-jalankan-get-linux-mint.md` | |

Aturan penting: **hanya petugas belakang yang boleh menulis ke Drive.** Panel
mengirim "permintaan", bukan menulis sendiri. Kalau suatu perubahan tampak
"tidak tersimpan", periksa antrean di `queue.js` dan pengiriman di
`background.js` — bukan di `sidepanel.js`.

## 8. Tes: apa yang dijaga otomatis

Ada dua "penjaga pintu" yang berjalan sebelum rilis:

| Perintah | Menjaga |
| --- | --- |
| `node extension/test/run.js` | 172 pemeriksaan perilaku: antrean, cache, Drive, panel, kontras warna, keamanan pesan, dan **tidak ada berkas mati** |
| `python3 -m pytest tests/ -q` | 68 pemeriksaan paket: versi manifest = changelog, isi `.deb`, tanda tangan APT, dokumentasi tidak menjanjikan hal yang sudah dihapus |

Penjaga "tidak ada berkas mati" (`extension/test/dead-code.test.js`) memastikan:
setiap berkas `.js` yang ikut dipaketkan benar-benar dimuat oleh panel, manifest,
atau petugas belakang; panel tidak memakai sesuatu yang tidak dimuat; dan tidak
ada penulis urutan/lipat per-item yang kembali.

## 9. Kesalahan yang mudah terjadi (dan gejalanya)

| Gejala di mata pengguna | Kemungkinan sebab | Di mana melihat |
| --- | --- | --- |
| "Drive: not connected." | folder belum dipilih, atau tiga berkas belum lengkap | Config → pesan menyebut berkas mana |
| Daftar muncul tapi isi catatan kosong | salinan menyusut karena penyimpanan penuh; baris diisi saat menganggur | `cache.js` (degradasi bertahap) |
| Perubahan belum ada di komputer lain | pesanan masih menunggu karena tab situs masih terbuka | bilah Drive menunjukkan jumlah menunggu; `↻ Sync` |
| ⚡ tidak muncul | situs tidak ada di `pastex-sites.json` | `sites.js`, lalu pilih folder lagi |
| Panel lama setelah pembaruan paket | Chrome membaca pendaftaran ekstensi saat start | keluar penuh dari Chrome, bandingkan versi |

## 10. Yang sudah dihapus dan tidak boleh dikembalikan

Beberapa lapisan lama sengaja dibuang supaya tidak ada dua cara melakukan hal
yang sama:

- **Penyimpanan folder handle (IndexedDB)** dan **pemindai berkas folder
  terpisah** — panel kini memindai folder sekali lalu menyimpan hasilnya.
- **Penulis urutan/lipat per item di Drive** — urutan dan lipatan hanya ditulis
  ke satu label `pastex_index`. Label lama tetap **dibaca** agar perpustakaan
  dari versi sebelumnya tidak berubah susunannya.
- Ekspor bantu yang tidak lagi dipanggil siapa pun (lihat entri changelog
  2.16.0 untuk daftar lengkapnya).

Kalau suatu saat fitur ini dibutuhkan lagi, tambahkan **satu** jalur baru
beserta tesnya — jangan menghidupkan kembali dua jalur sekaligus.
