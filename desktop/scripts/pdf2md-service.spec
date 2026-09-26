# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of the pdf2md service.

A spec file rather than command-line flags, because the TLS libraries need
fixing up after collection — see `Consistent OpenSSL` below.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = Path(SPECPATH)  # noqa: F821 — PyInstaller defines this
REPO = HERE.parent.parent

#: markitdown imports pdfminer, pdfminer.high_level and pdfplumber before it
#: will read a PDF, and reports the whole [pdf] extra as missing if any of them
#: fails, so the PDF packages and the binaries they load are collected whole.
PACKAGES = (
    "markitdown",
    "magika",
    "onnxruntime",
    "markdownify",
    "pdfminer",
    "pdfplumber",
    "pypdfium2",
    "pypdfium2_raw",
    "PIL",
)

datas, binaries, hiddenimports = [], [], []
for package in PACKAGES:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports
hiddenimports += collect_submodules("pdf2md")

a = Analysis(  # noqa: F821
    [str(HERE / "service_entry.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# --------------------------------------------------------------- Consistent OpenSSL
#
# Both `cryptography` and Python's own `_ssl` bring an OpenSSL, and PyInstaller
# flattens everything into one directory — so two files called
# `libssl.3.dylib` collide and one wins arbitrarily. On the Intel runner that
# produced a mismatched pair: libcrypto from cryptography (3.6.3) next to an
# older libssl, and cryptography's extension then failed to load with
#
#     Symbol not found: _SSL_get0_group_name
#
# taking pdfminer, and so the whole PDF backend, down with it.
#
# Every TLS library is therefore pointed at cryptography's copy, which keeps the
# pair consistent. Python's ssl module works against the newer library; it is
# the other way round that breaks.

sys.path.insert(0, str(HERE))
from bundle_openssl import align_tls_libraries  # noqa: E402

a.binaries, substitutions = align_tls_libraries(a.binaries)
for name, old_source, new_source in substitutions:
    print(f"spec: using cryptography's {name}\n      was {old_source}\n      now {new_source}")
if not substitutions:
    print("spec: no TLS libraries to align (cryptography links OpenSSL statically)")

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pdf2md-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
