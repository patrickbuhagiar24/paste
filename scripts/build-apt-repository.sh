#!/usr/bin/env bash
# Build a standard APT archive for the architecture-independent Pastex package.
#
# Usage: build-apt-repository.sh PACKAGE_DIRECTORY OUTPUT_DIRECTORY [SUITE]
#
# Layout (mirrors the repository used by patrickbuhagiar24/youtube):
#   index.html
#   pastex.sources              (only with PASSTEX_SIGNED=1)
#   pastex-unsigned.sources     (only when publishing unsigned)
#   pool/main/p/pastex/*.deb
#   dists/<suite>/Release
#   dists/<suite>/main/binary-all/Packages(.gz)
#
# Signing is done afterwards by scripts/sign-apt-repository.sh, which adds
# InRelease, Release.gpg and the public keyrings to the same output directory.
set -euo pipefail
export LC_ALL=C
cd "$(dirname "$0")/.."

# Reproducible metadata. The Release file is dated from SOURCE_DATE_EPOCH, and
# without it "now" would make two builds of the same commit differ. This script
# therefore derives the same value build-deb.sh does (the last commit time),
# instead of depending on the caller remembering to export it. A release
# tarball with no .git falls back to a fixed date, which beats "now" as well.
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
  SOURCE_DATE_EPOCH="$(git log -1 --format=%ct 2>/dev/null || true)"
  SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-315532800}"
fi
case "$SOURCE_DATE_EPOCH" in
  ''|*[!0-9]*) echo "error: SOURCE_DATE_EPOCH must be a unix timestamp" >&2; exit 2 ;;
esac
export SOURCE_DATE_EPOCH

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 PACKAGE_DIRECTORY OUTPUT_DIRECTORY [SUITE]" >&2
  exit 2
fi
package_dir=$(realpath -m "$1")
output_dir=$(realpath -m "$2")
suite=${3:-stable}
if [[ ! "$suite" =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo 'Invalid APT suite name.' >&2
  exit 2
fi
if [[ "$output_dir" == "$package_dir" || "$output_dir" == "$package_dir"/* ]]; then
  echo 'The repository output must be outside the input package directory.' >&2
  exit 2
fi
command -v dpkg-scanpackages >/dev/null || { echo 'Install dpkg-dev first.' >&2; exit 1; }
command -v dpkg-deb >/dev/null || { echo 'Install dpkg first.' >&2; exit 1; }

shopt -s nullglob
packages=("$package_dir"/*.deb)
if [[ ${#packages[@]} -eq 0 ]]; then
  echo "No .deb files found in $package_dir" >&2
  exit 1
fi
for package in "${packages[@]}"; do
  architecture=$(dpkg-deb --field "$package" Architecture)
  [[ "$architecture" == all ]] || {
    echo "Only Architecture: all packages are supported: $package" >&2
    exit 1
  }
done

script_dir=$(cd "$(dirname "$0")" && pwd)
staging=$(mktemp -d "${TMPDIR:-/tmp}/pastex-apt.XXXXXX")
trap 'rm -rf "$staging"' EXIT
pool="$staging/pool/main/p/pastex"
dist="$staging/dists/$suite"
mkdir -p "$pool" "$dist/main/binary-all"

# One version in the pool, on purpose. APT only ever installs one version of an
# architecture-independent package, so publishing older builds would keep dead
# files online forever and let a user pin a version the changelog has already
# moved past. The rule is stated in README.md: one name, one path.
versions=()
for package in "${packages[@]}"; do
  versions+=("$(dpkg-deb --field "$package" Version)")
done
mapfile -t versions < <(printf '%s\n' "${versions[@]}" | sort -u)
if [[ ${#versions[@]} -gt 1 ]]; then
  echo "Publish one version at a time; found: ${versions[*]}" >&2
  exit 1
fi
version=${versions[0]}
cp "$package_dir"/*_"$version"_*.deb "$pool/"
chmod 0644 "$pool"/*.deb

# Supply the archive override entries from each package's own control fields.
: > "$staging/override"
for package in "${packages[@]}"; do
  name=$(dpkg-deb --field "$package" Package)
  priority=$(dpkg-deb --field "$package" Priority)
  section=$(dpkg-deb --field "$package" Section)
  printf '%s %s %s\n' "$name" "${priority:-optional}" "${section:-misc}" >>"$staging/override"
done
(
  cd "$staging"
  dpkg-scanpackages --multiversion pool override >"dists/$suite/main/binary-all/Packages"
)
gzip -n -9 -c "$dist/main/binary-all/Packages" >"$dist/main/binary-all/Packages.gz"
# The override list is a build input, not part of the published archive.
rm -f "$staging/override"

python3 "$script_dir/write-apt-release.py" "$dist" "$suite"

# Sources templates: deb822 sources ship with the archive so users install the
# repository with a single downloaded file instead of a hand-written line.
if [[ "${PASSTEX_SIGNED:-0}" == "1" ]]; then
  install -m 0644 "$script_dir/../packaging/pastex.sources" "$staging/pastex.sources"
else
  install -m 0644 "$script_dir/../packaging/pastex-unsigned.sources" "$staging/pastex-unsigned.sources"
fi
install -m 0644 "$script_dir/../packaging/apt-index.html" "$staging/index.html"
# Keep the index truthful: only document files this archive actually publishes.
if [[ "${PASSTEX_SIGNED:-0}" == "1" ]]; then
  sed -i '/<!--uns:start-->/,/<!--uns:end-->/d' "$staging/index.html"
else
  sed -i '/<!--sgn:start-->/,/<!--sgn:end-->/d' "$staging/index.html"
fi
# Marker comments are build instructions, not published content.
sed -i -E '/<!--(sgn|uns):(start|end)-->/d' "$staging/index.html"
touch "$staging/.nojekyll"

if [[ -e "$output_dir/dists" || -e "$output_dir/pool" ]]; then
  echo "Output already contains an archive: $output_dir. Use a fresh output directory." >&2
  exit 1
fi
mkdir -p "$output_dir"
cp -a "$staging"/. "$output_dir"/
echo "APT archive created at $output_dir (suite: $suite, architecture: all)."
