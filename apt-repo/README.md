# Repo APT Paste Teks

Folder ini berisi hasil build repo APT (dibuat oleh `scripts/setup-apt-repo.sh`
secara lokal, atau oleh CI `.github/workflows/build-deb.yml` di GitHub).
Isinya: `*.deb`, `Packages`, `Packages.gz`, `Release` (+ `Release.gpg`/`InRelease`
bila ditandatangani GPG).

---

## Cara terbitkan ke GitHub Pages (langkah demi langkah)

Alurnya: **push ke `main` → GitHub Actions membangun `.deb` + `Packages`/`Release`
→ hasilnya di-push ke branch `gh-pages` → GitHub Pages menyajikan branch itu
sebagai situs HTTP** yang bisa dibaca APT.

### Langkah 1 — Aktifkan GitHub Pages (sekali saja)

1. Buka repo di GitHub → **Settings → Pages**.
2. Pada **Build and deployment → Source**, pilih **Deploy from a branch**.
3. Pada **Branch**, pilih **`gh-pages`** dan folder **`/ (root)`**, lalu klik **Save**.
4. Tunggu ±1 menit. Situs akan tersedia di
   `https://patrickbuhagiar24.github.io/paste/`.

> Tanpa langkah ini, branch `gh-pages` tidak akan disajikan sebagai situs,
> dan APT tidak bisa membaca repo-nya.

### Langkah 2 — Pastikan kode ada di branch `main`

Workflow hanya berjalan untuk push ke **`main`** (atau dijalankan manual).
Gabungkan branch kerja Anda ke `main` (mis. lewat Pull Request), lalu push.

### Langkah 3 — Jalankan workflow

- **Otomatis**: setiap push ke `main` menjalankan workflow *Build .deb & APT repo*.
- **Manual**: tab **Actions → Build .deb & APT repo → Run workflow**.

Workflow akan: menguji → membangun `.deb` → membuat `Packages`/`Release` →
menyalinnya ke folder `apt/` di branch `gh-pages` → push. GitHub Pages lalu
menyajikan file-file itu di:

```
https://patrickbuhagiar24.github.io/paste/apt/
├── paste-teks_1.0.0-1_all.deb
├── Packages / Packages.gz
├── Release (+ Release.gpg / InRelease bila ditandatangani)
└── index.html
```

Verifikasi: buka `https://patrickbuhagiar24.github.io/paste/apt/Packages` di
browser — harus tampil metadata paket `paste-teks`.

### Langkah 4 — Pasang dari APT di Ubuntu

```bash
echo "deb [trusted=yes] https://patrickbuhagiar24.github.io/paste/apt/ ./" \
  | sudo tee /etc/apt/sources.list.d/paste-teks.list
sudo apt update
sudo apt install paste-teks
```

Setelah itu `sudo apt upgrade` akan otomatis memperbarui Paste Teks setiap
ada rilis baru di `main`.

### (Opsional, disarankan) Tandatangani dengan GPG

Tanpa tanda tangan, pengguna harus memakai `[trusted=yes]`. Untuk keamanan
lebih baik (`[signed-by=...]`), aktifkan penandatanganan di CI:

1. Buat kunci GPG (sekali, di mesin Anda):
   ```bash
   gpg --quick-gen-key "Paste Teks <patrickbuhagiar24@users.noreply.github.com>" ed25519 sign never
   gpg --armor --export-secret-keys "Paste Teks" > paste-teks-secret.asc
   gpg --armor --export "Paste Teks" > paste-teks.gpg
   ```
2. Di GitHub: **Settings → Secrets and variables → Actions → New repository secret**:
   - `GPG_PRIVATE_KEY` = isi `paste-teks-secret.asc`
   - `GPG_PASSPHRASE` = passphrase kunci (kosongkan bila tanpa passphrase)
3. Jalankan ulang workflow. CI akan membuat `Release.gpg`, `InRelease`, dan
   `paste-teks.gpg` (kunci publik) di folder `apt/`.
4. Di Ubuntu, pengguna memasang kunci publik lalu memakai `signed-by`:
   ```bash
   curl -fsSL https://patrickbuhagiar24.github.io/paste/apt/paste-teks.gpg \
     | sudo gpg --dearmor -o /usr/share/keyrings/paste-teks.gpg
   echo "deb [signed-by=/usr/share/keyrings/paste-teks.gpg] https://patrickbuhagiar24.github.io/paste/apt/ ./" \
     | sudo tee /etc/apt/sources.list.d/paste-teks.list
   sudo apt update && sudo apt install paste-teks
   ```

---

## Pakai repo lokal (file://)

```bash
./scripts/setup-apt-repo.sh
echo "deb [trusted=yes] file://$(pwd)/apt-repo/dist ./" | sudo tee /etc/apt/sources.list.d/paste-teks.list
sudo apt update
sudo apt install paste-teks
```

## Pakai repo lokal bertanda tangan

```bash
GPG_KEY="KunciAnda" ./scripts/setup-apt-repo.sh
gpg --armor --export KunciAnda | sudo gpg --dearmor -o /usr/share/keyrings/paste-teks.gpg
echo "deb [signed-by=/usr/share/keyrings/paste-teks.gpg] file://$(pwd)/apt-repo/dist ./" | sudo tee /etc/apt/sources.list.d/paste-teks.list
sudo apt update && sudo apt install paste-teks
```
