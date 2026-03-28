@echo off
setlocal

for %%I in ("%~dp0..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"
if errorlevel 1 exit /b 1

set "NUITKA_PATH="
if exist "%ROOT_DIR%\venv\Scripts\nuitka.cmd" set "NUITKA_PATH=%ROOT_DIR%\venv\Scripts\nuitka.cmd"
if not defined NUITKA_PATH for /f "delims=" %%I in ('where.exe nuitka 2^>nul') do if not defined NUITKA_PATH set "NUITKA_PATH=%%I"

if not defined NUITKA_PATH (
    echo [ERROR] Nuitka was not found.
    echo    pip install nuitka
    exit /b 1
)

echo [INFO] Nuitka: %NUITKA_PATH%
echo [INFO] Building Helix Windows Standalone...

call "%NUITKA_PATH%" --mode=standalone --product-name=Helix --product-version=1.0.0 --windows-icon-from-ico=schematic/resources/icons/helix_windows_icn.ico --include-data-dir=backend/web=web --include-data-dir=backend/requires/windows=requires/windows --show-progress --show-memory --assume-yes-for-downloads --output-dir=schematic/supports/windows backend/helix.py

set "BUILD_RC=%ERRORLEVEL%"
if not "%BUILD_RC%"=="0" goto build_failed

echo [INFO] Cleaning Windows build directories...
if exist "schematic\supports\windows\*.build" for /d %%D in ("schematic\supports\windows\*.build") do rmdir /s /q "%%~fD"

echo [OK] Helix Windows build completed.
endlocal
goto :eof

:build_failed
echo [ERROR] Windows build failed.
exit /b %BUILD_RC%
