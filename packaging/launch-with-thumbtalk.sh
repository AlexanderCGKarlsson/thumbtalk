#!/usr/bin/env bash
# Keep the game environment untouched; sanitize Steam library paths only for our app.
set -u
root="$(cd -- "$(dirname -- "$0")" && pwd)"
if [ "$#" -eq 0 ]; then
  echo "Use this command in the game's Steam Launch Options, followed by %command%." >&2
  exit 2
fi
app="$root/app/thumbtalk"
[ -x "$app" ] || { echo "ThumbTalk is not installed at $root/app." >&2; exit 1; }
state="${XDG_STATE_HOME:-$HOME/.local/state}/thumbtalk"
mkdir -p "$state"
companion_pid=""
game_pid=""
cleanup() {
  if [ -n "$companion_pid" ]; then
    kill "$companion_pid" 2>/dev/null || true
    wait "$companion_pid" 2>/dev/null || true
    companion_pid=""
  fi
}
stop() {
  if [ -n "$game_pid" ]; then kill -TERM "$game_pid" 2>/dev/null || true; fi
  cleanup
  exit 143
}
trap cleanup EXIT
trap stop INT TERM
env -u LD_LIBRARY_PATH -u LD_PRELOAD -u PYTHONHOME -u PYTHONPATH \
  -u QT_PLUGIN_PATH -u QT_QPA_PLATFORM_PLUGIN_PATH -u QT_QPA_PLATFORM -u SDL_VIDEODRIVER \
  "$app" --headless > "$state/companion.log" 2>&1 &
companion_pid=$!
"$@" &
game_pid=$!
wait "$game_pid"
result=$?
game_pid=""
exit "$result"
