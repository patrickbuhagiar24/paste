# Pastex — sorotan warna dan analisis proyek

Dokumen ini mendampingi perubahan pada branch `arena/6072f3e1-paste`.
Bagian 1 menjelaskan perubahan visual, bagian 2 berisi analisis proyek dan
saran perbaikan yang saya temukan tetapi **belum** saya ubah (agar perubahan
di branch ini tetap fokus pada tampilan).

Gambar pratinjau hasil desain baru ada di `.arena/preview/panel.png`
(panel terang & gelap) dan `.arena/preview/editor.png` (dialog editor).

---

## 1. Sorotan warna: daftar dan tombol

### Masalah pada desain lama

Palet lama sangat pucat, sehingga daftar dan tombol nyaris tidak terlihat
sebagai elemen yang bisa diklik. Angka di bawah adalah rasio kontras WCAG
yang saya hitung dari nilai lama:

| Pasangan | Rasio lama | Status |
| --- | --- | --- |
| Baris terpilih `#d9e9fa` vs latar `#fff` | **1,24:1** | nyaris tak terlihat |
| Baris hover `#eef4fc` vs latar `#fff` | **1,11:1** | nyaris tak terlihat |
| Baris terpilih vs hover | **1,12:1** | dua keadaan tak bisa dibedakan |
| Garis tepi tombol `#c6d8ec` vs latar `#fff` | **1,45:1** | batas tombol hilang (min. 3:1) |
| Ikon aksi baris (opasitas 0,5) | **2,48:1** | sulit dibaca |
| Teks status ok/error | sama saja | tanpa warna hasil |

Semua tombol — Collapse all, Select, Refresh, Config, Text, Folder, Save,
Cancel, Delete — juga memakai satu gaya yang sama, sehingga **tidak ada
hierarki**: "Save" dan "Delete" terlihat identik.

### Yang berubah sekarang

**Palet diperiksa dengan angka, bukan perasaan.** Semua pasangan teks
mencapai WCAG AA, sebagian besar AAA:

| Pasangan | Rasio baru | Status |
| --- | --- | --- |
| Teks utama pada latar | 17,85:1 | AAA |
| Label baris pada baris idle / hover / terpilih | 16,00 / 14,56 / 11,54:1 | AAA |
| Teks redup (status Drive, label editor) | 7,19–7,58:1 | AAA |
| Putih pada tombol primer (atas–bawah gradien) | 6,71–11,07:1 | AAA |
| Putih pada tombol bahaya | 5,38–6,47:1 | AA |
| Border tombol sekunder | 3,77–3,97:1 | ≥ 3:1 (non-teks) |
| Penanda fokus | 6,70:1 | jelas |

**Daftar sekarang punya tiga keadaan yang jelas berbeda:**

- baris idle: permukaan lembut `#edf2f9` dengan sudut membulat, jadi daftar
  terbaca sebagai deretan item, bukan teks bebas;
- hover: `#d9e7fa` (selisih 1,20:1 dari idle — selalu bisa dibedakan);
- terpilih: `#b8d2f5` (1,39:1 dari idle, 1,26:1 dari hover) **plus** garis
  aksen biru tua di tepi kiri (7,37:1) dan label dipertebal. Jadi status
  terpilih tidak bergantung pada warna saja.

**Tombol sekarang punya hierarki makna:**

- **Primer** (gradien indigo, teks putih): `＋ Text`, `＋ Folder`,
  `＋ New snippet`, `Save` — aksi yang aman dan menjadi langkah berikutnya;
- **Bahaya** (gradien merah): `🗑 Delete` dan `🗑 Delete` di bilah seleksi;
- **Sekunder** (garis tepi kuat 3,9:1): Collapse all, Select, Refresh, Config,
  Cancel dan aksi baris (rename/edit/delete);
- tombol primer/bahaya yang **nonaktif** kehilangan isian mencolok dan
  kembali menjadi garis tepi biasa, jadi aksi yang tak bisa diklik tidak
  "berteriak" lebih keras daripada yang bisa;
