"""Allow ``python -m pdf2md``."""

from pdf2md.cli import main

if __name__ == "__main__":  # pragma: no cover - thin wrapper
    raise SystemExit(main())
