@echo off
REM ===========================================================================
REM Build script for AgentIvan Windows executable
REM
REM Usage:
REM   scripts\build_windows.bat
REM
REM Prerequisites:
REM   - Python 3.11+ installed and on PATH
REM   - Virtual environment created and activated (or install globally)
REM   - Dependencies installed: pip install -r requirements.txt
REM
REM Output:
REM   dist\AgentIvan\AgentIvan.exe
REM ===========================================================================

setlocal

echo.
echo ============================================
echo   AgentIvan - Windows Build
echo ============================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not on PATH.
    echo Please install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

REM Check if PyInstaller is installed
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found. Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
)

REM Clean previous builds
echo Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM Build with PyInstaller using the spec file
echo.
echo Building executable...
pyinstaller app.spec --noconfirm --clean

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. Check the output above for errors.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build successful!
echo ============================================
echo.
echo Output location: dist\AgentIvan\
echo Executable:      dist\AgentIvan\AgentIvan.exe
echo.
echo You can distribute the entire dist\AgentIvan\ folder.
echo.

pause
endlocal