- `Refresh` otomatis menjadi primer saat Drive tidak tersedia, karena itulah
  satu-satunya aksi yang menolong pada keadaan itu.

**Sorotan di tempat lain:**

- **Titik status Drive** (hijau siap / biru memuat / merah gagal) di samping
  teks statusnya — warna tidak pernah menjadi satu-satunya sinyal, teksnya
  tetap ada;
- **Garis status** di header diberi warna hasil (hijau berhasil, merah gagal)
  dan dipertebal;
- **Hitungan seleksi** berubah menjadi chip merah penuh ("2 selected") ketika
  ada item terpilih, sehingga bilah bawah langsung terlihat;
- **Aksi baris** (✎ 📝 🗑) muncul saat hover, pada baris terpilih, **dan saat
  fokus keyboard** (`:focus-within`) — sebelumnya tidak muncul untuk pengguna
  keyboard sama sekali. Tombol delete berubah merah saat ditunjuk.
- **Tema gelap** mengikuti preferensi sistem (`prefers-color-scheme`), dengan
  kontras yang juga diuji (mis. teks terpilih 7,34:1).
- **`prefers-contrast: more`** mempertebal border dan memberi bingkai hitam
  pada baris terpilih; **`prefers-reduced-motion`** mematikan semua transisi.

**Konsistensi:** peluncur ⚡ di halaman (`content.js`) sekarang memakai
indigo yang sama dengan panel (`assets/launcher-preview.html` ikut
diperbarui), sehingga produk terasa satu sistem.

### Pengujian

`extension/test/appearance.test.js` (baru, 3 kasus) menghitung kontras dari
CSS asli dan gagal bila ada pasangan teks turun di bawah AA, border di bawah
3:1, baris terpilih terlalu mirip dengan idle/hover, atau aturan CSS untuk
state yang dikirim `sidepanel.js` (siap/memuat/gagal, ok/error, terpilih)
hilang. Jadi regresi warna akan tertangkap otomatis.

| Pemeriksaan | Sebelum | Sesudah |
| --- | --- | --- |
| `node extension/test/run.js` | 119 lulus | **122 lulus** |
| `python3 -m pytest tests/ -q` | 68 lulus | **68 lulus** |

Versi dinaikkan ke **2.11.0** (manifest + `debian/changelog`) sesuai aturan
workflow "Check version alignment".

---

## 1b. Ronde kedua: kursor, tombol ikon, dan cache penuh

### Sorotan kursor (daftar & tombol)

| Keadaan | Sebelum | Sesudah |
| --- | --- | --- |
| Baris hover vs idle | 1,11:1 | **1,18:1** + garis tepi aksen 8,58:1 |
| Baris terpilih vs hover | 1,12:1 | **1,28:1** |
| Cincin fokus tombol | 2px `#1d4ed8` | **3px + gap**, tombol primer/bahaya pakai `--focus-strong` (12,79:1 terhadap permukaan) |
| Kedalaman baris bersarang | hanya indentasi | **garis pemandu** di kiri anak folder |
| Label baris saat ditunjuk | — | **garis bawah halus** sebagai isyarat "bisa diklik" |

Di tema gelap angka yang sama juga dijaga (hover vs idle 1,30:1, terpilih vs
hover 1,41:1, aksen 9,95:1). Ikon aksi baris kini memakai border
`--border-strong` (bukan `--border` yang pucat) dan tombol delete berubah
merah hanya saat ditunjuk. Tombol yang nonaktif dibuat abu penuh
(`grayscale`) supaya emoji/ikon tidak lagi terlihat aktif.

### Tombol ikon ⚡ di halaman: buka **dan** tutup

`sidePanel.close()` baru ada sejak **Chrome 141**, sedangkan `minimum_chrome_version`
proyek ini 116 — jadi penutupan harus dideteksi, bukan diasumsikan.

