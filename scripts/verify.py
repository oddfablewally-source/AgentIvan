"""
Reproducible verification for the Universal Boilerplate.

One command, from a fresh checkout, with only a system Python 3:

    python3 scripts/verify.py

It:
  1. Creates (or reuses) a virtualenv for the test tooling.
  2. Installs the test dependencies into it (pytest, PySide6, requests).
  3. Runs the full test suite.
  4. Verifies the Ivan Core SHA-256 baseline.

Any failure exits non-zero with a clear message. Nothing is skipped
silently: a missing Ivan baseline is an error, not a pass.

Environment overrides:
  BOILERPLATE_VENV  venv directory (default: <repo>/.venv)
  IVAN_DIR          Ivan Core directory (default: ~/workspace/ivan-core-brick)
  IVAN_BASELINE     baseline checksum file (default: ~/workspace/ivan_baseline.sha256)
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import venv
from pathlib import Path

# Import checks for the test dependencies, in the same order as the
# distribution names passed to load_requirement_specs().
# NOTE (Brick 11A): reportlab is a declared product dependency used by the
# Brick 8 PDF exporter and its tests. A fresh checkout's venv failed
# collection on 10 test modules because the gate never installed it --
# the dev machine's venv happened to have it already. Every runtime/test
# dependency in requirements.txt (except PyInstaller, which package.py
# manages) must be listed here, or a clean machine cannot run the gate.
IMPORT_CHECKS = ["pytest", "PySide6.QtCore", "requests", "reportlab"]

_REQUIREMENT_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+(?:\[[^\]]*\])?)\s*(.*)$")


def load_requirement_specs(root: Path, *names: str) -> list[str]:
    """Return the requirement specifiers from requirements.txt.

    Single source of truth for dependency versions: the scripts install
    exactly what requirements.txt declares, so bumping a version there
    cannot silently desync the gates. Names match case-insensitively;
    the returned specifiers are in the requested order.
    """
    req_file = root / "requirements.txt"
    if not req_file.is_file():
        raise RuntimeError(f"requirements.txt not found: {req_file}")
    wanted = [n.lower() for n in names]
    found: dict[str, str] = {}
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQUIREMENT_LINE.match(line)
        if not match:
            continue
        dist_name = match.group(1).split("[", 1)[0].lower()
        if dist_name in wanted and dist_name not in found:
            found[dist_name] = line
    missing = [n for n in wanted if n not in found]
    if missing:
        raise RuntimeError(
            f"requirements.txt has no specifier for: {', '.join(missing)}"
        )
    return [found[n] for n in wanted]

_CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+[* ]?(.+)$")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def venv_python(venv_dir: Path) -> Path:
    bindir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    return bindir / ("python.exe" if os.name == "nt" else "python")


def ensure_venv(venv_dir: Path) -> Path:
    """Return the venv's python executable, creating the venv if needed.

    Safety: the target must be a venv we manage -- a missing or empty
    directory, or an existing directory containing pyvenv.cfg. A
    populated non-venv directory (e.g. BOILERPLATE_VENV=/usr from a
    misresolved sys.executable) fails loudly instead of being wiped by
    clear=True or adopted as a foreign interpreter that pip would then
    install into.
    """
    target = venv_dir.resolve()
    if target.exists() and not (
        target.is_dir()
        and (not any(target.iterdir()) or (target / "pyvenv.cfg").is_file())
    ):
        raise RuntimeError(
            f"refusing to use {venv_dir} as a venv: it exists and is "
            f"neither an empty directory nor a venv (pyvenv.cfg missing)"
        )
    python = venv_python(venv_dir)
    if not python.exists():
        print(f"[verify] creating venv at {venv_dir}")
        venv.create(
            venv_dir, system_site_packages=True, with_pip=True, clear=True
        )
        python = venv_python(venv_dir)
    if not python.exists():
        raise RuntimeError(f"venv python missing after create: {python}")
    return python


def ensure_test_deps(python: Path, root: Path) -> None:
    """Install any test dependency whose import is unavailable.

    Versions come from requirements.txt (single source of truth).
    """
    specs = load_requirement_specs(root, "pytest", "PySide6", "requests", "reportlab")
    missing: list[str] = []
    for module, spec in zip(IMPORT_CHECKS, specs):
        proc = subprocess.run(
            [str(python), "-c", f"import {module}"],
            capture_output=True,
        )
        if proc.returncode != 0:
            missing.append(spec)
    if missing:
        print(f"[verify] installing: {', '.join(missing)}")
        proc = subprocess.run(
            [str(python), "-m", "pip", "install", "-q", *missing]
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pip install failed for {missing}")


def run_tests(python: Path, root: Path) -> int:
    print("[verify] running test suite")
    proc = subprocess.run(
        [str(python), "-m", "pytest", "tests/", "-q"],
        cwd=str(root),
    )
    return proc.returncode


def _content_digests(data: bytes) -> tuple[str, ...]:
    """SHA-256 digests accepted for file content.

    The byte-exact digest is primary. A newline-normalized digest
    (CRLF/CR -> LF) is also accepted, because files written in text
    mode or checked out with CRLF (e.g. git autocrlf on Windows) must
    still verify against baselines computed from LF content. A change
    to the actual content fails both digests, so tampering is still
    detected; only line-ending representation is tolerated.
    """
    digests = [hashlib.sha256(data).hexdigest()]
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if normalized != data:
        digests.append(hashlib.sha256(normalized).hexdigest())
    return tuple(digests)


def verify_checksums(baseline: Path, root: Path) -> list[str]:
    """Check files under `root` against a sha256sum-format baseline.

    Returns a list of problem descriptions; empty means all good.
    A missing baseline file raises FileNotFoundError (never a silent pass).
    """
    if not baseline.is_file():
        raise FileNotFoundError(f"Ivan baseline not found: {baseline}")
    problems: list[str] = []
    for lineno, raw_line in enumerate(
        baseline.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        match = _CHECKSUM_LINE.match(line)
        if not match:
            problems.append(f"{baseline}:{lineno}: malformed line")
            continue
        expected, relpath = match.group(1).lower(), match.group(2)
        target = root / relpath
        if not target.is_file():
            problems.append(f"{relpath}: missing")
            continue
        data = target.read_bytes()
        if expected not in _content_digests(data):
            problems.append(f"{relpath}: checksum mismatch")
    return problems


def main() -> int:
    root = repo_root()
    venv_dir = Path(
        os.environ.get("BOILERPLATE_VENV", str(root / ".venv"))
    ).expanduser()
    ivan_dir = Path(
        os.environ.get("IVAN_DIR", "~/workspace/ivan-core-brick")
    ).expanduser()
    ivan_baseline = Path(
        os.environ.get("IVAN_BASELINE", "~/workspace/ivan_baseline.sha256")
    ).expanduser()

    try:
        python = ensure_venv(venv_dir)
        ensure_test_deps(python, root)
    except (RuntimeError, OSError) as exc:
        print(f"[verify] ERROR: environment setup failed: {exc}")
        return 2

    if run_tests(python, root) != 0:
        print("[verify] ERROR: test suite failed")
        return 1

    try:
        problems = verify_checksums(ivan_baseline, ivan_dir)
    except FileNotFoundError as exc:
        print(f"[verify] ERROR: {exc}")
        return 2
    if problems:
        print("[verify] ERROR: Ivan Core checksum baseline mismatch:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("[verify] OK:  venv, test suite, and Ivan Core baseline all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
