# UI Marker Chrome Extension

一个智能的 Chrome 扩展，可以自动识别并标记网页上的所有可操作 UI 元素。

## 功能特性

✨ **智能识别**：自动识别页面上的所有可交互元素（按钮、链接、表单等）
🎨 **分类标记**：根据元素类型自动分类并用不同颜色标记
📊 **实时统计**：显示找到的元素数量、标记数量和识别的类型数
🚀 **一键操作**：简单的两个按钮，标记和清除
🌐 **通用兼容**：适用于任何网站和框架（React、Vue、Angular等）

## 安装方法

### 方法 1：开发者模式安装（推荐）

1. **下载扩展文件**
   - 确保你已下载整个 `chrome-extension` 文件夹

2. **准备图标文件**
   - 扩展需要 PNG 格式的图标文件（16x16、32x32、48x48、128x128）
   - 你可以使用在线工具将 `icons/icon.svg` 转换为 PNG 格式
   - 推荐工具：https://cloudconvert.com/svg-to-png

3. **生成图标**
   ```bash
   # macOS 使用 ImageMagick（如果没有安装：brew install imagemagick）
   cd ~/clawd/skills/ui-marker/chrome-extension/icons
   convert icon.svg -resize 16x16 icon16.png
   convert icon.svg -resize 32x32 icon32.png
   convert icon.svg -resize 48x48 icon48.png
   convert icon.svg -resize 128x128 icon128.png
   ```

4. **打开 Chrome 扩展管理页面**
   - 在 Chrome 地址栏输入：`chrome://extensions/`
   - 或点击 Chrome 菜单 → 更多工具 → 扩展程序

5. **启用开发者模式**
   - 在扩展页面右上角，开启"开发者模式"开关

6. **加载扩展**
   - 点击"加载已解压的扩展程序"
   - 选择 `chrome-extension` 文件夹
   - 扩展将出现在扩展列表中

7. **确认安装**
   - 查看扩展列表中是否有 "UI Marker" 扩展
   - 如果有错误提示，检查图标文件是否都已生成

### 方法 2：打包安装

如果你想让其他用户安装这个扩展：

1. 使用 Chrome 的"打包扩展程序"功能
2. 选择 `chrome-extension` 文件夹
3. 生成 `.crx` 文件
4. 将 `.crx` 文件拖拽到 Chrome 扩展页面进行安装

## 使用方法

1. **打开扩展**
   - 点击 Chrome 工具栏上的 UI Marker 图标
   - 或右键点击页面，选择"检查元素"（开发者工具）

2. **标记 UI 元素**
   - 点击"✨ 开始标记"按钮
   - 扩展会自动扫描并标记页面上的所有可交互元素
   - 标记完成后会显示统计信息

3. **查看结果**
   - 页面上的可交互元素会显示洋红色边框和阴影
   - 弹出窗口会显示详细的统计信息
   - 浏览器控制台（F12）也会输出详细的分析结果

4. **清除标记**
   - 点击"🧹 清除标记"按钮
   - 所有标记会被清除，页面恢复正常

## 支持的元素类型

扩展可以自动识别以下类型的元素：

| 类型 | 颜色 | 说明 |
|------|------|------|
| button | 蓝色 | 普通按钮 |
| link | 蓝绿色 | 链接 |
| nav_back | 红色 | 返回/导航 |
| delete | 橙红色 | 删除操作 |
| upload | 紫色 | 上传/导入 |
| tool | 热粉色 | 工具选择 |
| view | 青色 | 查看详情 |
| refresh | 浅绿色 | 刷新/重新加载 |
| settings | 深蓝色 | 设置/配置 |
| more | 紫色 | 更多/菜单 |
| send | 绿色 | 发送/提交 |
| switch | 珊瑚色 | 切换/模式 |
| visualize | 红色 | 可视化/图表 |
| info | 红色 | 信息/统计 |
| stop | 深红色 | 停止/暂停 |
| publish | 深蓝色 | 发布/共享 |
| act | 灰色 | 执行/运行 |
| chat | 青色 | 对话/聊天 |
| correct | 绿色 | 正确反馈 |
| error | 红色 | 错误反馈 |
| other | 青色 | 其他交互元素 |

## 技术细节

### 扫描策略

扩展使用多种方法识别可交互元素：

1. **HTML 标签**：BUTTON、A、INPUT、SELECT、TEXTAREA 等
2. **事件属性**：onclick、role、tabindex、title
3. **CSS 特征**：cursor: pointer、pointer-events
4. **框架检测**：React、Vue、Angular 的特定属性
5. **SVG 元素**：支持 SVG 图形交互
6. **图片元素**：检测可点击的图片

### 分类逻辑

元素类型通过以下优先级识别：

1. **文本匹配**：分析 textContent、title、aria-label
2. **语义特征**：role 属性、href 属性
3. **JavaScript 特征**：事件监听器、框架属性
4. **结构特征**：HTML 标签、父元素交互性
5. **默认分类**：未匹配的按标签分类

### 标记样式

每个标记元素会显示：
- **边框**：3px 洋红色
- **阴影**：洋红色光晕
- **层级**：z-index: 999999
- **位置**：自动添加 position: relative（如果需要）

## 常见问题

### Q: 扩展无法标记某些元素？

A: 检查以下几点：
- 确保元素有交互特征（onclick、role、cursor: pointer 等）
- 某些动态加载的内容可能需要先触发加载
- 某些网站的安全策略可能限制脚本执行

### Q: 标记影响页面功能？

A: 标记只是添加了 CSS 样式，不会影响元素的原始功能。点击"清除标记"即可恢复。

### Q: 如何在新标签页自动标记？

A: 扩展设计为手动触发，避免自动干扰页面。你可以在每个需要的页面上手动点击标记按钮。

### Q: 支持移动版 Chrome 吗？

A: 支持 Android Chrome，但需要手动加载扩展包。

## 开发说明

### 文件结构

```
chrome-extension/
├── manifest.json          # 扩展配置文件
├── content.js            # 内容脚本（标记逻辑）
├── popup/
│   ├── popup.html        # 弹出界面
│   └── popup.js          # 弹出界面脚本
├── icons/
│   ├── icon.svg          # SVG 源图标
│   ├── icon16.png        # 16x16 图标
│   ├── icon32.png        # 32x32 图标
│   ├── icon48.png        # 48x48 图标
│   └── icon128.png       # 128x128 图标
└── README.md            # 本说明文件
```

### 修改建议

如果你想扩展功能，可以修改：

1. **添加新的元素类型**：编辑 `content.js` 中的 `classifyElement()` 函数
2. **调整标记样式**：修改 `content.js` 中的 `colors` 对象和标记代码
3. **添加更多选择器**：在 `content.js` 中的 `selectors` 数组中添加
4. **自定义界面**：修改 `popup/popup.html` 和 `popup/popup.js`

## 版本历史

- **v1.0.0** (2025-02-20)
  - 初始版本
  - 支持基本的 UI 元素识别和标记
  - 支持 20+ 种元素类型自动分类

## 许可证

MIT License

## 反馈与贡献

如有问题或建议，欢迎反馈！
