#!/usr/bin/env python3
"""Dev helper: copy HubInterface/ into a Slicer module directory.

Requires --dest or SLICER_HUB_EXTENSION pointing at the installed module path, e.g.
  .../qt-scripted-modules/HubInterface

This mirrors the CMake install layout; it is not part of release/deploy.

Run from repo root:
  python tools/sync_extension.py --bundle
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "volview-client",
    "vtkjs-worklist-client",
    "hub-worklist-example",
    "OHIF-client",
    "slim",
    "deploy",
    "samples",
}
SKIP_FILE_NAMES = {"slicer-hub.zip"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

MODULE_FILES = ("HubInterface.py",)
MODULE_DIRS = ("Lib", "Resources", "Testing")
BUNDLED_DIRS = (
    "hub",
    "python_client",
    "resource_servers",
    "image_display",
)


def _default_src_root() -> Path:
    return Path(__file__).resolve().parent.parent / "HubInterface"


def _resolve_dest(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit.resolve()
    env = os.environ.get("SLICER_HUB_EXTENSION", "").strip()
    if env:
        return Path(env).resolve()
    return None


def _should_copy(rel: Path) -> bool:
    if rel.name in SKIP_FILE_NAMES:
        return False
    if rel.suffix.lower() in SKIP_SUFFIXES:
        return False
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return False
    return True


def sync_tree(src: Path, dest: Path) -> int:
    count = 0
    for file_path in sorted(src.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(src)
        if not _should_copy(rel):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            if file_path.read_bytes() == target.read_bytes():
                continue
        shutil.copy2(file_path, target)
        count += 1
        print(f"  {rel}")
    return count


def sync_module(src_root: Path, dest_root: Path) -> int:
    copied = 0
    for name in MODULE_FILES:
        src = src_root / name
        if src.is_file():
            print(f"[{name}]")
            dest = dest_root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file() or src.read_bytes() != dest.read_bytes():
                shutil.copy2(src, dest)
                copied += 1
                print(f"  {name}")
    for name in MODULE_DIRS:
        src = src_root / name
        if src.is_dir():
            print(f"[{name}/]")
            copied += sync_tree(src, dest_root / name)
    return copied


def sync_bundled(src_root: Path, dest_root: Path) -> int:
    copied = 0
    for name in BUNDLED_DIRS:
        src = src_root / name
        if not src.is_dir():
            continue
        print(f"[{name}/]")
        if name == "image_display":
            lib_src = src / "lib"
            if lib_src.is_dir():
                copied += sync_tree(lib_src, dest_root / name / "lib")
            continue
        if name == "resource_servers":
            for sub in ("products", "runtime"):
                sub_src = src / sub
                if sub_src.is_dir():
                    copied += sync_tree(sub_src, dest_root / name / sub)
            for fname in ("resource_server.py", "hub_presets.py"):
                fsrc = src / fname
                if fsrc.is_file():
                    fdest = dest_root / name / fname
                    fdest.parent.mkdir(parents=True, exist_ok=True)
                    if not fdest.is_file() or fsrc.read_bytes() != fdest.read_bytes():
                        shutil.copy2(fsrc, fdest)
                        copied += 1
                        print(f"  {fname}")
            continue
        copied += sync_tree(src, dest_root / name)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        help="Installed HubInterface module directory (qt-scripted-modules/HubInterface)",
    )
    parser.add_argument(
        "--src",
        type=Path,
        help="HubInterface source folder (default: repo/HubInterface)",
    )
    parser.add_argument(
        "--bundle",
        "--with-standalone",
        action="store_true",
        dest="bundle",
        help="Also sync hub/, python_client/, resource_servers/, image_display/",
    )
    args = parser.parse_args()

    src_root = (args.src or _default_src_root()).resolve()
    if not (src_root / "HubInterface.py").is_file():
        print(f"HubInterface.py not found under {src_root}", file=sys.stderr)
        return 1

    dest_root = _resolve_dest(args.dest)
    if dest_root is None or not dest_root.is_dir():
        print(
            "No destination. Pass --dest or set SLICER_HUB_EXTENSION to the "
            "installed module directory.",
            file=sys.stderr,
        )
        return 1

    print(f"Sync module {src_root} -> {dest_root}")
    copied = sync_module(src_root, dest_root)

    if args.bundle:
        copied += sync_bundled(src_root, dest_root)

    print(f"Done ({copied} file(s) updated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