- Worker mengirim `toggle: true/false` bersama capability; `content.js`
  memakai **⚡** (buka) atau **×** (tutup) sesuai keadaan.
- Panel mengumumkan dirinya terbuka/tertutup (`pastex-panel-state`), sehingga
  ikon tahu kapan harus berubah tanpa menebak.
- Penutupan memakai **capability yang sama** dengan pembukaan (token 30 detik,
  terikat URL tab + revisi allowlist); token palsu/kedaluwarsa ditolak, dan
  halaman di luar allowlist tidak bisa menutup panel.
- Panel sendiri juga mendapat tombol **✕ Close**, hanya muncul bila
  `chrome.sidePanel.close` tersedia (feature-detect, jadi Chrome 116 tetap aman).

### Cache: seluruh daftar folder + teks disimpan, tidak ada sinkron di depan klik

Penyebab lambat yang saya temukan:

1. **Folder yang terlipat tidak pernah di-cache.** Cache mengirim
   `deferCollapsed: true`, dan isi folder terlipat tidak diunduh sampai
   dibuka — jadi membuka folder "dingin" = menunggu Drive.
2. **Tidak ada batas kuota.** `storage.local` dibatasi (±10 MB tanpa
   `unlimitedStorage`), dan kegagalan tulis ditelan diam-diam (`catch {}`).
   Untuk pustaka besar, cache lokal bisa hilang tanpa jejak.
3. **Pengecekan latar selalu melakukan listing penuh** walau tidak ada
   perubahan.

Perbaikannya:

- **Snapshot lengkap**: cache meminta `deferCollapsed: false`, jadi teks di
  folder terlipat ikut disimpan. Panel membuka semuanya dari cache tanpa
  memanggil Drive di jalur klik.
- **Degradasi bertahap saat kuota sempit**: (1) snapshot lengkap, (2) snapshot
  dengan tiap body disimpan sekali, (3) hanya snapshot terbaru. Snapshot
  lengkap **tidak** ditimpa versi yang lebih kurus; kalau tetap tidak muat,
  kembali ke memori sesi worker seperti sebelumnya.
- **Pelaporan + pengisian**: hasil cache melaporkan `pending` (baris yang
  body-nya belum tersimpan). Panel tetap menampilkan daftar seketika, lalu
  worker mengisi baris itu **tanpa listing Drive** (`library.fillMissing`),
  menyimpan ulang snapshot, dan panel repaint sekali.
- **`verify()` metadata-only**: pengecekan latar membandingkan signature
  folder/item + label indeks tanpa mengunduh satu body; hanya bila ada
  perbedaan ia jatuh ke listing penuh. Pustaka yang tidak berubah jadi jauh
  lebih murah, dan panel tidak pernah "menunggu sinkron" karena render selalu
  dari cache lebih dulu.

Yang **tidak** berubah (disengaja): Drive tetap sumber kebenaran; tulisan
(create/edit/delete/reorder) tetap langsung ke Drive dan cache diinvalidasi
sebelum & sesudah; tidak ada antrean offline. Artinya "taruh sepenuhnya pada
cache" saya artikan sebagai **semua data tersedia dari cache untuk dibaca dan
dibuka**, bukan mengganti Drive sebagai sumber kebenaran.

### Ringkasan pengujian ronde kedua

| Suite | Hasil |
| --- | --- |
| `node extension/test/run.js` | **133 lulus** (dari 122) |
| `pytest tests/ -q` | **68 lulus** |
| `stylelint` | bersih |

Uji baru mencakup: snapshot menyimpan teks folder terlipat, buka-dari-cache
tanpa download/listing, `verify()` tanpa body, pelaporan & pengisian baris
yang hilang, kuota sempit tidak menghapus snapshot yang berguna, capability
tutup (dan penolakannya), serta tombol Close yang hanya muncul bila didukung.

---

## 1c. Ronde ketiga: baca dari cache, tulis saat tab ditinggalkan

