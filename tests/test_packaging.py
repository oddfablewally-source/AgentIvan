"""
Gate-composition tests: the package gate must refuse to build when the
test suite is red, so a green package can never create false confidence
about the tests.

Runs the real scripts/package.py in a subprocess with a deliberately
broken probe test injected, then removes the probe. Uses the same venv
the test process runs in (sys.executable) so no new environment is
built.

Recursion guard: package.py itself runs the full pytest suite, which
would re-enter this test forever. The nested runs see
BOILERPLATE_GATE_NESTED=1 and skip, while the injected probe test still
fails and proves the gate refuses.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("BOILERPLATE_GATE_NESTED") == "1",
    reason="nested gate run: recursion guard",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROBE = REPO_ROOT / "tests" / "test_zz_gate_probe.py"


def _run_package() -> subprocess.CompletedProcess[str]:
    # sys.prefix is the venv root even when sys.executable is a symlink
    # into /usr (in that case resolve().parent.parent would be /usr, and
    # package.py would try venv.create("/usr", clear=True) -- destructive).
    venv_dir = Path(sys.prefix)
    assert (venv_dir / "pyvenv.cfg").exists(), (
        f"refusing to target {venv_dir}: not a venv (pyvenv.cfg missing)"
    )
    env = dict(os.environ)
    env["BOILERPLATE_VENV"] = str(venv_dir)
    env["BOILERPLATE_GATE_NESTED"] = "1"
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "package.py")],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_package_gate_refuses_to_build_when_tests_fail() -> None:
    # A backend kill between write_text() and the finally-unlink below
    # leaves the deliberately-failing probe behind, where the next outer
    # run would collect it as a real failing test. Self-heal first.
    PROBE.unlink(missing_ok=True)
    PROBE.write_text(
        "def test_deliberate_gate_probe_failure():\n"
        "    assert False, 'deliberate failure: package gate must refuse'\n",
        encoding="utf-8",
    )
    try:
        proc = _run_package()
    finally:
        PROBE.unlink(missing_ok=True)
    assert proc.returncode != 0, (
        f"package gate built (or passed) with a red suite\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    assert "refusing to build" in proc.stdout
    assert "building with PyInstaller" not in proc.stdout
