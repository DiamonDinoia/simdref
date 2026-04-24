#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_ROOT="${SIMDREF_INSTALL_ROOT:-$HOME/.local/share/simdref}"
BIN_DIR="${SIMDREF_BIN_DIR:-$HOME/.local/bin}"
VENV_DIR="$INSTALL_ROOT/venv"
MAN_DIR="$INSTALL_ROOT/man"

mkdir -p "$INSTALL_ROOT" "$BIN_DIR" "$MAN_DIR"

if command -v uv >/dev/null 2>&1; then
	export UV_CACHE_DIR="${UV_CACHE_DIR:-$INSTALL_ROOT/uv-cache}"
	mkdir -p "$UV_CACHE_DIR"
	uv venv "$VENV_DIR"
	uv pip install --python "$VENV_DIR/bin/python" --no-build-isolation "$REPO_DIR"
else
	python3 -m venv "$VENV_DIR"
	"$VENV_DIR/bin/pip" install --upgrade pip
	"$VENV_DIR/bin/pip" install "$REPO_DIR"
fi

"$VENV_DIR/bin/python" -m simdref update --man-dir "$MAN_DIR"

ln -sf "$VENV_DIR/bin/simdref" "$BIN_DIR/simdref"
ln -sf "$VENV_DIR/bin/simdref-lsp" "$BIN_DIR/simdref-lsp"

cat <<EOF
Installed simdref.

Commands:
  $BIN_DIR/simdref doctor
  $BIN_DIR/simdref search _mm256_add_epi32
  $BIN_DIR/simdref man _mm256_add_epi32

The generated manpages live under:
  $MAN_DIR
EOF
