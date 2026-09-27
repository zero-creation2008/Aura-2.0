#!/usr/bin/env bash
set -euo pipefail
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$HOME/Divi"
if [ ! -d "$PREFIX" ]; then echo 'Run inside Termux'; exit 1; fi
pkg install -y python termux-api
python -m pip install --upgrade 'aiohttp>=3.10,<4'
if [ "$SOURCE_DIR" != "$TARGET_DIR" ]; then
  mkdir -p "$TARGET_DIR"
  cp -Rn "$SOURCE_DIR"/. "$TARGET_DIR"/
fi
mkdir -p "$TARGET_DIR"/{data,secure,logs,backups}
chmod 700 "$TARGET_DIR/secure" "$TARGET_DIR/data"
chmod +x "$TARGET_DIR/bin/divi"
mkdir -p "$PREFIX/bin"
ln -sfn "$TARGET_DIR/bin/divi" "$PREFIX/bin/divi"
"$TARGET_DIR/bin/divi" selftest
echo 'Run: divi owner setup && divi start'
