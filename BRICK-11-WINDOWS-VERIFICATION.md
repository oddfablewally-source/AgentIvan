# BRICK-11-WINDOWS-VERIFICATION — portable Windows verification package

## Why this brick exists

Brick 11 (final Linux + Windows verification) certified Linux fully
(602/602, 35/35 workflow checks) but could not execute anything on
Windows: no Windows machine is available in this environment, so
Windows runtime verification remains pending.

Brick 11A exists to make the *verification artifact itself* portable
and honest, so the Windows run can actually happen on a real
Windows 10/11 machine. This is not a product brick: no AgentIvan
domain code was changed, no functionality was added, no tests were
added or removed, and the 602-test baseline is unchanged.

## What was wrong with the first package

The initial `agent-ivan-b11-windows-verify.zip` had four
packaging/documentation defects (none in the product):

1. `WINDOWS-VERIFY.txt` commands did not match the ZIP's directory
   layout (it said `python scripts\verify.py` from the package root,
   but `scripts/` lives under `agent-ivan/`).
2. `ivan_b11_workflow.py` hardcoded `/home/hatch/workspace/agent-ivan`
   in `sys.path`.
3. `verify.py` required the Ivan Core baseline via `IVAN_DIR` /
   `IVAN_BASELINE`, but the package supplied no baseline artifact.
4. A latent gate defect: `scripts/verify.py` only auto-installed
   pytest, PySide6, and requests. `reportlab` (a declared dependency
   in `requirements.txt`, used by the Brick 8 PDF exporter and its
   tests) was missing from the check list, so a genuinely fresh
   checkout failed 10 test modules at collection. This never surfaced
   on the dev machine because its venv already had reportlab.

Fix 4 touched `scripts/verify.py` (verification tooling only):
`IMPORT_CHECKS` now includes `reportlab`, installed from the pinned
`requirements.txt` specifier. No product code, no new tests, no
baseline count change. This is documented here explicitly, not folded
in silently.

## Package structure

The staging directory is `~/workspace/brick11-windows-verify/`
(the ZIP root plays the role of the package root):

```
<package-root>/
    WINDOWS-VERIFY.txt        literal cmd.exe procedure
    ivan_b11_workflow.py      35-check real-workflow script (portable)
    ivan_baseline.sha256      Ivan Core SHA-256 baseline (9 entries)
    ivan-core-brick/          the 9 baseline files the check verifies
    agent-ivan/               product checkout (no .venv, no caches)
        src/  tests/  scripts/  requirements.txt  ...
```

## Workflow portability

`ivan_b11_workflow.py` resolves the product checkout from its own file
location:

```python
_PACKAGE_ROOT = Path(__file__).resolve().parent
_AGENT_IVAN_ROOT = _PACKAGE_ROOT / "agent-ivan"
sys.path.insert(0, str(_AGENT_IVAN_ROOT))
```

No `/home/...`, no drive letters, no usernames, no `PYTHONPATH`
manipulation. The whole package may be moved to any directory.

## Baseline handling

`ivan_baseline.sha256` lists 9 paths relative to the Ivan Core root
(e.g. `src/app/product/ivan/agent.py`). The package ships those exact
9 files under `ivan-core-brick/`, preserving the baseline's semantics:
the same files, the same hashes, the same relative layout. The
documented procedure sets:

```
set IVAN_DIR=%CD%\..\ivan-core-brick
set IVAN_BASELINE=%CD%\..\ivan_baseline.sha256
```

so the existing gate finds them through its supported mechanism.
If the variables are wrong or missing, the gate fails loudly --
it never silently passes.

## Linux relocated-package validation (2026-09-20)

The complete staging package was copied to an arbitrary unrelated
path (`~/workspace/reloc-winverify/`, deliberately NOT
`~/workspace/agent-ivan`) and validated there:

- Fresh `python3 scripts/verify.py` with `IVAN_DIR`/`IVAN_BASELINE`
  pointing at the packaged copies: created a brand-new venv, pip
  installed pytest + PySide6 + reportlab from `requirements.txt`,
  ran the suite: **602/602 passed**, Ivan Core baseline 9/9 OK,
  `[verify] OK`.
- `agent-ivan/.venv/bin/python ivan_b11_workflow.py` from the package
  root: **35/35 checks PASS**, `WORKFLOW: ALL CHECKS PASSED`.

This proves no hardcoded workspace path remains, relative resolution
works, imports work from the relocated package, and the gate is
self-sufficient on a clean environment.

Correction to the Brick 11 report: the workflow script contains 35
checks, not 36 (35 `check()` calls; the earlier "36" was a miscount
in the report, not a script change). All pass.

## Machine-specific path audit

- No `/home/`, `/srv/`, `/mnt/`, or drive letters in executable
  package code. `verify.py`'s *default* `IVAN_DIR`/`IVAN_BASELINE`
  point at `~/workspace/...`; they are defaults only and are
  overridden by the documented procedure (missing baseline fails
  loudly, never silently).
- `UNIVERSAL-BOILERPLATE.md` mentions `/home/hatch/workspace/boilerplate/`
  as the locked boilerplate location -- documentation, not executable.
- `build_windows.bat` (a leftover LocalAI-Desktop/PyInstaller artifact)
  was inspected and deliberately excluded from the package.

## Prerequisites for the Windows run

Windows 10/11 64-bit, Python 3.12 (python.org, "Add python.exe to
PATH" checked), one-time internet access for pip, ~1 GB disk.
Exact commands are in `WINDOWS-VERIFY.txt` (cmd.exe syntax).

## Current status

- Linux: fully verified (602/602 suite, 35/35 workflow, boilerplate
  untouched, no product defects).
- Relocated Linux package validation: passed (see above).
- **Windows runtime verification has NOT yet been performed.**
  No Windows machine has executed any of this. Nothing in this brick
  claims otherwise.

Next action: put the corrected ZIP on an actual Windows 10/11 machine
and follow `WINDOWS-VERIFY.txt` literally.
