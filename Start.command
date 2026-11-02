#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ -x "RelayChat.app/Contents/MacOS/RelayChat" ]; then
    relay_data="${RELAY_DATA_DIR:-$HOME/Library/Application Support/RelayChat}"
    mkdir -p "$relay_data"
    pwd -P > "$relay_data/setup-folder.txt"
    exec "./RelayChat.app/Contents/MacOS/RelayChat" "$@"
fi
exec python3 app.py "$@"
