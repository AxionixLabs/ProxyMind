@echo off
setlocal enabledelayedexpansion

REM ==============================
REM Helix :: Windows 编译脚本
REM 说明：
REM 1. 先切回项目根目录再执行编译
REM 2. 编译前检查 nuitka 是否已安装
REM 3. 编译完成后删除 build 目录
REM ==============================

REM 切换到项目根目录（backend 的上一层）
cd /d "%~dp0.."

REM 检查 nuitka 是否已安装
where nuitka >nul 2>nul
if errorlevel 1 (
    echo ❌ 未检测到 nuitka，请先安装：
    echo    pip install nuitka
    exit /b 1
)

echo ✅ 已检测到 nuitka
echo 🚀 开始编译 Helix Windows Standalone...

nuitka ^
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
    echo ❌ Windows 编译失败
    exit /b 1
)

echo 🧹 清理 Windows 编译中间目录...
for /d %%D in ("schematic\supports\windows\*.build") do rmdir /s /q "%%D"

echo ✅ Helix Windows 编译完成
endlocal
