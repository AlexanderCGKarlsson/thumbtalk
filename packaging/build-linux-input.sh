#!/usr/bin/env bash
# Compile the same unmodified revision as Decktation 32e1a7d.
set -euo pipefail
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
out="${1:-$root/build/linux-input}"
mkdir -p "$out"
source_dir="$(mktemp -d)"
trap 'rm -rf "$source_dir"' EXIT
archive="$root/packaging/third-party/ydotool/ydotool-57ba7d0.tar.gz"
[ -f "$archive" ] || archive="$(dirname -- "$0")/ydotool-57ba7d0.tar.gz"
tar -xzf "$archive" -C "$source_dir" --strip-components=1
cc -std=c99 -O2 -D_DEFAULT_SOURCE -DVERSION='"v1.0.4"' \
  -o "$out/ydotoold" "$source_dir/Daemon/ydotoold.c"
cc -std=c99 -O2 -D_DEFAULT_SOURCE -DVERSION='"v1.0.4"' -I"$source_dir/Client" \
  -o "$out/ydotool" "$source_dir/Client/ydotool.c" \
  "$source_dir/Client/tool_click.c" "$source_dir/Client/tool_mousemove.c" \
  "$source_dir/Client/tool_type.c" "$source_dir/Client/tool_key.c"
