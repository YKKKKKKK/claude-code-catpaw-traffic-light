# Claude Code Traffic Light

Claude Code 菜单栏状态监控工具 —— 通过红绿灯直观显示 Claude Code 会话状态。

![macOS](https://img.shields.io/badge/macOS-supported-blue)
![Python](https://img.shields.io/badge/Python-3.9+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 功能特性

- **红绿灯状态指示**：在 macOS 菜单栏实时显示 Claude Code 会话状态
  - 🟢 绿灯常亮 — 会话进行中
  - 🟡 黄灯闪烁 — 需要确认（等待权限）
  - 🔴 红灯常亮 — 会话结束
- **多项目支持**：同时监控多个项目的 Claude Code 状态，一键切换
- **自动配置**：启动时自动配置 Claude Code hooks，退出时自动还原
- **配置备份**：安全备份原始 `settings.json`，确保不影响现有配置

## 安装

### 方式一：下载预编译应用（推荐）

前往 [Releases](https://github.com/DemoJj/claude-code-traffic-light/releases) 页面下载最新版本：

- **ClaudeTrafficLight.app.zip** — 直接解压使用
- **ClaudeTrafficLight-x.x.x.dmg** — 安装包

下载后将应用拖入 Applications 文件夹，双击启动即可。

### 方式二：从源码构建

```bash
# 克隆项目
git clone https://github.com/DemoJj/claude-code-traffic-light.git
cd claude-code-traffic-light

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 构建应用
python build.py
```

构建完成后，应用位于 `dist/ClaudeTrafficLight.app`。

### 方式三：直接运行 Python 脚本

```bash
python traffic_light.py
```

启动后，菜单栏会出现红绿灯图标，自动开始监控 Claude Code 状态。

### 退出

- 点击菜单栏红绿灯图标，选择「退出」
- 或按 `Ctrl+C` 终止进程

退出时会自动还原 Claude Code 的 `settings.json` 配置。

## 工作原理

1. **Hook 机制**：通过 Claude Code 的 hooks 功能，在会话状态变化时写入状态文件
2. **状态轮询**：定时读取状态文件，更新菜单栏显示
3. **闪烁效果**：黄灯状态通过定时器实现闪烁效果

### Hook 事件映射

| 事件 | 状态 |
|------|------|
| `SessionStart` | 红灯（会话开始） |
| `UserPromptSubmit` | 绿灯（用户输入） |
| `PreToolUse` (需权限工具) | 黄灯（等待确认） |
| `PostToolUse` (需权限工具) | 绿灯（工具执行完成） |
| `Stop` | 红灯（会话结束） |

## 配置说明

应用会自动配置以下路径：

- 状态文件：`~/.claude/traffic_light/`
- 配置备份：`~/.claude/traffic_light/settings_backup.json`
- 项目选择：`~/.claude/traffic_light/selected_project`

## 系统要求

- macOS 10.15+
- Python 3.9+（仅从源码构建时需要）

## 发布流程

### 自动发布（推荐）

1. 更新 `build.py` 中的 `VERSION` 版本号
2. 提交更改并打 tag：
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
3. GitHub Actions 会自动构建并发布到 Releases

### 手动发布

1. 在 GitHub Actions 页面手动触发 `Build and Release` 工作流
2. 输入版本号即可

## 贡献

欢迎提交 Issue 和 Pull Request！请参考以下规范：

- **Issue**：使用 Issue 模板提交 Bug 报告或功能建议
- **PR**：使用 PR 模板描述变更内容，确保代码通过测试

## License

MIT License
