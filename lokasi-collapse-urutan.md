# Di mana urutan daftar dan collapse disimpan di Google Drive?

## Jawaban singkat

**Tidak ada berkas.** Keduanya disimpan sebagai **label (custom file properties)** pada **folder utama (root folder)** Perpustakaan Anda — folder Google Drive yang ID-nya Anda isi di `pastex-google-drive.json`.

Jadi bukan `.json`, bukan `.txt`, bukan berkas tersembunyi. Label itu menempel pada folder, bukan pada berkas, sehingga tidak muncul sebagai item di daftar Drive dan tidak ikut terhitung sebagai snippet.

## Label yang dipakai

| Label | Menempel pada | Isi |
| --- | --- | --- |
| `pastex_index` | **folder utama** | Satu string JSON berisi **seluruh** urutan + status lipat |

Kode: `extension/drive.js` → `setIndex()`. Itu **satu-satunya** penulis urutan
dan status lipat. Label lama `pastex_order` / `pastex_collapsed` (satu per
berkas/folder) tidak lagi ditulis **maupun** dibaca.

## Bentuk isi `pastex_index`

```json
{
  "v": 2,
  "order": {
    "17Xk...rootId": ["idB", "idC", "idA"]
  },
  "collapsed": {
    "17Xk...folderId": "1",
    "17Xk...otherFolder": "0"
  }
}
```

- `v` adalah versi bentuk data. Versi lain **dibuang**, bukan ditebak.
- `order` dipetakan per **id folder induk**. Kunci `rootId` berarti isi folder utama. Folder lain memakai id folder itu sendiri.
- `collapsed` dipetakan per **id folder** dengan nilai `"1"` (terlipat) atau `"0"` (terbuka).

## Kenapa satu label, bukan per item?

Reorder dan fold berbagi satu label, sehingga:

- satu kali reorder = **satu** tulis ke Drive, bukan satu tulis per baris;
- satu kali fold/unfold = **satu** tulis;
- fold tidak pernah menghapus reorder yang belum tersimpan, karena keduanya ditulis bersamaan.

## Kalau ini perpustakaan lama

Versi lama menyimpan urutan dan status lipat **per item**. Label itu sekarang
**diabaikan**: `parseIndex()` hanya membaca `pastex_index` di folder utama, dan
bila label itu tidak ada atau versinya tidak dikenal, perpustakaan ditampilkan
dalam urutan Drive dengan semua folder terbuka. Tidak ada migrasi dan tidak ada
pembacaan per item — lihat `DEVELOPMENT.md` ("state carries a version").

Akibatnya, untuk perpustakaan yang diatur sebelum 2.19: urutan manual dan
lipatan kembali ke default, sedangkan **isi snippet tidak berubah sama sekali**.

## Di mana melihatnya sendiri di Drive

1. Buka folder perpustakaan Anda di <https://drive.google.com>.
2. Klik kanan folder itu → **Detail** (atau panel info).
3. Bagian **Properti** / **Properties** — di situ ada `pastex_index` berisi JSON di atas.

Lewat API juga bisa:

```
GET https://www.googleapis.com/drive/v3/files/<ROOT_FOLDER_ID>?fields=properties
```

## Catatan penting

- Label ini adalah **metadata**, bukan isi berkas, jadi Drive tidak menghitungnya sebagai berkas dan tidak muncul di pencarian berkas.
- Karena menempel di folder utama, **mengganti folder** di konfigurasi berarti memakai set urutan/lipat yang berbeda — yang lama tetap utuh di foldernya masing-masing.
- Nilai `"1"`/`"0"` dipakai (bukan `true`/`false`) karena properti Drive hanya menyimpan string.
