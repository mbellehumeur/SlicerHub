#!/usr/bin/env python3
"""Create a deployment zip for Slicer Hub (zip deploy, no Docker).

Builds SlicerHub-js-clients worklist + reporting + classroom, syncs production builds from
sibling repos into hub client folders, then packages everything under hub/:

- volview-client/  <- VolView/dist  (served at /volview-client/)
- worklist-client/  <- SlicerHub-js-clients/packages/worklist (/worklist-client/)
- reporting-client/  <- SlicerHub-js-clients/packages/reporting (/reporting-client/)
- classroom-client/  <- SlicerHub-js-clients/packages/classroom (/classroom-client/)
- slicerlive/  <- pw46/SlicerLive/render/demos/ira (IRA; /slicerlive/)
- slim/  <- slim/build  (/slim/)
- OHIF-client/  <- Viewers/platform/app/dist  (/ohif/; builds via pnpm run build:slicer-hub if needed)

Run from any directory:
    python hub/make_zip.py

Optional:
    python hub/make_zip.py --output slicer-hub.zip
    python hub/make_zip.py --skip-build
    python hub/make_zip.py --skip-sync

SlicerHub-js-clients: npm run build:apps (unless --skip-build)
SlicerLive IRA: syncs prebuilt ira.js (+ idc-worker.js); build IRA separately if needed
SlicerLive IRA: syncs prebuilt ira.js (+ idc-worker.js); build IRA separately if needed

OHIF: syncs Viewers/platform/app/dist. If that dist was built for site root
    (PUBLIC_URL=/ from local ``dev:slicer-hub``), this script runs
    ``pnpm run build:slicer-hub`` (PUBLIC_URL=/ohif/) unless --skip-build.
    Still refuses to zip a root build (breaks the cloud /ohif/ mount).

Slim for hub: pnpm run build:cast
    (PUBLIC_URL=/slim/, REACT_APP_CONFIG=cast, config file public/config/cast.js)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "tools",
    ".egg-info",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".ts", ".map"}
EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    "slicer-hub.zip",
    "package.json",
    "package-lock.json",
    ".gitignore",
    "LICENSE",
    # Local monorepo editable pin — Azure zip uses requirements-azure.txt instead.
    "requirements.txt",
}
EXCLUDED_DOC_SUFFIXES = {".md"}
# Written into the zip as requirements.txt (no -e ../python_client).
AZURE_REQUIREMENTS_NAME = "requirements-azure.txt"

# (dest folder under hub, CLI dest name for --*-dist override key)
CLIENT_SYNC_SPECS = (
    ("volview-client", "volview"),
    ("worklist-client", "worklist"),
    ("reporting-client", "reporting"),
    ("classroom-client", "classroom"),
    ("slicerlive", "slicerlive"),
    ("hub-mirror", "hubmirror"),
    ("slim", "slim"),
    ("OHIF-client", "ohif"),
)

# URL mount path in hub.py (may differ from folder name)
MOUNT_PATH_BY_DEST = {
    "volview-client": "/volview-client/",
    "worklist-client": "/worklist-client/",
    "reporting-client": "/reporting-client/",
    "classroom-client": "/classroom-client/",
    "slicerlive": "/slicerlive/",
    "hub-mirror": "/hub-mirror/",
    "slim": "/slim/",
    "OHIF-client": "/ohif/",
}

# Repo folder names and dist paths under each repo (workspace layout).
REPO_NAMES = {
    "volview": "VolView",
    "worklist": "worklist",
    "reporting": "reporting",
    "classroom": "classroom",
    "slicerlive": "SlicerLive",
    "hubmirror": "SlicerLive",
    "slim": "slim",
    "ohif": "Viewers",
}
DIST_PARTS_UNDER_REPO = {
    "volview": ("dist",),
    "worklist": (),  # flat static tree (index.html + worklist.js + …)
    "reporting": (),
    "classroom": (),
    "slicerlive": ("render", "demos", "ira"),
    "hubmirror": ("render", "demos", "hub-mirror"),
    "slim": ("build",),
    "ohif": ("platform", "app", "dist"),
}
# Flat SPA packages under SlicerHub-js-clients/packages/.
HUB_JS_CLIENT_KEYS = frozenset({"worklist", "reporting", "classroom"})
HUB_JS_CLIENTS_REPO = "SlicerHub-js-clients"

# Static files to ship for the IRA demo (ira.html is also written as index.html).
SLICERLIVE_IRA_FILES = ("ira.html", "ira.js", "idc-worker.js")
# hub-mirror LiveScene stream client (hub-mirror.html → index.html).
HUB_MIRROR_FILES = ("hub-mirror.html", "hub-mirror.js")


def find_hub_js_clients_root(script_dir: Path) -> Path | None:
    """Locate SlicerHub-js-clients monorepo root (has package.json workspaces)."""
    for base in (script_dir, *script_dir.parents):
        root = base / HUB_JS_CLIENTS_REPO
        package_json = root / "package.json"
        if package_json.is_file():
            return root.resolve()
    return None


def find_repo_root(script_dir: Path, key: str) -> Path | None:
    """Locate client repos from hub upward (workspace roots)."""
    repo_name = REPO_NAMES[key]
    for base in (script_dir, *script_dir.parents):
        if key == "slicerlive":
            for candidate in (base / "pw46" / "SlicerLive", base / "SlicerLive"):
                ira = candidate / "render" / "demos" / "ira" / "ira.html"
                if ira.is_file():
                    return candidate.resolve()
            continue
        if key == "hubmirror":
            for candidate in (base / "pw46" / "SlicerLive", base / "SlicerLive"):
                hm = candidate / "render" / "demos" / "hub-mirror" / "hub-mirror.html"
                if hm.is_file():
                    return candidate.resolve()
            continue
        if key in HUB_JS_CLIENT_KEYS:
            pkg = base / HUB_JS_CLIENTS_REPO / "packages" / repo_name
            if (
                pkg.is_dir()
                and (pkg / "package.json").is_file()
                and (pkg / "index.html").is_file()
            ):
                return pkg.resolve()
            continue
        if key == "slim":
            direct = base / "slim"
            if direct.is_dir() and (direct / "package.json").is_file():
                return direct.resolve()
            continue
        if key == "ohif":
            # Prefer current Viewers (Slicer Hub OHIF 3.14 + @slicer-hub/ohif-extension)
            # over legacy ProjectWeek45/Viewers.
            for candidate in (
                base / "Viewers",
                base / "hub-interface" / "Viewers",
                base / "ProjectWeek45" / "Viewers",
            ):
                if candidate.is_dir() and (candidate / "package.json").is_file():
                    return candidate.resolve()
            continue
        under_pw = base / "ProjectWeek45" / repo_name
        if under_pw.is_dir():
            return under_pw.resolve()
        direct = base / repo_name
        if direct.is_dir():
            return direct.resolve()
    return None


def default_dist_path(script_dir: Path, key: str) -> Path:
    repo_name = REPO_NAMES[key]
    repo_root = find_repo_root(script_dir, key)
    if repo_root is None:
        if key in HUB_JS_CLIENT_KEYS:
            raise FileNotFoundError(
                f"Could not find {HUB_JS_CLIENTS_REPO}/packages/{repo_name} "
                f"(searched from {script_dir})"
            )
        raise FileNotFoundError(
            f"Could not find {repo_name} repo in workspace "
            f"(searched from {script_dir})"
        )
    parts = DIST_PARTS_UNDER_REPO[key]
    return (repo_root / Path(*parts)).resolve() if parts else repo_root.resolve()


def should_include(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDED_DIR_NAMES:
        return False
    if any(part.endswith(".egg-info") for part in path.parts):
        return False
    if path.name in EXCLUDED_FILE_NAMES:
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.suffix.lower() in EXCLUDED_DOC_SUFFIXES:
        return False
    return True


def ohif_index_is_cloud_mount(index: Path, expected: str = "/ohif/") -> bool:
    """True when index.html was built for the hub /ohif/ mount (not site root)."""
    if not index.is_file():
        return False
    text = index.read_text(encoding="utf-8", errors="replace")
    needle = f"window.PUBLIC_URL = '{expected}'"
    if needle not in text:
        return False
    if 'src="/app.js"' in text or "src='/app.js'" in text:
        return False
    return True


def assert_ohif_public_url(index: Path, expected: str = "/ohif/") -> None:
    """Fail if OHIF was built for site root (PUBLIC_URL=/) instead of /ohif/."""
    if ohif_index_is_cloud_mount(index, expected):
        return
    needle = f"window.PUBLIC_URL = '{expected}'"
    raise FileNotFoundError(
        f"OHIF index.html missing {needle!r} (got wrong PUBLIC_URL). "
        f"Rebuild with: cd Viewers/platform/app && pnpm run build:slicer-hub "
        f"(PUBLIC_URL=/ohif/). Refusing to package broken cloud mount. "
        f"Index: {index}"
    )


def sync_dist_tree(src: Path, dest: Path, *, ohif_mount_check: bool = False) -> int:
    """Replace dest with a copy of src; require src/index.html. Returns file count."""
    index = src / "index.html"
    if not src.is_dir():
        raise FileNotFoundError(f"Source dist not found: {src}")
    if not index.is_file():
        raise FileNotFoundError(f"Source dist missing index.html: {index}")
    if ohif_mount_check:
        assert_ohif_public_url(index)

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    file_count = 0
    for file_path in src.rglob("*"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(src)
        if not should_include(rel):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
        file_count += 1
    return file_count


def sync_slicerlive_ira(src: Path, dest: Path) -> int:
    """Copy IRA demo static files; write ira.html as index.html for hub SPA mount."""
    if not src.is_dir():
        raise FileNotFoundError(f"Source dist not found: {src}")
    ira_html = src / "ira.html"
    ira_js = src / "ira.js"
    if not ira_html.is_file():
        raise FileNotFoundError(f"SlicerLive IRA missing ira.html: {ira_html}")
    if not ira_js.is_file():
        raise FileNotFoundError(
            f"SlicerLive IRA missing ira.js (build the demo first): {ira_js}"
        )

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    shutil.copy2(ira_html, dest / "index.html")
    file_count = 1
    for name in SLICERLIVE_IRA_FILES:
        src_file = src / name
        if not src_file.is_file():
            if name == "idc-worker.js":
                print(
                    f"Warning: {src_file} missing; IDC worker loads may fail",
                    file=sys.stderr,
                )
            continue
        shutil.copy2(src_file, dest / name)
        file_count += 1
    return file_count


def sync_hub_mirror(src: Path, dest: Path) -> int:
    """Copy hub-mirror demo; write hub-mirror.html as index.html for hub SPA mount."""
    if not src.is_dir():
        raise FileNotFoundError(f"Source dist not found: {src}")
    html = src / "hub-mirror.html"
    js = src / "hub-mirror.js"
    if not html.is_file():
        raise FileNotFoundError(f"hub-mirror missing hub-mirror.html: {html}")
    if not js.is_file():
        raise FileNotFoundError(
            f"hub-mirror missing hub-mirror.js (build the demo first): {js}"
        )

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    shutil.copy2(html, dest / "index.html")
    file_count = 1
    for name in HUB_MIRROR_FILES:
        src_file = src / name
        if not src_file.is_file():
            continue
        shutil.copy2(src_file, dest / name)
        file_count += 1
    return file_count


def find_python_client(script_dir: Path) -> Path | None:
    """Locate HubInterface/python_client next to hub."""
    sibling = (script_dir.parent / "python_client").resolve()
    if sibling.is_dir() and (sibling / "src" / "hub_client").is_dir():
        return sibling
    for base in script_dir.parents:
        candidate = base / "HubInterface" / "python_client"
        if candidate.is_dir() and (candidate / "src" / "hub_client").is_dir():
            return candidate.resolve()
    return None


def add_tree_to_zip(
    zf: ZipFile,
    src_root: Path,
    arc_prefix: str,
) -> int:
    """Add files under src_root into the zip under arc_prefix/. Returns file count."""
    count = 0
    for file_path in sorted(src_root.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(src_root)
        if not should_include(rel):
            continue
        arcname = f"{arc_prefix.rstrip('/')}/{rel.as_posix()}"
        zf.write(file_path, arcname)
        count += 1
    return count


def build_zip(source_dir: Path, output_zip: Path) -> tuple[int, dict[str, int]]:
    file_count = 0
    client_counts: dict[str, int] = {name: 0 for name, _ in CLIENT_SYNC_SPECS}
    azure_req = source_dir / AZURE_REQUIREMENTS_NAME
    with ZipFile(output_zip, "w", ZIP_DEFLATED) as zf:
        # Azure / Oryx expect requirements.txt — ship the azure variant (no editable pin).
        if azure_req.is_file():
            zf.write(azure_req, "requirements.txt")
            file_count += 1
            print(
                f"Packaged {AZURE_REQUIREMENTS_NAME} as requirements.txt "
                "(omitted -e ../python_client)"
            )
        else:
            print(
                f"Warning: {AZURE_REQUIREMENTS_NAME} missing; "
                "zip will not include requirements.txt",
                file=sys.stderr,
            )

        for file_path in sorted(source_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(source_dir)
            if not should_include(rel):
                continue
            # Already written as requirements.txt above.
            if rel.as_posix() == AZURE_REQUIREMENTS_NAME:
                continue
            arcname = rel.as_posix()
            zf.write(file_path, arcname)
            file_count += 1
            for client_name, _ in CLIENT_SYNC_SPECS:
                prefix = f"{client_name}/"
                if arcname.startswith(prefix):
                    client_counts[client_name] += 1
                    break

        py_client = find_python_client(source_dir)
        if py_client is not None:
            n = add_tree_to_zip(zf, py_client, "python_client")
            file_count += n
            print(f"Packaged python_client/ ({n} files) from {py_client}")
            client_counts["python_client"] = n
        else:
            print(
                "Warning: python_client not found; hub may fail to import hub_client",
                file=sys.stderr,
            )

    return file_count, client_counts


def resolve_dist_arg(
    script_dir: Path,
    key: str,
    override: str | None,
) -> Path:
    if override:
        path = Path(override).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (script_dir / path).resolve()
    return default_dist_path(script_dir, key)


def _npm_cmd() -> list[str]:
    """Prefer npm.cmd on Windows so CreateProcess finds it."""
    if os.name == "nt":
        return ["npm.cmd"]
    return ["npm"]


def _pnpm_cmd() -> list[str]:
    if os.name == "nt":
        return ["pnpm.cmd"]
    return ["pnpm"]


def build_ohif_slicer_hub(script_dir: Path) -> None:
    """Production OHIF for the hub /ohif/ mount (PUBLIC_URL=/ohif/)."""
    viewers = find_repo_root(script_dir, "ohif")
    if viewers is None:
        raise RuntimeError("Could not find Viewers repo (searched from hub/)")
    app = viewers / "platform" / "app"
    if not (app / "package.json").is_file():
        raise RuntimeError(f"OHIF app package.json missing: {app}")
    print(f"Building OHIF for /ohif/ ({app}) …")
    try:
        subprocess.run(
            [*_pnpm_cmd(), "run", "build:slicer-hub"],
            cwd=app,
            check=True,
        )
    except FileNotFoundError as err:
        raise RuntimeError("pnpm not found; install pnpm to build OHIF") from err
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            f"pnpm run build:slicer-hub failed (exit {err.returncode})"
        ) from err
    index = app / "dist" / "index.html"
    if not ohif_index_is_cloud_mount(index):
        raise RuntimeError(
            f"OHIF build finished but {index} still lacks PUBLIC_URL=/ohif/"
        )
    print("Built OHIF (PUBLIC_URL=/ohif/)")


def ensure_ohif_cloud_dist(script_dir: Path, dist: Path) -> None:
    """Rebuild OHIF when dist is missing or was built for PUBLIC_URL=/."""
    if ohif_index_is_cloud_mount(dist / "index.html"):
        return
    print(
        f"OHIF dist is not a /ohif/ build ({dist / 'index.html'}); "
        "running pnpm run build:slicer-hub …"
    )
    build_ohif_slicer_hub(script_dir)


def build_hub_js_clients(script_dir: Path) -> int:
    """Build worklist + reporting + classroom via SlicerHub-js-clients ``npm run build:apps``."""
    root = find_hub_js_clients_root(script_dir)
    if root is None:
        raise RuntimeError(
            f"Could not find {HUB_JS_CLIENTS_REPO} (searched from {script_dir})"
        )
    for key in sorted(HUB_JS_CLIENT_KEYS):
        if find_repo_root(script_dir, key) is None:
            raise RuntimeError(
                f"Missing {HUB_JS_CLIENTS_REPO}/packages/{REPO_NAMES[key]}"
            )
    print(f"Building hub JS clients ({root}) …")
    try:
        subprocess.run(
            [*_npm_cmd(), "run", "build:apps"],
            cwd=root,
            check=True,
        )
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            f"{HUB_JS_CLIENTS_REPO} npm run build:apps failed (exit {err.returncode})"
        ) from err
    print(f"Built hub JS clients ({len(HUB_JS_CLIENT_KEYS)} apps)")
    return len(HUB_JS_CLIENT_KEYS)


def build_deploy_clients(script_dir: Path, *, ohif_dist: Path | None = None) -> int:
    """Build clients required for the Azure zip (JS apps + OHIF /ohif/ dist)."""
    n = build_hub_js_clients(script_dir)
    dist = ohif_dist if ohif_dist is not None else default_dist_path(script_dir, "ohif")
    ensure_ohif_cloud_dist(script_dir, dist)
    return n


def main() -> int:
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(
        description="Sync hub SPA clients and create hub zip deployment package"
    )
    parser.add_argument(
        "--output",
        default=str(script_dir / "slicer-hub.zip"),
        help="Output zip path (default: hub/slicer-hub.zip)",
    )
    parser.add_argument(
        "--volview-dist",
        default=None,
        help="VolView production dist (default: <VolView>/dist in workspace)",
    )
    parser.add_argument(
        "--worklist-dist",
        default=None,
        help="worklist static root (default: SlicerHub-js-clients/packages/worklist)",
    )
    parser.add_argument(
        "--reporting-dist",
        default=None,
        help="reporting static root (default: SlicerHub-js-clients/packages/reporting)",
    )
    parser.add_argument(
        "--classroom-dist",
        default=None,
        help="classroom static root (default: SlicerHub-js-clients/packages/classroom)",
    )
    parser.add_argument(
        "--ohif-dist",
        default=None,
        help="OHIF viewer dist (default: Viewers/platform/app/dist)",
    )
    parser.add_argument(
        "--slim-dist",
        default=None,
        help="Slim production build (default: slim/build in workspace)",
    )
    parser.add_argument(
        "--slicerlive-dist",
        default=None,
        help="SlicerLive IRA static root (default: <SlicerLive>/render/demos/ira)",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip npm run build:apps and OHIF pnpm run build:slicer-hub",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Zip hub as-is without copying dist folders",
    )
    args = parser.parse_args()

    dist_overrides = {
        "volview": args.volview_dist,
        "worklist": args.worklist_dist,
        "reporting": args.reporting_dist,
        "classroom": args.classroom_dist,
        "ohif": args.ohif_dist,
        "slim": args.slim_dist,
        "slicerlive": args.slicerlive_dist,
        "hubmirror": None,
    }

    output_zip = Path(args.output).resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    if not args.skip_sync and not args.skip_build:
        try:
            ohif_for_build = None
            if args.ohif_dist:
                ohif_for_build = resolve_dist_arg(script_dir, "ohif", args.ohif_dist)
            build_deploy_clients(script_dir, ohif_dist=ohif_for_build)
        except (RuntimeError, FileNotFoundError) as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1

    if not args.skip_sync:
        for dest_name, key in CLIENT_SYNC_SPECS:
            src = resolve_dist_arg(script_dir, key, dist_overrides.get(key))
            dest = script_dir / dest_name
            try:
                if key == "slicerlive":
                    count = sync_slicerlive_ira(src, dest)
                elif key == "hubmirror":
                    count = sync_hub_mirror(src, dest)
                else:
                    count = sync_dist_tree(
                        src, dest, ohif_mount_check=(key == "ohif")
                    )
                print(f"Synced {src} -> {dest_name}/ ({count} files)")
            except FileNotFoundError as err:
                if key == "ohif":
                    print(f"Error: {err}", file=sys.stderr)
                    return 1
                print(f"Warning: {err}; skipping {dest_name}/", file=sys.stderr)

    if output_zip.exists():
        output_zip.unlink()

    for dest_name, _ in CLIENT_SYNC_SPECS:
        index = script_dir / dest_name / "index.html"
        if not index.is_file():
            mount = MOUNT_PATH_BY_DEST.get(dest_name, f"/{dest_name}/")
            print(
                f"Warning: {dest_name}/index.html not found; "
                f"hub will not serve {mount}",
                file=sys.stderr,
            )
            continue
        if dest_name == "OHIF-client":
            try:
                assert_ohif_public_url(index)
            except FileNotFoundError as err:
                print(f"Error: {err}", file=sys.stderr)
                return 1

    file_count, client_counts = build_zip(script_dir, output_zip)
    summary_parts = [
        f"{name}={client_counts.get(name, 0)}" for name, _ in CLIENT_SYNC_SPECS
    ]
    if "python_client" in client_counts:
        summary_parts.append(f"python_client={client_counts['python_client']}")
    print(f"Created {output_zip} ({file_count} files; {', '.join(summary_parts)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