### Tombol ikon toolbar: sekarang benar-benar toggle

Saya memeriksa repositori **yousub** (`patrickbuhagiar24/youtube`). Tombol
ikonnya **bukan** `action.onClicked` manual, melainkan satu baris di service
worker:

```js
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
```

Dengan itu **Chrome sendiri** yang menangani klik: buka panel pada klik
pertama, tutup pada klik berikutnya (Chrome 141+). Pastex sebelumnya memakai
`chrome.action.onClicked` yang hanya bisa membuka. Perubahan:

- `action.onClicked` dihapus, `setPanelBehavior` dipasang saat worker start.
- Tidak ada listener yang merebut klik, jadi perilaku toggle milik Chrome utuh.
- Ini juga menghapus kebutuhan akan tombol **Close** di panel: permintaan Anda
  untuk menghapusnya sejalan dengan mekanisme ini. Tombol ✕ Close di panel
  dihapus, begitu pula deteksi `sidePanel.close()` di panel.

Ikon ⚡ di dalam halaman tetap toggle buka/tutup seperti ronde sebelumnya
(capability 30 detik yang sama).

### Sinkronisasi: sekali saat konfigurasi dimuat, sisanya cache

Sebelumnya `get()` bisa memicu listing Drive dari klik panel (jalur
`force`, `scheduleStaleRefresh`, dan snapshot yang hilang). Sekarang:

| Kejadian | Sebelum | Sesudah |
| --- | --- | --- |
| Buka panel | bisa listing bila snapshot basi | **selalu dari cache**, nol request |
| Refresh manual | listing penuh | **baca ulang cache** (penulisan antrean tetap sinkron) |
| Konfigurasi baru dimuat | listing berikutnya | **satu full sync**, lalu cache |
| Pemeriksaan latar | listing metadata saat idle | tetap, dan satu-satunya jalan ke Drive saat menganggur |
| Edit di situs | tulis langsung ke Drive | **tulis ke cache + antrean**, flush saat tab ditutup/disembunyikan |

### Antrean tulis yang tahan restart

Antrean disimpan di `chrome.storage.local` (`extension/queue.js`), sehingga:

- Browser yang ditutup sebelum sempat flush **tidak kehilangan edit**: antrean
  dikirim saat start berikutnya.
- Satu edit = satu entri. Baris baru memakai id lokal (`local-…`) sampai
  unggahan selesai; setelah flush, satu pembacaan penuh mengganti id lokal
  dengan id Drive.
- Indeks (urutan + status lipat) diantrekan sebagai **satu** entri `order` dan
  ditulis dalam satu request, bukan satu per baris.
- Kegagalan sementara (offline, kuota) → entri dipertahankan. Kegagalan
  permanen (HTTP 400/403/404/410, "already exists", "not found") → entri
  dibuang dari antrean **dan** barisnya dibuang dari cache, supaya tidak
  dicoba selamanya dan tidak menampilkan baris yang tidak ada di Drive.
- Drive bar menampilkan berapa perubahan yang masih menunggu.

Catatan jujur: karena Drive tetap sumber kebenaran, flush diakhiri satu
pembacaan penuh. Artinya "sinkronkan semuanya saat tab ditutup" benar-benar
terjadi, tetapi biayanya satu listing penuh per flush (bukan per edit).

### Ringkasan pengujian ronde ketiga

| Suite | Hasil |
| --- | --- |
| `node extension/test/run.js` | **149 lulus** (dari 133) |
| `pytest tests/ -q` | **68 lulus** |

Uji baru: perilaku edit lokal (create/edit/rename/reorder/collapse/delete),
id lokal dan remap, antrean yang bertahan setelah restart worker, `indexOf`
yang melewati baris yang belum ada di Drive, toggle toolbar lewat
`setPanelBehavior`, create di situs yang tidak menyentuh Drive, flush yang
menulis ke Drive lalu memberi baris id Drive-nya, serta penolakan `flush-now`
dari halaman yang bukan situs diizinkan/bukan frame utama.

