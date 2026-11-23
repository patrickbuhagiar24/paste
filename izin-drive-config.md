# Izin Drive untuk berkas konfigurasi Pastex

## Ringkasan satu tabel

| Objek | Siapa yang mengakses | Izin | Kenapa |
| --- | --- | --- | --- |
| **Folder snippet** | `client_email` akun layanan | **Editor** | Snippet dan label harus bisa ditulis |
| `pastex-google-drive.json` | pengunjung anonim (tautan) | **Viewer** | Isinya hanya `folder_id`; perlu dibaca sebelum ada kredensial |
| `pastex-sites.json` | pengunjung anonim (tautan) | **Viewer** | Sama, dibaca anonim |
| `service-account.json` | pengunjung anonim (tautan) | **Viewer** | Kunci itu sendiri harus diunduh dulu |
| Folder tempat config | — | apa saja | Izin folder tidak menurun ke berkas di Drive |

Kata kunci yang membedakan semuanya: **apa yang dibaca sebelum kredensial ada.**

---

## Kenapa "SA Editor" pada `pastex-google-drive.json` tidak berpengaruh

Ini urutan yang dilakukan ekstensi saat Anda memilih manifest
(`extension/config-source.js` baris 61–82):

```js
async function readConfigFrom(source, force) {
  var active = requireManifest(source);
  return memo("config", keyOf(active), !!force, async function () {
    var found = await discovery.discover(await linkedFiles(active, true));   // (1) unduh 2 berkas
    var drive = found.files[DRIVE], sa = found.files[ACCOUNT];               // (2) isi baru tersedia
    var id = drive && typeof drive.folder_id === "string" ? drive.folder_id.trim() : "";
    ...
    return { drive: validId ? { folderId: id } : null, ..., serviceAccount: validSa ? {...} : null, ... };
  });
}
```

Perhatikan dua hal:

1. **`linkedFiles(...)` dipanggil di baris sebelum `sa` ada.** Saat baris 64 berjalan
   — tempat `folder_id` dibaca dari `pastex-google-drive.json` — variabel `sa`
   baru saja diisi pada baris yang sama. Artinya pembacaan `pastex-google-drive.json`
   **selesai sebelum** kunci akun layanan tersedia untuk dipakai.
2. **Tidak ada jalur yang memakai kunci untuk mengunduh.** Kalau ada, ia harus
   berada sebelum baris 64 — dan tidak ada.

Yang benar-benar dipakai kunci itu adalah **folder snippet**, bukan berkas config:

```js
// background.js — membaca daftar isi folder memakai token dari kunci
var children = await PastexDrive.listChildren(token, command.parentId);
// drive.js — menulis snippet dan label, juga memakai token
method: "PATCH" / "POST" / "DELETE"
```

Jadi rantainya berurutan dan tidak bisa dibalik:

```text
tau­tan manifest
   → unduh pastex-google-drive.json   (anonim)
   → unduh service-account.json       (anonim)
   → unduh pastex-sites.json          (anonim)
   → baru sekarang kunci tersedia
        → tanda tangani JWT
        → tukar jadi token akses
        → baca/tulis folder snippet    ← DI SINILAH Editor dipakai
```

Memberi SA izin pada `pastex-google-drive.json` setara dengan memberi kunci
rumah kepada orang yang tidak pernah datang ke rumah itu.

## Bukti: header yang benar-benar dikirim

Saya jalankan `config-manifest.js` dengan `fetch` tiruan untuk merekam permintaan
yang dibuatnya:

```text
Header yang DIKIRIM ekstensi:
{
  "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8"
}
Ada Authorization? false
```

Tanpa `Authorization`, Google memperlakukan permintaan itu sebagai pengunjung
anonim. Izin SA tidak pernah dibaca — bukan ditolak, memang tidak dikirim.

## Konsekuensi praktis

**Kalau `pastex-google-drive.json` hanya "Anyone with the link → Editor":**
berkas itu **bisa diunduh** (Editor mencakup izin baca), jadi konfigurasi
**akan berhasil**. Tapi izin Edit-nya tidak pernah dipakai, dan itu berarti Anda
memberi kuasa tulis kepada siapa pun yang memegang tautannya — tanpa manfaat apa pun.

**Kalau `pastex-google-drive.json` hanya "Anyone with the link → Viewer":**
berhasil, dengan risiko yang lebih kecil. Ini yang dianjurkan.

**Kalau salah satu berkas hanya privat (tanpa link sharing):** gagal. Google
mengembalikan halaman login, dan ekstensi melaporkan
`A linked configuration file is not valid JSON. Check that each Drive link is
downloadable without signing in.`

## Yang perlu dipastikan

- Ketiga berkas config: **Anyone with the link → Viewer** (bukan Editor).
- **Folder snippet**: dibagikan ke `client_email` akun layanan sebagai **Editor**.
- `folder_id` di dalam `pastex-google-drive.json` harus menunjuk ke folder snippet itu.
- Izin pada folder config **tidak** menurun ke berkas di dalamnya — di Google
  Drive, tautan dibagikan per berkas. Jadi atur izin pada ketiga berkasnya
  masing-masing.

## Batasan desain

Alur berbasis tautan ini tidak bisa membuat berkas config privat. Kalau bisa
diunduh tanpa login, maka siapa pun yang memegang tautannya bisa membacanya.
Untuk `service-account.json` itu serius, karena isinya kunci privat.

Mitigasi yang realistis:

1. Pakai kunci **khusus Pastex**, jangan pakai ulang kunci proyek lain.
2. Rotasi kunci bila tautannya pernah bocor.
3. Batasi akses SA **hanya** ke folder snippet.
4. Jangan pernah menyebar tautan config di chat, forum, atau tangkapan layar.
