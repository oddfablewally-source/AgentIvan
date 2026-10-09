"""
Tests for scripts/verify.py -- the reproducible verification entry point.

Covers the checksum-gate logic deterministically (tmp files, no venv
creation, no network). The end-to-end bootstrap path is exercised by
running `python3 scripts/verify.py` itself.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.verify import load_requirement_specs, verify_checksums


def _write_baseline(baseline: Path, root: Path, names: list[str]) -> None:
    lines = []
    for name in names:
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    baseline.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_verify_checksums_all_match(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print('hi')\n", encoding="utf-8")
    baseline = tmp_path / "baseline.sha256"
    _write_baseline(baseline, tmp_path, ["a.py"])
    assert verify_checksums(baseline, tmp_path) == []


def test_verify_checksums_detects_tampering(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("original\n", encoding="utf-8")
    baseline = tmp_path / "baseline.sha256"
    _write_baseline(baseline, tmp_path, ["a.py"])
    target.write_text("tampered\n", encoding="utf-8")
    problems = verify_checksums(baseline, tmp_path)
    assert len(problems) == 1
    assert "a.py" in problems[0] and "mismatch" in problems[0]


def test_verify_checksums_detects_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("x\n", encoding="utf-8")
    baseline = tmp_path / "baseline.sha256"
    _write_baseline(baseline, tmp_path, ["a.py"])
    target.unlink()
    problems = verify_checksums(baseline, tmp_path)
    assert len(problems) == 1
    assert "a.py" in problems[0] and "missing" in problems[0]


def test_verify_checksums_missing_baseline_is_an_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        verify_checksums(tmp_path / "nope.sha256", tmp_path)


def test_verify_checksums_rejects_malformed_lines(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.sha256"
    baseline.write_text("this is not a checksum line\n", encoding="utf-8")
    problems = verify_checksums(baseline, tmp_path)
    assert len(problems) == 1
    assert "malformed" in problems[0]


def test_verify_checksums_handles_binary_marker_and_subdirs(
    tmp_path: Path,
) -> None:
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "b.py").write_text("y\n", encoding="utf-8")
    digest = hashlib.sha256(b"y\n").hexdigest()
    baseline = tmp_path / "baseline.sha256"
    baseline.write_text(f"{digest} *pkg/b.py\n", encoding="utf-8")
    assert verify_checksums(baseline, tmp_path) == []


def _write_requirements(root: Path, text: str) -> None:
    (root / "requirements.txt").write_text(text, encoding="utf-8")


def test_load_requirement_specs_parses_in_requested_order(
    tmp_path: Path,
) -> None:
    _write_requirements(
        tmp_path,
        "# a comment\n\nPySide6>=6.6\nrequests>=2.31,<3.0  # trailing\n"
        "pytest>=8.0,<9.0\n",
    )
    assert load_requirement_specs(tmp_path, "pytest", "pyside6", "requests") == [
        "pytest>=8.0,<9.0",
        "PySide6>=6.6",
        "requests>=2.31,<3.0",
    ]


def test_load_requirement_specs_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="requirements.txt not found"):
        load_requirement_specs(tmp_path, "pytest")


def test_load_requirement_specs_missing_name_raises(tmp_path: Path) -> None:
    _write_requirements(tmp_path, "pytest>=8.0\n")
    with pytest.raises(RuntimeError, match="pyinstaller"):
        load_requirement_specs(tmp_path, "pytest", "pyinstaller")


def test_ensure_venv_refuses_populated_non_venv_dir(tmp_path: Path) -> None:
    """A populated directory that is not a venv must fail loudly.

    Deliberate break: without the guard, ensure_venv() would run
    venv.create(..., clear=True) on this directory and wipe it (this is
    what the packaging test did to /usr before the guard existed).
    """
    from scripts.verify import ensure_venv

    victim = tmp_path / "not-a-venv"
    victim.mkdir()
    sentinel = victim / "precious.txt"
    sentinel.write_text("do not wipe", encoding="utf-8")
    with pytest.raises(RuntimeError, match="pyvenv.cfg"):
        ensure_venv(victim)
    assert sentinel.read_text(encoding="utf-8") == "do not wipe"


def test_ensure_venv_refuses_foreign_interpreter(tmp_path: Path) -> None:
    """A bin/python without pyvenv.cfg is not adopted as the venv.

    Without the guard, a BOILERPLATE_VENV pointing at a system prefix
    whose bin/python exists would be silently used, and pip would then
    install test/build deps into the foreign interpreter.
    """
    from scripts.verify import ensure_venv

    fake = tmp_path / "fake-prefix"
    (fake / "bin").mkdir(parents=True)
    (fake / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="pyvenv.cfg"):
        ensure_venv(fake)


def test_ensure_venv_creates_in_empty_dir(tmp_path: Path) -> None:
    """The guard must not block the legitimate fresh-bootstrap path."""
    from scripts.verify import ensure_venv

    target = tmp_path / "fresh"
    target.mkdir()
    python = ensure_venv(target)
    assert python.exists()
    assert (target / "pyvenv.cfg").is_file()

