#!/usr/bin/env bash
# Install a prebuilt ThumbTalk bundle for the current user. No sudo or Python needed.
set -euo pipefail
prefix="$HOME/.local/share/thumbtalk"
bundle=""
launch=1
shortcuts=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --bundle) bundle="$2"; shift 2 ;;
    --no-launch) launch=0; shift ;;
    --no-shortcuts) shortcuts=0; shift ;;
    --help) echo "Usage: bash install.sh [--bundle DIRECTORY] [--prefix DIRECTORY] [--no-launch] [--no-shortcuts]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
if [ -z "$bundle" ]; then
  [ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
    echo "This download supports 64-bit x86 Linux (including SteamOS and Bazzite)." >&2; exit 1;
  }
fi
case "$prefix" in /*) ;; *) echo "Use an absolute installation path." >&2; exit 1 ;; esac
[ ! -L "$prefix" ] || { echo "The install folder must not be a symbolic link." >&2; exit 1; }
# Serialize installs before creating staging files inside the target folder.
# Keep the lock beside the target so it cannot look like an unrelated install.
mkdir -p "$(dirname -- "$prefix")"
lock="$prefix.install-lock"
if ! mkdir "$lock" 2>/dev/null; then
  if [ -d "$lock" ]; then
    echo "Another ThumbTalk installer is already running. Wait for its setup window."
    exit 0
  fi
  echo "Cannot create the installer lock: $lock" >&2
  exit 1
fi
stage=""
cleanup() {
  if [ -n "$stage" ]; then rm -rf "$stage"; fi
  rmdir "$lock"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -d "$prefix" ] && [ -n "$(ls -A "$prefix")" ] && [ ! -f "$prefix/.thumbtalk-install" ]; then
  echo "That folder is not a ThumbTalk installation. Choose another --prefix." >&2; exit 1
fi
mkdir -p "$prefix"
stage="$(mktemp -d "$prefix/.install.XXXXXX")"
if [ -z "$bundle" ]; then
  base="https://github.com/AlexanderCGKarlsson/thumbtalk/releases/download/v0.8.0"
  asset="thumbtalk-linux-x86_64.tar.gz"
  echo "Downloading ThumbTalk…"
  curl --fail --location --retry 3 "$base/$asset" -o "$stage/$asset"
  curl --fail --location --retry 3 "$base/$asset.sha256" -o "$stage/$asset.sha256"
  (cd "$stage" && sha256sum --check "$asset.sha256")
  tar -xzf "$stage/$asset" -C "$stage"
  bundle="$stage/ThumbTalk"
fi
[ -x "$bundle/thumbtalk" ] && [ -f "$bundle/launch-with-thumbtalk.sh" ] || {
  echo "The app bundle is incomplete. Download the Linux bundle from ThumbTalk Releases." >&2; exit 1;
}
cp -R "$bundle" "$stage/app"
# Verify the runtime before replacing a working installation.
"$stage/app/thumbtalk" --help > /dev/null
previous="$prefix/app.previous"
[ ! -e "$previous" ] || { echo "An app.previous backup already exists; move it aside before updating." >&2; exit 1; }
if [ -d "$prefix/app" ]; then mv "$prefix/app" "$previous"; fi
if ! mv "$stage/app" "$prefix/app"; then
  if [ -d "$previous" ]; then mv "$previous" "$prefix/app"; fi
  exit 1
fi
touch "$prefix/.thumbtalk-install"
cp "$prefix/app/launch-with-thumbtalk.sh" "$prefix/launch-with-thumbtalk.sh"
chmod +x "$prefix/launch-with-thumbtalk.sh"
if [ "$shortcuts" -eq 1 ]; then
  applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$applications"
  # Desktop Exec has its own escaping rules (not shell quoting).
  desktop_exec="$prefix/app/thumbtalk"
  desktop_exec="${desktop_exec//\\/\\\\}"
  desktop_exec="${desktop_exec//\"/\\\"}"
  desktop_exec="${desktop_exec//\$/\\\$}"
  desktop_exec="${desktop_exec//\`/\\\`}"
  desktop_exec="${desktop_exec//%/%%}"
  cat > "$applications/thumbtalk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ThumbTalk
Comment=Local voice-to-chat for handheld players
Exec="$desktop_exec" --setup
Icon=audio-input-microphone
Terminal=false
Categories=Game;Utility;
EOF
  if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$applications" >/dev/null 2>&1 || true; fi
fi
if [ -d "$previous" ]; then rm -rf "$previous"; fi
echo "Updating existing ThumbTalk addons…"
if ! "$prefix/app/thumbtalk" --update-addons; then
  echo "The app is installed. Some addons need an update from ThumbTalk Setup > WoW."
fi
echo "ThumbTalk installed. Open ThumbTalk from your applications menu."
# Installation is complete; a second launch must not race with staging or wait
# for the setup window to close. App startup errors belong in the app log.
cleanup
trap - EXIT HUP INT TERM
if [ "$launch" -eq 1 ]; then
  logs="${XDG_STATE_HOME:-$HOME/.local/state}/thumbtalk"
  if mkdir -p "$logs"; then
    nohup "$prefix/app/thumbtalk" --setup > "$logs/setup.log" 2>&1 < /dev/null &
    echo "Opening setup. If no window appears, see $logs/setup.log"
  else
    echo "Open ThumbTalk from your applications menu to start setup."
  fi
fi
