#!/usr/bin/env bash
# Bundle the Python service into one self-contained binary and put it where
# Tauri expects a sidecar: src-tauri/binaries/pdf2md-service-<target-triple>.
#
# Run from anywhere; works on macOS and Linux. Needs python3 and rustc.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
desktop="$(dirname "$here")"
repo="$(dirname "$desktop")"

triple="${TARGET_TRIPLE:-$(rustc -vV | sed -n 's/^host: //p')}"
if [ -z "$triple" ]; then
  echo "could not work out the Rust target triple; set TARGET_TRIPLE" >&2
  exit 1
fi

work="${BUILD_DIR:-$desktop/.sidecar-build}"
out="$desktop/src-tauri/binaries"
mkdir -p "$out"

echo "==> installing the Python dependencies"
python3 -m pip install --quiet --upgrade pyinstaller
python3 -m pip install --quiet -r "$repo/requirements.txt"

echo "==> bundling pdf2md-service for $triple"
cd "$repo"
python3 -m PyInstaller \
  --noconfirm --clean \
  --distpath "$work/dist" --workpath "$work/build" \
  "$here/pdf2md-service.spec"

binary="$work/dist/pdf2md-service"
[ -f "$binary.exe" ] && binary="$binary.exe"
target="$out/pdf2md-service-$triple"
mv -f "$binary" "$target"
chmod +x "$target"

echo "==> checking the bundle actually converts a PDF"
# --version only proves it starts. This converts a real PDF, so a bundle that
# is missing markitdown's PDF backend fails the build instead of shipping.
"$target" --selftest

echo "==> $target ($(du -h "$target" | cut -f1))"
