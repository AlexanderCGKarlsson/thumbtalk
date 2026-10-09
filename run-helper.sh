#!/bin/sh
# Launch this checkout with its locked dependencies, from any working directory.
if ! command -v uv >/dev/null 2>&1; then
    printf '%s\n' 'ThumbTalk source startup requires uv: https://docs.astral.sh/uv/getting-started/installation/' >&2
    exit 127
fi

thumbtalk_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
exec uv run --project "$thumbtalk_root/helper" --frozen --python 3.12 \
    python "$thumbtalk_root/helper/thumbtalk_cli.py" "$@"
