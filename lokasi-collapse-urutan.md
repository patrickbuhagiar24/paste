# Di mana urutan daftar dan collapse disimpan di Google Drive?

## Jawaban singkat

**Tidak ada berkas.** Keduanya disimpan sebagai **label (custom file properties)** pada **folder utama (root folder)** Perpustakaan Anda — folder Google Drive yang ID-nya Anda isi di `pastex-google-drive.json`.

Jadi bukan `.json`, bukan `.txt`, bukan berkas tersembunyi. Label itu menempel pada folder, bukan pada berkas, sehingga tidak muncul sebagai item di daftar Drive dan tidak ikut terhitung sebagai snippet.

## Label yang dipakai

| Label | Menempel pada | Isi |
| --- | --- | --- |
| `pastex_index` | **folder utama** | Satu string JSON berisi **seluruh** urutan + status lipat |
| `pastex_order` | tiap berkas (cara lama) | Angka urutan, mis. `"3"` |
| `pastex_collapsed` | tiap folder (cara lama) | `"1"` atau `"0"` |

Kode: `extension/drive.js` → `setIndex()`, `setOrder()`, `setCollapsed()`.

## Bentuk isi `pastex_index`

```json
{
  "order": {
    "17Xk...rootId": ["idB", "idC", "idA"]
  },
  "collapsed": {
    "17Xk...folderId": "1",
    "17Xk...otherFolder": "0"
  }
}
```

- `order` dipetakan per **id folder induk**. Kunci `rootId` berarti isi folder utama. Folder lain memakai id folder itu sendiri.
- `collapsed` dipetakan per **id folder** dengan nilai `"1"` (terlipat) atau `"0"` (terbuka).

## Kenapa satu label, bukan per item?

Reorder dan fold berbagi satu label, sehingga:

- satu kali reorder = **satu** tulis ke Drive, bukan satu tulis per baris;
- satu kali fold/unfold = **satu** tulis;
- fold tidak pernah menghapus reorder yang belum tersimpan, karena keduanya ditulis bersamaan.

## Kalau ini perpustakaan lama

Versi lama menyimpan urutan dan status lipat **per item** (`pastex_order` pada tiap berkas, `pastex_collapsed` pada tiap folder). Label-label itu **masih dibaca** saat perpustakaan dimuat, tetapi **label `pastex_index` di folder utama yang menang** bila keduanya ada. Kode: `extension/library.js` → `parseIndex()`.

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
