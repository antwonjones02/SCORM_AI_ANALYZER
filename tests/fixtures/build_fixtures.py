#!/usr/bin/env python3
"""Build SCORM/xAPI test fixture packages.

Zips every package source tree found under tests/fixtures/src/<package_name>/
into tests/fixtures/<package_name>.zip. Zip entries are stored relative to the
package root, so the manifest (imsmanifest.xml / tincan.xml) sits at the top
level of the archive — exactly how an LMS expects a SCORM upload.

The script is idempotent: existing zips are rebuilt from scratch on every run.

Usage:
    python3 tests/fixtures/build_fixtures.py
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
SRC_DIR = FIXTURES_DIR / "src"

# Files that should never end up inside a package archive.
EXCLUDED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def iter_package_files(package_dir: Path):
    """Yield all files in a package tree, sorted for reproducible archives."""
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and path.name not in EXCLUDED_NAMES:
            yield path


def build_package(package_dir: Path) -> Path:
    """Zip one package source tree; returns the path of the written archive."""
    zip_path = FIXTURES_DIR / f"{package_dir.name}.zip"
    if zip_path.exists():
        zip_path.unlink()  # rebuild from scratch so runs are idempotent

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in iter_package_files(package_dir):
            # Archive names are relative to the package root => manifest at
            # the top level of the zip, not nested inside a folder.
            arcname = file_path.relative_to(package_dir).as_posix()
            zf.write(file_path, arcname)

    return zip_path


def main() -> int:
    if not SRC_DIR.is_dir():
        print(f"error: source directory not found: {SRC_DIR}", file=sys.stderr)
        return 1

    package_dirs = sorted(p for p in SRC_DIR.iterdir() if p.is_dir())
    if not package_dirs:
        print(f"error: no package source trees under {SRC_DIR}", file=sys.stderr)
        return 1

    failures = 0
    for package_dir in package_dirs:
        zip_path = build_package(package_dir)
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            entries = len(zf.namelist())
        if bad is not None:
            print(f"FAIL  {zip_path.name}: corrupt entry {bad}", file=sys.stderr)
            failures += 1
            continue
        size = zip_path.stat().st_size
        print(f"built {zip_path.name}: {entries} files, {size} bytes")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
