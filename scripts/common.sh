#!/usr/bin/env bash
# Shared helpers for setup.sh and run.sh.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ok()   { printf '  [OK]   %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; }
fail() { printf '  [FAIL] %s\n' "$1"; exit 1; }

# Find uv even when the current terminal was opened before uv was installed.
find_uv() {
  if command -v uv >/dev/null 2>&1; then UV="uv"; return 0; fi
  local candidate
  for candidate in \
    "$HOME/.local/bin/uv" "$HOME/.local/bin/uv.exe" "$HOME/.cargo/bin/uv" \
    "${LOCALAPPDATA:-$HOME/AppData/Local}"/Microsoft/WinGet/Links/uv.exe \
    "${LOCALAPPDATA:-$HOME/AppData/Local}"/Microsoft/WinGet/Packages/astral-sh.uv_*/uv.exe; do
    if [ -x "$candidate" ]; then UV="$candidate"; return 0; fi
  done
  return 1
}
