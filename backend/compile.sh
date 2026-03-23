#!/usr/bin/env bash
set -euo pipefail

# ==============================
# Helix :: macOS 编译脚本
# 说明：
# 1. 先切回项目根目录再执行编译
# 2. 编译前检查 nuitka 是否已安装
# 3. 编译完成后删除 build、dist 目录
# ==============================

# 切换到项目根目录
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# 检查 nuitka 是否已安装
if ! command -v nuitka >/dev/null 2>&1; then
  echo "❌ 未检测到 nuitka，请先安装："
  echo "   pip install nuitka"
  exit 1
fi

echo "✅ 已检测到 nuitka：$(command -v nuitka)"
echo "🚀 开始编译 Helix macOS App Bundle..."

nuitka \
  --macos-create-app-bundle \
  --macos-app-name=Helix \
  --macos-app-version=1.0.0 \
  --macos-app-icon=schematic/resources/images/macos/helix_macos_icn.png \
  --include-data-dir=backend/web=web \
  --include-data-dir=backend/requires/macos=requires/macos \
  --show-progress \
  --output-dir=schematic/supports/MacOS \
  backend/helix.py

echo "🧹 清理 macOS 编译中间目录..."
rm -rf build dist

echo "✅ Helix macOS 编译完成"
