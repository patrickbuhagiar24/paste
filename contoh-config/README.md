# Contoh berkas konfigurasi Pastex

## Masalah pada `contoh.json` Anda

Berkas Anda:

```json
{
	"pastex-google-drive.json": "https://drive.google.com/file/d//view?usp=sharing",
	"pastex-sites.json": "https://drive.google.com/file/d//view?usp=drive_link",
	"service-account.json": "https://drive.google.com/file/d//view?usp=drive_link"
}
```

Perhatikan `/file/d//view` — **ID berkasnya kosong** (tidak ada apa pun di antara
`d/` dan `/view`). Saya jalankan berkas ini melalui validator ekstensi yang
sebenarnya (`config-manifest.js`):

```text
DITOLAK: pastex-google-drive.json must be an https://drive.google.com/file/d/... link.
```

Penyebabnya ada di kode, `config-manifest.js` baris 32:

```js
var match = /^\/file\/d\/([A-Za-z0-9_-]+)(?:\/(?:view|preview|edit))?\/?$/.exec(url.pathname);
```

`[A-Za-z0-9_-]+` menuntut **minimal satu karakter**. Dengan ID kosong, `fileId()`
mengembalikan `""`, lalu `parse()` melempar galat. Ekstensi berhenti sebelum
mengunduh apa pun, dan konfigurasi lama Anda dipertahankan.

## Bentuk yang benar

Ganti `d//view` menjadi `d/<ID>`:

```json
{
	"pastex-google-drive.json": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing",
	"pastex-sites.json": "https://drive.google.com/file/d/1BbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=drive_link",
	"service-account.json": "https://drive.google.com/file/d/1CcCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=drive_link"
}
```

Umumnya ketiga ID berbeda, karena ketiganya berkas berbeda.

## Cara mendapat ID

1. Buka berkasnya di <https://drive.google.com>
2. Klik berkas → **Bagikan** → **Salin tautan** (atau klik kanan → *Dapatkan tautan*)
3. Tautannya berbentuk `https://drive.google.com/file/d/<ID>/view?usp=...`
4. **ID** adalah bagian di antara `d/` dan `/view` — biasanya 33 karakter

Contoh pemisahannya:

```text
https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=sharing
                                └────────── ID ──────────────────────┘
```

## Bentuk yang diterima dan ditolak

Sudah saya uji satu per satu dengan validator asli:

| Bentuk | Hasil |
| --- | --- |
| `https://drive.google.com/file/d/<ID>/view?usp=sharing` | **DITERIMA** |
| `https://drive.google.com/file/d/<ID>` (tanpa `/view`) | **DITERIMA** |
| `https://drive.google.com/open?id=<ID>` | **DITERIMA** |
| `[berkas](https://drive.google.com/file/d/<ID>/view)` (Markdown) | **DITERIMA** |
| `https://drive.google.com/file/d//view` (ID kosong) | DITOLAK |
| `http://drive.google.com/...` (bukan https) | DITOLAK |
| `https://drive.google.com/drive/folders/<ID>` (URL folder) | DITOLAK |
| `https://example.com/file/d/<ID>/view` (host lain) | DITOLAK |

Kunci yang hilang juga ditolak dengan pesan spesifik, misalnya
`The manifest is missing pastex-google-drive.json.`

## Isi ketiga berkas

`manifest.json` hanya berisi tautan. Berkas yang ditautkan harus berisi:

### `pastex-google-drive.json`

```json
{ "folder_id": "1FolderIdAbCdEfGhIjKlMnOpQrSt" }
```

Boleh juga memakai `{"folder_url": "https://drive.google.com/drive/folders/<ID>"}`.
Bila keduanya ada, `folder_id` yang dipakai (`config-source.js` baris 66–68).

### `pastex-sites.json`

```json
{ "allowed_sites": ["https://example.com", "https://*.example.org"] }
```

Hanya origin HTTP(S), tanpa path/port/kredensial, maksimal 500 entri, tanpa
wildcard global. Daftar kosong berarti **tidak ada situs** yang diizinkan.

### `service-account.json`

Unduh dari Google Cloud (IAM & Admin → Service Accounts → Keys → Add key → JSON).
Bentuk aslinya sudah benar; jangan disunting. Contoh strukturnya ada di
`service-account.contoh.json` — itu **bukan kunci asli**, hanya untuk melihat
bentuk field-nya.

## Urutan memasang

1. Siapkan ketiga berkas di Drive.
2. Atur izin tiap berkas: **Anyone with the link → Viewer**. (Lihat
   [`../izin-drive-config.md`](../izin-drive-config.md) — izin kepada service
   account tidak berpengaruh, karena berkas diunduh anonim.)
3. Bagikan **folder snippet** ke `client_email` akun layanan sebagai **Editor**.
4. Buat `manifest.json` dengan tiga tautan yang ID-nya sudah terisi.
5. Simpan manifest di komputer Anda.
6. Buka side panel Pastex → **Config** → pilih `manifest.json`.
7. Bila ada yang salah, ekstensi menampilkan galat dan **mempertahankan
   konfigurasi sebelumnya**.

## Berkas di folder ini

| Berkas | Isi |
| --- | --- |
| `manifest.json` | contoh manifest dengan ID terisi (ID palsu) |
| `pastex-google-drive.json` | contoh isi, `folder_id` |
| `pastex-sites.json` | contoh isi, `allowed_sites` |
| `service-account.contoh.json` | contoh **struktur** kunci, bukan kunci asli |

Catatan: `manifest.json` di sini memakai ID palsu, jadi **tidak bisa langsung
dipakai** — ia hanya menunjukkan bentuk yang lolos validasi. Ganti ketiga ID
dengan milik Anda.
