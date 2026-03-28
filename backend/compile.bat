@echo off
setlocal

REM ==============================
REM Helix :: Windows build script
REM 1. Switch to project root before compiling
REM 2. Check whether nuitka is available
REM 3. Remove build directories after compiling
REM ==============================

REM Switch to project root (parent of backend)
for %%I in ("%~dp0..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"
if errorlevel 1 exit /b 1

REM Check whether nuitka is installed
set "NUITKA_PATH="
if exist "%ROOT_DIR%\venv\Scripts\nuitka.cmd" (
    set "NUITKA_PATH=%ROOT_DIR%\venv\Scripts\nuitka.cmd"
) else (
    for /f "delims=" %%I in ('where.exe nuitka 2^>nul') do if not defined NUITKA_PATH set "NUITKA_PATH=%%I"
)

if not defined NUITKA_PATH (
    echo [ERROR] Nuitka was not found.
    echo    pip install nuitka
    exit /b 1
)

echo [INFO] Nuitka: %NUITKA_PATH%
echo [INFO] Building Helix Windows Standalone...

call "%NUITKA_PATH%" ^
  --mode=standalone ^
  --product-name=Helix ^
  --product-version=1.0.0 ^
  --windows-icon-from-ico=schematic/resources/icons/helix_windows_icn.ico ^
  --include-data-dir=backend/web=web ^
  --include-data-dir=backend/requires/windows=requires/windows ^
  --show-progress ^
  --show-memory ^
  --assume-yes-for-downloads ^
  --output-dir=schematic/supports/windows ^
  backend/helix.py

if errorlevel 1 (
    echo [ERROR] Windows build failed.
    exit /b 1
)

echo [INFO] Cleaning Windows build directories...
for /d %%D in ("schematic\supports\windows\*.build") do if exist "%%~fD" rmdir /s /q "%%~fD"

echo [OK] Helix Windows build completed.
endlocal
