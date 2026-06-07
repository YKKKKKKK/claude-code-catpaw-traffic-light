# 🚦 PawSignal

> macOS 菜单栏红绿灯 —— 实时监控 Claude Code 和 CatPaw Agent 的工作状态

![macOS](https://img.shields.io/badge/macOS-Apple%20Silicon-blue?logo=apple)
![Version](https://img.shields.io/badge/version-v2.1.0-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

## ✨ 它是什么？

当你让 Claude Code 或 CatPaw 帮你写代码、执行命令时，很难一眼判断它现在是在跑、还是已经完成了。

**PawSignal** 在 macOS 菜单栏显示一组红绿灯，让你随时知道 Agent 的状态：

| 状态 | 含义 |
|------|------|
| 🟢 绿灯常亮 | 空闲 / 完成 / 成功，等待你的指令 |
| 🟡 黄灯闪烁 | Agent 正在执行 / 思考 / 调用工具 |
| 🔴 红灯常亮 | 失败 / 取消 / 出现异常 |

---

## 📦 安装（推荐）

无需安装 Python 或任何依赖，下载即用。

1. 前往 [Releases](https://github.com/DemoJj/claude-code-traffic-light/releases) 页面
2. 下载最新版 **`PawSignal-v2.1.0.dmg`**
3. 打开 DMG，将 **PawSignal.app** 拖入「应用程序」文件夹
4. 双击启动，菜单栏出现红绿灯图标即表示运行成功 🎉

> ⚠️ 当前版本仅支持 **Apple Silicon（M1/M2/M3/M4）** Mac。Intel Mac 暂不支持。

---

## 🖥 界面预览

菜单栏红绿灯：

```
⚫ ⚫ 🟢   空闲中
⚫ 🟡 ⚫   执行中（黄灯闪烁）
🔴 ⚫ ⚫   出错了
```

桌面悬浮挂件（可拖动，可折叠）：

- 三盏灯实时显示当前状态
- 黄灯时底部自动出现实时计时 `⏱ 执行中 42 秒`
- 底部显示今日执行次数和总时长

---

## 🔧 功能一览

### 核心监控
- **Claude Code**：通过 hooks 自动注入，监听会话全生命周期
- **CatPaw JetBrains 插件**：实时解析 IDEA 日志，自动覆盖所有已安装的 JetBrains IDE
- **CatPaw VSCode 客户端**：监听 Hook Log 事件
- **多源合并**：红 > 黄 > 绿，任一 Agent 在忙就亮黄灯

### 桌面挂件
- 🖱 **可拖动**：随意放置在屏幕任意位置，位置重启后自动恢复
- 📌 **越界保护**：切换显示器或 Space 后自动归位，不会飞出屏幕
- 🗂 **可折叠**：点击标题栏折叠/展开挂件，节省空间
- ⏱ **实时计时**：执行中自动显示已用时间（秒/分钟）
- 📐 **三档尺寸**：小 / 中（默认）/ 大，菜单中随时切换
- 🔢 **多会话显示**：同时监控多个项目或来源时，底部小点逐一显示各来源状态

### 菜单栏
- 今日执行次数 + 总时长（不足1分钟显示秒数）
- 黄灯执行中时，实时显示本次已用时长
- 支持隐藏菜单栏图标（只保留挂件）

### 其他
- 🚀 **开机自启动**：一键开启/关闭，无需手动配置 LaunchAgents
- 📁 **多项目支持**：多个 Claude Code 项目分别记录状态，菜单中切换
- 🔍 **监控模式**：仅 Claude Code / 仅 CatPaw / 两者同时监控

---

## 📊 工作原理

### Claude Code 监控

启动时自动在 `~/.claude/settings.json` 注入 hooks，退出时自动还原：

| Hook 事件 | 灯色 |
|-----------|------|
| `SessionStart` / `Stop` / `SessionEnd` | 🟢 绿灯 |
| `UserPromptSubmit` / `PermissionRequest` / `PreToolUse` / `PostToolUse` | 🟡 黄灯 |

### CatPaw 监控

后台线程实时 tail 日志文件，无需额外配置：

| 日志状态 | 灯色 | 说明 |
|----------|------|------|
| `Status: running` | 🟡 黄灯 | Agent 执行中 |
| `Status: completed` | 🟡 → 🟢 | 2 秒后变绿，防止连续工具调用误判 |
| `Status: cancelled/failed` | 🔴 红灯 | 10 秒保护期，屏蔽取消后的噪音事件 |
| 60 秒无事件 | 🟢 绿灯 | 超时兜底 |

---

## 🗂 配置文件位置

| 文件 | 说明 |
|------|------|
| `~/.claude/traffic_light/daily_stats.json` | 今日统计（次数 + 时长），重启不丢失，次日自动重置 |
| `~/.claude/traffic_light/widget_position` | 挂件位置 |
| `~/.claude/traffic_light/widget_size` | 挂件尺寸 |
| `~/.claude/traffic_light/monitor_mode` | 监控模式 |
| `~/Library/LaunchAgents/com.pawsignal.traffic-light.plist` | 开机自启配置 |
| `~/Library/Logs/PawSignal.log` | 运行日志 |

---

## 🔨 从源码构建

```bash
git clone https://github.com/DemoJj/claude-code-traffic-light.git
cd claude-code-traffic-light

# 创建虚拟环境并安装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 直接运行（开发模式）
python traffic_light.py

# 打包成 .app
pyinstaller --noconfirm --windowed --name PawSignal \
  --osx-bundle-identifier com.pawsignal.traffic-light \
  --icon PawSignal.icns traffic_light.py
```

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

- **Bug 反馈**：请附上 `~/Library/Logs/PawSignal.log` 的相关内容
- **功能建议**：在 Issue 中描述使用场景

---

## License

MIT License
