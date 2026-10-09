"""
Build the Windows verification ZIP with intact Unicode filenames.

Why this script exists (instead of `zip -r`): the Info-ZIP `zip` CLI
does not set the UTF-8 flag (bit 11) on entries with non-ASCII names.
On Windows, extractors then decode those names as cp437 and the
synthetic fixture's Unicode filename
(2428504b...__rig-photo-ünïcode.png) comes out mangled, which breaks
the source-catalog and workspace-intake tests. Python's zipfile with
the UTF-8 flag set is honored by Windows Explorer, Expand-Archive,
7-Zip, and Python itself, so the name survives the round-trip byte
for byte.

Usage (from the repo root):

    python scripts/build_verify_zip.py \
        --staging ~/workspace/brick11-windows-verify \
        --out ~/workspace/your_files/agent-ivan-ui-brick.zip

It first syncs this repo into <staging>/agent-ivan (excluding
.venv, __pycache__, .pytest_cache, .git), then zips the whole staging
directory. Files already in staging that are not part of the repo
(WINDOWS-VERIFY.txt, ivan-core-brick/, ivan_b11_workflow.py,
ivan_baseline.sha256) are left untouched.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

# Directory/file names never packaged.
_EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache", ".git", ".hg"}
_EXCLUDE_FILES = {".DS_Store"}


def _iter_files(root: Path):
    """Yield (relative_posix_path, absolute_path) for every packaged file."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place so walk does not descend.
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDE_DIRS)
        for name in sorted(filenames):
            if name in _EXCLUDE_FILES:
                continue
            abs_path = Path(dirpath) / name
            rel = abs_path.relative_to(root).as_posix()
            yield rel, abs_path


def sync_repo_to_staging(repo: Path, staging_agent_ivan: Path) -> None:
    """Mirror the repo into staging/agent-ivan (excluding tooling dirs)."""
    if staging_agent_ivan.exists():
        shutil.rmtree(staging_agent_ivan)
    for rel, abs_path in _iter_files(repo):
        # Never package the staging output or this script's own temp files.
        dest = staging_agent_ivan / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(abs_path, dest)


def build_zip(staging: Path, out_zip: Path) -> int:
    """Zip the staging dir with the UTF-8 flag set on every entry."""
    count = 0
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        for rel, abs_path in _iter_files(staging):
            info = zipfile.ZipInfo(filename=rel)
            # Fixed timestamp keeps builds reproducible; the UTF-8 flag
            # (0x800) is the actual fix -- it tells Windows extractors to
            # decode the name as UTF-8 instead of cp437.
            info.date_time = (2026, 9, 21, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix
            info.external_attr = (0o644 << 16)
            info.flag_bits |= 0x800  # UTF-8 filename
            with open(abs_path, "rb") as fh:
                archive.writestr(info, fh.read())
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the Windows verification ZIP.")
    parser.add_argument("--staging", required=True,
                        help="staging directory (contains agent-ivan/, "
                             "WINDOWS-VERIFY.txt, ivan-core-brick/, ...)")
    parser.add_argument("--out", required=True, help="output ZIP path")
    parser.add_argument("--repo", default=".",
                        help="repo root to sync (default: cwd)")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    staging = Path(args.staging).resolve()
    out_zip = Path(args.out).resolve()

    if not (repo / "main.py").is_file() or not (repo / "src").is_dir():
        print(f"error: {repo} does not look like the agent-ivan repo",
              file=sys.stderr)
        return 2

    sync_repo_to_staging(repo, staging / "agent-ivan")
    count = build_zip(staging, out_zip)
    print(f"built {out_zip} ({count} files, UTF-8 filenames)")

    # Self-check: every non-ASCII name must carry the UTF-8 flag.
    with zipfile.ZipFile(out_zip) as archive:
        bad = [i.filename for i in archive.infolist()
               if any(ord(c) > 127 for c in i.filename)
               and not (i.flag_bits & 0x800)]
    if bad:
        print("error: entries missing the UTF-8 flag:", file=sys.stderr)
        for name in bad:
            print(f"  {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
