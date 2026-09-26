# pdf2md desktop

A small macOS app around the `pdf2md` pipeline: load a document that links to
online PDFs, tick the ones you want, and they are downloaded and converted to
Markdown into folders that mirror the document's categories.

## Getting the app

**From the Actions tab.** The *build macOS app* workflow produces a `.dmg` for
Apple silicon and for Intel. Run it from the Actions tab (or push a `v*` tag for
a release) and download the artifact for your Mac.

**Opening it the first time.** The app is not signed with an Apple Developer
certificate, so macOS will refuse to open it on the first try. Either right-click
the app and choose *Open* (then *Open* again in the dialog), or clear the
quarantine flag once:

```bash
xattr -dr com.apple.quarantine /Applications/pdf2md.app
```

That is the standard hurdle for unsigned apps. If you have a Developer ID,
signing and notarising is a matter of setting the usual `APPLE_*` secrets — see
[Tauri's macOS signing guide](https://v2.tauri.app/distribute/sign/macos/).

## Using it

1. **Load a document** — the button, or drop a file on the window. Markdown,
   text and HTML are read directly; Word, Excel and PDF are converted first.
2. **Pick what you want** — links are grouped by the document's own categories.
   Expand a category, filter by title, category or URL, and tick whole groups or
   single rows. Nothing is selected by default, so a 1,200-link document never
   starts downloading by accident.
3. **Choose where it goes** — the folder tree is written under it, along with
   `INDEX.md` and `_manifest.json`. Optionally keep the PDFs as well.
4. **Convert** — progress shows per file, and you can stop part way. Anything
   that failed is listed at the end, and *Show in Finder* opens the result.

## How it is put together

```
┌─────────────────────────┐   JSON, one object per line   ┌────────────────────┐
│ web view (src/)         │  ───── stdin ───────────────► │ pdf2md-service     │
│ · link list, selection  │  ◄──── stdout ─────────────── │ (PyInstaller       │
│ · progress, summary     │        replies + events       │  bundle of pdf2md) │
└─────────────────────────┘                               └────────────────────┘
            │ plugins: dialog, shell, opener
┌─────────────────────────┐
│ Rust shell (src-tauri/) │
└─────────────────────────┘
```

The Rust side is deliberately empty — it registers three Tauri plugins and
nothing else. All of the UI lives in the web view, and all of the work lives in
the Python service (`pdf2md/service.py`), which is bundled into a single binary
and shipped as a Tauri sidecar. That keeps the conversion exactly the same code
the CLI runs, markitdown included.

The protocol is documented at the top of `pdf2md/service.py`. The tricky part on
the JavaScript side is reassembling stdout into whole messages — Tauri's shell
plugin may deliver stripped lines or raw chunks — which is why that lives in its
own module, `src/protocol.js`, with tests.

## Building it yourself

On a Mac with [Rust](https://rustup.rs), Node 20+ and Python 3.11+:

```bash
cd desktop
bash scripts/build-sidecar.sh   # bundles the Python service (~70 MB, a few minutes)
npm install
npx tauri build --bundles app,dmg
```

The results land in `src-tauri/target/release/bundle/`. For a specific
architecture, add `--target aarch64-apple-darwin` or `--target x86_64-apple-darwin`.

### Developing

```bash
bash scripts/build-sidecar.sh   # only needed when the Python side changes
npm install
npm run tauri dev
```

`npm test` runs the front-end tests; `python -m pytest` at the repository root
covers the pipeline and the service, including the whole path from a document to
converted files.

### Regenerating the icon

```bash
python3 scripts/make-icon.py
npx tauri icon src-tauri/icon-source.png
```

## Troubleshooting

- **"the converter did not start"** — the sidecar is missing or was built for
  another architecture. It must exist as
  `src-tauri/binaries/pdf2md-service-<target-triple>`; re-run
  `scripts/build-sidecar.sh` on the machine you are building for.
- **Downloads all fail** — check the site is reachable from this Mac; a VPN or
  captive portal blocks them the same way it would block a browser.
- **A file converted but is empty** — that PDF is a scan with no text layer. The
  app marks it *no text found*; run it through OCR and convert it again.
