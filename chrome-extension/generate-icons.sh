#!/bin/bash

# UI Marker Chrome Extension - 图标生成脚本
# 此脚本使用 ImageMagick 从 SVG 生成不同尺寸的 PNG 图标

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ICONS_DIR="$SCRIPT_DIR/icons"
SVG_FILE="$ICONS_DIR/icon.svg"

echo "🎨 UI Marker - 图标生成脚本"
echo "================================"

# 检查 ImageMagick 是否已安装
if ! command -v convert &> /dev/null; then
    echo "❌ 错误: ImageMagick 未安装"
    echo ""
    echo "请安装 ImageMagick："
    echo "  macOS: brew install imagemagick"
    echo "  Ubuntu: sudo apt-get install imagemagick"
    echo "  Windows: 从 https://imagemagick.org 下载安装"
    exit 1
fi

# 检查 SVG 文件是否存在
if [ ! -f "$SVG_FILE" ]; then
    echo "❌ 错误: 找不到 SVG 图标文件: $SVG_FILE"
    exit 1
fi

echo "✅ 检查通过: ImageMagick 已安装"
echo "📁 SVG 文件: $SVG_FILE"
echo ""

# 生成不同尺寸的图标
SIZES=(16 32 48 128)

for size in "${SIZES[@]}"; do
    output_file="$ICONS_DIR/icon${size}.png"

    echo "🔄 生成 ${size}x${size} 图标..."
    convert "$SVG_FILE" -resize "${size}x${size}" -background none "$output_file"

    if [ -f "$output_file" ]; then
        echo "✅ 已生成: $output_file"
    else
        echo "❌ 生成失败: $output_file"
        exit 1
    fi
done

echo ""
echo "🎉 图标生成完成！"
echo ""
echo "已生成的图标文件："
for size in "${SIZES[@]}"; do
    file="$ICONS_DIR/icon${size}.png"
    if [ -f "$file" ]; then
        size_info=$(file "$file" | grep -o '[0-9]* x [0-9]*')
        echo "  ✓ icon${size}.png ($size_info)"
    fi
done

echo ""
echo "现在你可以加载扩展到 Chrome 了！"
echo "1. 打开 chrome://extensions/"
echo "2. 启用开发者模式"
echo "3. 点击 '加载已解压的扩展程序'"
echo "4. 选择此文件夹: $SCRIPT_DIR"
