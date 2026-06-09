#!/bin/bash
set -e

echo "=== PawSignal 构建脚本 ==="
echo ""

# 使用 arm64 版 Python（Apple Silicon 原生）
ARM64_PYTHON=~/miniconda3-arm64/bin/python3

# 检查虚拟环境（如果已有 venv 但不是 arm64，则重建）
if [ -d "venv" ]; then
    VENV_ARCH=$(venv/bin/python3 -c "import platform; print(platform.machine())" 2>/dev/null || echo "unknown")
    if [ "$VENV_ARCH" != "arm64" ]; then
        echo "检测到旧 venv 非 arm64（$VENV_ARCH），重建..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    echo "创建 arm64 虚拟环境..."
    $ARM64_PYTHON -m venv venv
fi

source venv/bin/activate

# 安装依赖
echo "安装依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 清理旧构建
echo "清理旧构建..."
rm -rf build dist *.spec

# ── 打包 PawSignal（菜单栏 + 桌面挂件二合一）──────────────
echo ""
echo "打包 PawSignal（含菜单栏 + 桌面挂件）..."
pyinstaller \
    --name "PawSignal" \
    --windowed \
    --noconfirm \
    --clean \
    --icon "traffic_light.icns" \
    traffic_light.py

echo "构建完成 → dist/PawSignal.app"

# ── 生成 DMG ────────────────────────────────────────────────
echo ""
echo "正在生成 DMG..."
rm -rf /tmp/PawSignal_DMG_staging
mkdir /tmp/PawSignal_DMG_staging
cp -r "dist/PawSignal.app" "/tmp/PawSignal_DMG_staging/"

rm -f PawSignal.dmg
hdiutil create \
    -volname "PawSignal" \
    -srcfolder /tmp/PawSignal_DMG_staging \
    -ov -format UDZO \
    PawSignal.dmg

rm -rf /tmp/PawSignal_DMG_staging

echo ""
echo "=== 全部构建完成 ==="
echo "应用:       dist/PawSignal.app"
echo "DMG 安装包: PawSignal.dmg"
