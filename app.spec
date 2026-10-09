# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for LocalAI Desktop.

Builds a onedir Windows executable with all resources bundled.

To build:
    pyinstaller app.spec --noconfirm

Or use the build script:
    scripts\\build_windows.bat

The output will be in dist/LocalAI-Desktop/
"""

import os
import sys
from pathlib import Path

# Project root is the directory containing this spec file
project_root = Path(os.getcwd())

a = Analysis(
    ['main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # Bundle resources directory
        ('src/app/resources/*', 'src/app/resources'),
    ],
    hiddenimports=[
        # Ensure PySide6 modules are included
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Exclude unnecessary modules to reduce size
        'tkinter',
        'unittest',
        'pydoc',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LocalAI-Desktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI application: no console window
    icon=None,  # Add 'src/app/resources/icon.ico' here if you have an icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LocalAI-Desktop',
)
