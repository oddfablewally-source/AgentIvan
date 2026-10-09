"""
Build the frozen application with PyInstaller and smoke-test it.

One command, cross-platform (pure stdlib + PyInstaller):

    python3 scripts/package.py

It:
  1. Ensures PyInstaller is installed in the BOILERPLATE_VENV venv
     (version from requirements.txt, single source of truth).
  2. Runs the full test suite -- a red suite refuses the build, so a
     green package can never create false confidence about the tests.
  3. Removes any previous dist/ for a deterministic rebuild, then runs
     `pyinstaller app.spec --noconfirm --clean`.
  4. Executes the frozen binary with `--self-test` (offscreen Qt,
     redirected user-data dir) and requires exit 0.

Any failure exits non-zero. The smoke run is hermetic: it never
touches the real user data directory.

Environment overrides:
  BOILERPLATE_VENV  venv directory (default: <repo>/.venv)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify import (  # noqa: E402
    ensure_test_deps,
    ensure_venv,
    load_requirement_specs,
    run_tests,
)

APP_EXE_NAME = "LocalAI-Desktop"


def ensure_pyinstaller(python: Path, root: Path) -> None:
    proc = subprocess.run(
        [str(python), "-c", "import PyInstaller"],
        capture_output=True,
    )
    if proc.returncode != 0:
        (spec,) = load_requirement_specs(root, "pyinstaller")
        print(f"[package] installing {spec}")
        proc = subprocess.run(
            [str(python), "-m", "pip", "install", "-q", spec]
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pip install failed for {spec}")


def frozen_binary(root: Path) -> Path:
    exe = APP_EXE_NAME + (".exe" if os.name == "nt" else "")
    candidate = root / "dist" / APP_EXE_NAME / exe
    if not candidate.is_file():
        raise RuntimeError(f"frozen binary not found: {candidate}")
    return candidate


def smoke_env() -> dict[str, str]:
    """Hermetic environment for the frozen smoke run."""
    env = dict(os.environ)
    data_home = tempfile.mkdtemp(prefix="boilerplate-pkg-smoke-")
    if os.name == "nt":
        env["LOCALAPPDATA"] = data_home
    else:
        env["HOME"] = data_home
    env["QT_QPA_PLATFORM"] = "offscreen"
    # No network, no proxy interference for a local smoke test.
    for key in (
        "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
        "ALL_PROXY", "all_proxy",
    ):
        env.pop(key, None)
    return env


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    venv_dir = Path(
        os.environ.get("BOILERPLATE_VENV", str(root / ".venv"))
    ).expanduser()

    try:
        python = ensure_venv(venv_dir)
        # A fresh venv has no test tooling; the gate must run the real
        # suite, not die on "No module named pytest".
        ensure_test_deps(python, root)
        ensure_pyinstaller(python, root)
    except (RuntimeError, OSError) as exc:
        print(f"[package] ERROR: environment setup failed: {exc}")
        return 2

    # Gate composition: a green package must never imply green tests.
    # Refuse the expensive build when the suite is red.
    print("[package] running test suite (must be green before building)")
    if run_tests(python, root) != 0:
        print("[package] ERROR: test suite failed; refusing to build")
        return 1

    # Deterministic rebuild: never let a stale dist/ contaminate the
    # artifact under verification (--clean only clears build/).
    dist_dir = root / "dist"
    if dist_dir.exists():
        print("[package] removing previous dist/ for a deterministic rebuild")
        shutil.rmtree(dist_dir)

    print("[package] building with PyInstaller")
    proc = subprocess.run(
        [str(python), "-m", "PyInstaller", "app.spec",
         "--noconfirm", "--clean"],
        cwd=str(root),
    )
    if proc.returncode != 0:
        print("[package] ERROR: PyInstaller build failed")
        return 1

    try:
        binary = frozen_binary(root)
    except RuntimeError as exc:
        print(f"[package] ERROR: {exc}")
        return 1

    print(f"[package] smoke-testing {binary}")
    proc = subprocess.run(
        [str(binary), "--self-test"],
        env=smoke_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )
    print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0 or "self-test OK" not in proc.stdout:
        print("[package] ERROR: frozen smoke test failed")
        return 1

    print("[package] OK: frozen build smoke-tested successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
