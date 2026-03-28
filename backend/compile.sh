#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

NUITKA_BIN=""
if [ -x "$ROOT_DIR/venv/bin/nuitka" ]; then
  NUITKA_BIN="$ROOT_DIR/venv/bin/nuitka"
elif command -v nuitka >/dev/null 2>&1; then
  NUITKA_BIN="$(command -v nuitka)"
fi

if [ -z "$NUITKA_BIN" ]; then
  echo "❌ 未检测到 nuitka，请先安装："
  echo "   pip install nuitka"
  exit 1
fi

echo "✅ 已检测到 nuitka：$NUITKA_BIN"
echo "🚀 开始编译 Helix macOS App Bundle..."

"$NUITKA_BIN" \
  --macos-create-app-bundle \
  --macos-app-name=Helix \
  --macos-app-version=1.0.0 \
  --macos-app-icon=schematic/resources/images/macos/helix_macos_icn.png \
  --include-data-dir=backend/web=web \
  --include-data-dir=backend/requires/macos=requires/macos \
  --show-progress \
  --output-dir=schematic/supports/macos \
  backend/helix.py

echo "🧹 清理 macOS 编译中间目录..."
rm -rf schematic/supports/macos/*.build schematic/supports/macos/*.dist

echo "✅ Helix macOS 编译完成"
