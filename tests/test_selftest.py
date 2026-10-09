"""
Regression tests for the `main.py --self-test` headless smoke test.

Runs the real entry point in a subprocess (never in the test process,
so Qt application singletons cannot collide) with a redirected HOME
so no real user data is touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_self_test(home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "main.py", "--self-test"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_self_test_exits_zero_and_reports_ok(tmp_path: Path) -> None:
    proc = _run_self_test(tmp_path / "home")
    assert proc.returncode == 0, (
        f"self-test failed\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}"
    )
    assert "self-test OK" in proc.stdout


def test_self_test_is_hermetic(tmp_path: Path) -> None:
    """The self-test must not write outside the redirected HOME."""
    fake_home = tmp_path / "home"
    real_marker = tmp_path / "real_home_probe"
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.run(
        [sys.executable, "main.py", "--self-test"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0
    assert not real_marker.exists()
    # The redirected home *was* used: data dir exists under it.
    assert (fake_home / ".local" / "share").exists()
