@echo off
chcp 65001 >nul 2>&1

echo ==================================================
echo   AssetMaker - Nuitka Build Tool
echo ==================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python not found
    pause
    exit /b 1
)

:: Create virtual environment if not exists
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

:: Activate virtual environment
echo Activating virtual environment...
call .venv\Scripts\activate.bat

:: Install dependencies
echo Installing dependencies...
pip install nuitka zstandard ordered-set -q
pip install PyQt6 opencv-python numpy -q

:: Run build script
echo.
echo Starting build...
python build.py

:: Deactivate virtual environment
call deactivate

echo.
pause
