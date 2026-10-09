#!/usr/bin/env bash
# Install the matching offline Linux bundle; never fetch a different release.
set -euo pipefail
folder="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
archive="thumbtalk-linux-x86_64.tar.gz"
(cd "$folder" && sha256sum --check "$archive.sha256")
stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
tar -xzf "$folder/$archive" -C "$stage"
bash "$folder/install.sh" --bundle "$stage/ThumbTalk" "$@"
