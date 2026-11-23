#!/usr/bin/env bash
# Sign the Pastex APT archive with a dedicated repository key.
#
# Usage: sign-apt-repository.sh REPOSITORY_DIRECTORY [SUITE]
#
# Requires APT_SIGNING_KEY_ID (full 40-character fingerprint) and, for a
# protected key, GPG_PASSPHRASE. Never enable `set -x`: secrets come from CI.
set -euo pipefail
export LC_ALL=C

repository=$(realpath -m "${1:?Usage: $0 REPOSITORY_DIRECTORY [SUITE]}")
suite=${2:-stable}
if [[ ! "$suite" =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo 'Invalid APT suite name.' >&2
  exit 2
fi
distribution="$repository/dists/$suite"
key_id=${APT_SIGNING_KEY_ID:?Set APT_SIGNING_KEY_ID to the full signing-key fingerprint.}
if [[ ! "$key_id" =~ ^[A-Fa-f0-9]{40}$ ]]; then
  echo 'APT_SIGNING_KEY_ID must be a full 40-character fingerprint.' >&2
  exit 2
fi
[[ -f "$distribution/Release" ]] || { echo 'Build the APT repository before signing it.' >&2; exit 1; }
command -v gpg >/dev/null || { echo 'Install gnupg first.' >&2; exit 1; }
command -v gpgv >/dev/null || { echo 'Install gpgv first.' >&2; exit 1; }

if ! gpg --batch --list-secret-keys "$key_id" >/dev/null 2>&1; then
  echo "The signing secret key $key_id is not present in this GPG keyring." >&2
  echo 'Import it first (GPG_PRIVATE_KEY in CI) and check GNUPGHOME.' >&2
  exit 1
fi

# Public key metadata only: no secret material is ever printed.
gpg --batch --with-colons --list-secret-keys "$key_id" |
  awk -F: '
    $1 == "sec" { printf "Signing key: algorithm %s, %s bits, capabilities %s\n", $4, $3, $12 }
    $1 == "fpr" { printf "Fingerprint: %s\n", $10 }
  ' || true

# The passphrase travels on a file descriptor, never in process arguments.
passphrase=${GPG_PASSPHRASE-${APT_SIGNING_PASSPHRASE-}}
sign_arguments=(--batch --yes --local-user "$key_id")
if [[ -n "$passphrase" ]]; then
  sign_arguments+=(--pinentry-mode loopback --passphrase-fd 3)
fi

sign_failed=0
gpg "${sign_arguments[@]}" --clearsign \
  --output "$distribution/InRelease" "$distribution/Release" 3<<<"$passphrase" || sign_failed=1
if [[ $sign_failed -eq 0 ]]; then
  gpg "${sign_arguments[@]}" --armor --detach-sign \
    --output "$distribution/Release.gpg" "$distribution/Release" 3<<<"$passphrase" || sign_failed=1
fi
if [[ $sign_failed -ne 0 ]]; then
  if [[ -z "$passphrase" ]]; then
    echo 'gpg could not sign the Release file. If the key is passphrase-protected, configure GPG_PASSPHRASE.' >&2
  else
    echo 'gpg could not sign the Release file. Check GPG_PASSPHRASE and the key capabilities.' >&2
  fi
  exit 1
fi
unset passphrase sign_arguments

verification_keyring=$(mktemp "${TMPDIR:-/tmp}/pastex-public.XXXXXX")
trap 'rm -f "$verification_keyring"' EXIT
gpg --batch --armor --export "$key_id" >"$repository/archive-keyring.asc"
gpg --batch --export "$key_id" >"$verification_keyring"
gpg --batch --export "$key_id" >"$repository/archive-keyring.gpg"
printf '%s\n' "$key_id" >"$repository/archive-keyring.fingerprint.txt"
gpgv --keyring "$verification_keyring" "$distribution/InRelease"
gpgv --keyring "$verification_keyring" "$distribution/Release.gpg" "$distribution/Release"

# Publish the deb822 source file that pins the exported keyring.
install -m 0644 "$(cd "$(dirname "$0")" && pwd)/../packaging/pastex.sources" \
  "$repository/pastex.sources"
chmod 0644 "$repository/archive-keyring.asc" "$repository/archive-keyring.gpg" \
  "$repository/archive-keyring.fingerprint.txt" "$repository/pastex.sources"
echo 'Repository signatures created and verified successfully.'