---

## 2. Analisis proyek dan saran perbaikan

### Yang sudah bagus (dan sebaiknya dipertahankan)

1. **Arsitektur data yang hemat.** Isi snippet di-cache berdasarkan SHA-256
   konten (`hash.js`, `library.js`, `cache.js`), sehingga file yang tidak
   berubah tidak pernah diunduh ulang; urutan dan status lipat disimpan di
   satu label `pastex_index` di folder root, jadi reorder = satu tulisan ke
   Drive, bukan satu per item. Ini keputusan desain yang kuat.
2. **Fail-closed security.** Allowlist situs menolak konfigurasi kosong/rusak;
   manifest hanya menerima tautan HTTPS `drive.google.com/file/d/…`;
   `content.js` memverifikasi `sender.id`; panel memakai capability token
   berumur 30 detik yang terikat ke URL tab; CSP MV3 tanpa remote code;
   action di workflow dipin ke commit SHA. Dokumentasi keamanannya
   (`SECURITY.md`, peringatan service-account) jujur, tidak menjual aman
   yang tidak aman.
3. **Disiplin uji.** 190 pengujian lintas JS dan Python, runner JS tanpa
   dependensi, dan banyak asersi yang mengunci *perilaku* (mis. "insertion
   gagal tidak boleh diam-diam menyalin ke clipboard"), bukan hanya sintaks.
4. **Dokumentasi yang dalam.** README menjelaskan *mengapa*, bukan hanya
   cara pakai.

### Saran perbaikan (belum diubah)

**P1 — Teknis / risiko nyata**

1. **Kunci service account terekspos lewat tautan.** Model konfigurasi
   sekarang mengharuskan `service-account.json` dapat diunduh browser, jadi
   siapa pun yang punya tautannya punya private key tersebut (README sudah
   memperingatkan). Ini menempatkan Pastex di atas fondasi yang rapuh untuk
   data pengguna. Saran: dukung **OAuth user flow + `drive.file` scope**
   sebagai jalur utama (akses hanya ke file yang dibuat app, kunci tidak
   pernah didistribusikan), dan pertahankan service account hanya sebagai
   opsi lanjutan. Ini menghapus kelas risiko terbesar di proyek.
2. **`host_permissions` terlalu luas** (`http://*/*`, `https://*/*`) hanya
   untuk mendukung pendaftaran script dinamis. Bisa dipersempit dengan
   `optional_host_permissions` yang diminta per situs saat pengguna memilih
   allowlist, sehingga izin yang tidak dipakai tidak terus dipegang.
3. **`innerHTML` di `sidepanel.js`** (pesan "No snippets match…"). Datanya
   konstanta sehingga aman hari ini, tetapi sebaiknya diganti
   `textContent`/node DOM agar tidak menjadi contoh buruk yang ditiru
   kemudian.

**P2 — Aksesibilitas (lanjutan dari pekerjaan warna ini)**

4. **Pohon tidak mengumumkan keadaan ke pembaca layar**: baris belum punya
   `aria-level`/`aria-expanded` untuk folder, dan `#tree` belum memakai
   `aria-activedescendant`. Pemilihan baris juga memindahkan fokus visual
   (CSS `.selected`) tetapi tidak fokus DOM, jadi Tab memerlukan banyak
   langkah. Saran: tambahkan `aria-level={depth+1}`,
   `aria-expanded` pada folder, dan navigasi ↑/↓ ala treeview.
5. **Dialog editor belum mengurung fokus.** (masih terbuka) `role="dialog"` dan
   `aria-modal` sudah ada, tetapi fokus tidak di-trap dan tidak
   dikembalikan ke pemicu saat ditutup; tombol Escape sengaja tidak dipakai
   (ada test yang melarangnya untuk picker), tetapi untuk dialog editor
   Escape adalah kebiasaan pengguna yang wajar.
6. **`confirm()` bawaan browser** untuk hapus. Fungsional, tetapi tidak bisa
   diberi gaya dan di beberapa konfigurasi memblokir render. Dialog
   konfirmasi di dalam panel akan lebih konsisten dan lebih mudah diuji.

**P3 — Kualitas kode / pemeliharaan**

7. **CSS baru belum ada test visual otomatis.** Kandungan CSS diuji angkanya,
   tetapi bukan render sesungguhnya (tata letak, pemotongan teks panjang).
   Menambah screenshot test berbasis Playwright di CI akan menutup celah ini.
8. **`setStatus` kini tahu "nada" hasil.** Logika status masih tersebar di
   banyak tempat; bila tumbuh lagi, pertimbangkan satu helper
   `report(kind, message)` agar tidak ada pemanggilan yang lupa memberi nada.
9. **`config-reader.js` hanya 15 baris** — masih wajar, tetapi lapisan
   offscreen + handle-store + config-source berarti tiga jalur yang harus
   dijaga konsisten. Dokumentasikan diagram alir konfigurasi singkat di
   README extension sudah cukup.
10. **`debian/changelog` naik versi setiap perubahan UI** — bagus untuk
    rilis, tetapi pertimbangkan entri "Unreleased" di `CHANGELOG.md`
    terpisah agar commit kecil tidak memaksa bump versi.

**Catatan operasional**

11. Menjalankan `./scripts/build-deb.sh` butuh `dpkg-deb`/`openssl` di mesin
    build; di sandbox ini tidak tersedia sehingga verifikasi build `.deb`
    penuh belum dijalankan — jalur yang diuji adalah unit test JS + pytest
    (termasuk packing CRX yang di-skip bila `openssl` tak ada).
12. CI menjalankan `node extension/test/run.js` dan `pytest`; setelah
    `appearance.test.js` ditambahkan, tidak ada perubahan konfigurasi CI yang
    diperlukan karena pemanggilan lewat `run.js` sudah otomatis mencakupnya.

---

## Ringkasan berkas yang berubah

| Berkas | Perubahan |
| --- | --- |
| `extension/sidepanel.css` | ditulis ulang: variabel tema, tombol primer/bahaya/sekunder, baris idle/hover/terpilih, titik status, chip seleksi, tema gelap, prefers-contrast, reduced-motion |
| `extension/sidepanel.html` | kelas `primary`/`danger` pada tombol yang tepat |
| `extension/sidepanel.js` | `setStatus(text, tone)`, `data-kind` baris, state Drive, chip seleksi, delete berwarna saat hover, Refresh primer saat Drive mati |
| `extension/content.js`, `assets/launcher-preview.html` | peluncur ⚡ diselaraskan ke indigo panel |
| `extension/test/appearance.test.js` | **baru** — uji kontras & keadaan |
| `extension/test/run.js`, `extension/test/sidepanel.test.js` | daftarkan uji baru; stub DOM mengenal `removeAttribute` |
| `extension/library.js` | `deferCollapsed`, jalur `signaturesOnly`, `verifyTree`, `fillMissing` |
| `extension/cache.js` | cache seluruh pustaka, degradasi kuota, `pending`, `fill()`, `verify()` |
| `extension/background.js` | cek latar metadata-only, relay status panel, isi-cache, toggle buka/tutup |
| `extension/content.js` | ikon ⚡/× (toggle buka-tutup, capability sama) |
| `extension/sidepanel.js`, `.html` | tombol ✕ Close, pengisian baris, mikro-tugas fill |
| `extension/queue.js` | **baru**: edit lokal, id lokal & remap, indeks, antrean tahan restart |
| `extension/test/*` | uji cache lengkap, kuota, verify, toggle, antrean & flush, tampilan |
| `extension/README.md`, `debian/changelog`, `extension/manifest.json` | dokumentasi, entri 2.12.0, versi |
