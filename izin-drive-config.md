# Izin Drive: siapa butuh Editor, siapa cukup Viewer

## Ringkasan

| Berkas | Untuk siapa | Izin minimum | Kenapa |
| --- | --- | --- | --- |
| `service-account.json` | **service account** (dirinya sendiri) | — *(tidak dibagikan)* | Ini kunci milik SA itu sendiri, bukan berkas yang perlu diakses |
| `pastex-sites.json` | **cuma Anda** (manusia) | **Privat** | Tidak ada pihak lain yang boleh membacanya |
| `pastex-google-drive.json` | **cuma Anda** (manusia) | **Privat** | Sama, hanya berisi ID folder |
| Folder snippet + isinya | **service account** | **Editor** | Snippet & label harus bisa ditulis |
| Folder tempat config | **cuma Anda** | **Privat** | Ekstensi mengunduhnya lewat tautan publik |

**Intinya: dari ketiga berkas config, tidak ada satu pun yang perlu dibagikan ke service account.** Ekstensi mengunduhnya lewat tautan, bukan lewat kredensial. Justru **Viewer kepada siapa pun adalah kesalahan** — lihat bagian bahaya di bawah.

---

## Kenapa

### Berkas config dibaca lewat tautan publik, bukan lewat SA

`extension/config-manifest.js` baris 45:

```js
function downloadUrl(id) {
  return "https://drive.google.com/uc?export=download&id=" + encodeURIComponent(id);
}
```

Tidak ada header `Authorization` di permintaan itu (`fetchJson`, baris 74). Artinya berkas config didownload sebagai **pengunjung anonim**. Konsekuensinya:

- Memberi izin Viewer kepada service account **tidak ada gunanya** — ekstensi tidak memakai identitas SA untuk mengunduhnya.
- Yang menentukan berhasil/gagal adalah apakah tautan itu **bisa diunduh tanpa login**.

### Folder snippet justru **wajib** Editor

`extension/drive.js` memakai scope penuh `https://www.googleapis.com/auth/drive` (baris 23) dan menulis dengan `POST`/`PATCH`/`DELETE` (baris 183, 308, 330, 353, 360, 384, 396) — untuk membuat snippet, mengganti isi, menghapus, dan menulis label `pastex_index`/`pastex_order`/`pastex_collapsed`.

Kalau folder itu hanya **Viewer**, yang berhasil hanya membaca. Menambah, mengedit, menghapus, dan mengubah urutan/lipat semuanya gagal. Yang tampak oleh Anda: daftar terbaca, tapi setiap penyimpanan gagal.

### `pastex-sites.json` punya risiko paling besar

Berkas ini berisi daftar situs tempat launcher boleh muncul. Isinya **tidak disimpan di browser** — dibaca ulang dari Drive (hanya tiga URL yang tersimpan di IndexedDB).

Artinya: **siapa pun yang bisa mengedit berkas itu bisa menambah situs ke allowlist**, dan perubahan itu berlaku pada pemeriksaan berikutnya tanpa Anda perlu menyetujui apa pun di browser. Karena itu:

- **Jangan** berikan akses edit kepada akun lain selain Anda.
- **Jangan** taruh di folder bersama (shared folder).
- **Jangan** taruh di folder snippet — folder itu dipegang SA sebagai Editor.

Kalau sudah telanjur di folder snippet: pindahkan. Alasannya bukan bug, tapi higiene — SA punya akses tulis ke folder itu, jadi kunci bocor berarti orang lain bisa mengubah allowlist Anda.

Catatan: ekstensi memvalidasi isi allowlist (`sites.js` baris 12–16: hanya origin HTTP(S), maksimal 500, tanpa wildcard global) dan **gagal-tertutup** bila kosong/rusak. Jadi penyerang tidak bisa menyuntikkan pola aneh — tapi ia tetap bisa menambahkan situs yang sah.

### `service-account.json` adalah kunci, bukan konfigurasi

Berkas ini berisi `private_key` akun layanan. Tidak ada alasan membagikannya ke siapa pun. Yang **perlu** dilakukan hanyalah menambahkan `client_email`-nya sebagai **Editor** pada **folder snippet**.

---

## Bentuk izin yang aman

```text
Google Drive Anda
├── 📁 Pastex                 ← SA: Editor    (snippet .txt + label)
│   ├── catatan.txt
│   └── ...
├── 📁 Pastex-config          ← Privat (hanya Anda)
│   ├── pastex-sites.json
│   ├── pastex-google-drive.json
│   └── service-account.json
└── 📄 manifest.json          ← Privat, berisi 3 tautan
```

Yang perlu dibagikan ke SA **hanya** folder `Pastex`.

---

## Bahaya yang perlu Anda sadari

Karena tautan config harus bisa diunduh tanpa login, **siapa pun yang memiliki tautan itu bisa membacanya**. Untuk `service-account.json` ini serius: siapa pun yang memegang tautannya memegang kunci akun layanan Anda.

README proyek menyebutnya sendiri (`extension/README.md` baris 19–22):

> *"the key must be downloadable by the browser for this link-based flow to work. If the Drive link is public, anyone with the link has your service-account private key. Use a restricted, short-lived key and rotate it immediately if the link leaks."*

Mitigasi yang masuk akal:

1. **Jangan sumbar tautan itu ke siapa pun** — tidak di chat, tidak di forum, tidak di screenshot.
2. Pakai **kunci khusus** untuk Pastex; jangan pakai ulang kunci dari proyek lain.
3. Batasi akses SA **hanya** ke folder snippet (jangan seluruh Drive).
4. **Rotasi kunci** bila tautan pernah bocor.
5. Ingat: ini adalah SA dengan scope **Drive penuh** — bukan read-only. Karena itu batasi folder yang dibagikan, jangan bagikan seluruh Drive.

Satu hal yang sering terlewat: mengaktifkan *link sharing* pada berkas config berarti berkas itu **tidak** menjadi "privat" dalam arti sebenarnya. Yang bisa Anda lakukan hanyalah menjaga tautannya. Itulah kompromi desain dari alur berbasis tautan ini.

---

## Jawaban singkat untuk pertanyaan Anda

- **`pastex-sites.json`** → **bukan Editor, bukan juga Viewer kepada SA**. Simpan **privat**, hanya Anda.
- **`pastex-google-drive.json`** → sama, privat.
- **`service-account.json`** → tidak dibagikan sama sekali.
- **Yang butuh Editor adalah folder snippet-nya**, diberikan kepada `client_email` akun layanan.

Kalau yang Anda maksud "pelihat" adalah **service account** sebagai penerima izin: untuk ketiga berkas config, jawabannya **tidak perlu sama sekali** — Viewer pun tidak. Dan untuk folder snippet, Viewer **tidak cukup**; harus Editor.
