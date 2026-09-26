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
  --noconfirm --clean --onefile \
  --name pdf2md-service \
  --distpath "$work/dist" --workpath "$work/build" --specpath "$work" \
  --paths "$repo" \
  --collect-all markitdown \
  --collect-all magika \
  --collect-all onnxruntime \
  --collect-all markdownify \
  --collect-submodules pdf2md \
  `# markitdown imports all three before it will read a PDF, and gives the` \
  `# same "install markitdown[pdf]" error if any of them fails to import.` \
  --collect-all pdfminer \
  --collect-all pdfplumber \
  --collect-all pypdfium2 \
  --collect-all pypdfium2_raw \
  `# ...and this carries the imaging code pdfplumber loads at import time.` \
  --collect-all PIL \
  `# cryptography (pdfminer needs it for encrypted PDFs) is deliberately NOT` \
  `# collected wholesale: PyInstaller ships a hook for it, and forcing a blanket` \
  `# collect bundled an OpenSSL that did not match its compiled extension, so` \
  `# the Intel build failed to import it with "Symbol not found: _SSL_get0_group_name".` \
  "$desktop/scripts/service_entry.py"

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
