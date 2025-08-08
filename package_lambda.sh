#!/bin/zsh
set -euo pipefail

WORKDIR="$(pwd)"
PKG_DIR="$WORKDIR/.lambda_build"
ZIP_FILE="$WORKDIR/lambda_package.zip"

rm -rf "$PKG_DIR" "$ZIP_FILE"
mkdir -p "$PKG_DIR"

python3 -m venv "$PKG_DIR/.venv"
source "$PKG_DIR/.venv/bin/activate"
pip install -q -r "$WORKDIR/requirements.txt" -t "$PKG_DIR" | cat
deactivate

cp "$WORKDIR/lambda_function.py" "$PKG_DIR/"

cd "$PKG_DIR"
zip -qr "$ZIP_FILE" .
cd "$WORKDIR"

echo "Pacote gerado: $ZIP_FILE"


