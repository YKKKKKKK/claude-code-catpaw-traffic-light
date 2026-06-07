#!/bin/bash
set -e

echo "=== PawSignal 构建脚本 ==="
echo ""

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
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
