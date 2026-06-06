# Claude Code & CatPaw Traffic Light

macOS 菜单栏状态监控工具 —— 通过红绿灯直观显示 Claude Code 和 CatPaw 的 Agent 工作状态。

![macOS](https://img.shields.io/badge/macOS-supported-blue)
![Python](https://img.shields.io/badge/Python-3.9+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## 功能特性

- **红绿灯状态指示**：在 macOS 菜单栏实时显示 Agent 工作状态
  - 🟢 绿灯常亮 — 空闲 / 完成 / 成功
  - 🟡 黄灯闪烁 — Agent 正在执行 / 思考 / 调用工具或命令
  - 🔴 红灯常亮 — 失败 / 拒绝 / 取消 / 异常
- **双源监控**：同时监控 Claude Code（命令行）和 CatPaw（JetBrains 插件），任一 Agent 工作即亮黄灯
- **监控模式切换**：支持仅监控 Claude Code、仅监控 CatPaw、或两者同时监控
- **多 IDE 支持**：CatPaw 监控自动覆盖 IntelliJ IDEA、PyCharm、WebStorm 等所有已安装的 JetBrains IDE
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

适合开发调试阶段，无需构建 `.app`，修改代码后立即生效。

```bash
# 克隆项目
git clone https://github.com/DemoJj/claude-code-traffic-light.git
cd claude-code-traffic-light

# 创建虚拟环境并安装依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 运行
python traffic_light.py
```

或直接使用 venv 内的 Python（无需 activate）：

```bash
venv/bin/python traffic_light.py
```

> **注意**：系统自带的 Python 3.9 可能因编译环境缺失而无法安装 `rumps` 依赖，推荐使用上述 venv 方式。

启动后，菜单栏会出现红绿灯图标，自动开始监控状态。

### 退出

- 点击菜单栏红绿灯图标，选择「退出」
- 或按 `Ctrl+C` 终止进程

退出时会自动还原 Claude Code 的 `settings.json` 配置。

## 工作原理

### Claude Code 监控

通过 Claude Code 的 hooks 功能，在会话状态变化时写入状态文件，定时读取并更新菜单栏显示。

#### Hook 事件映射

| 事件 | 状态 |
|------|------|
| `SessionStart` | 🟢 绿灯（会话开始，等待输入） |
| `UserPromptSubmit` | 🟡 黄灯（用户提交，AI 处理中） |
| `PermissionRequest` | 🟡 黄灯（等待权限确认） |
| `PreToolUse` (需权限工具) | 🟡 黄灯（工具调用中） |
| `PostToolUse` (需权限工具) | 🟡 黄灯（工具执行完，继续处理） |
| `Stop` | 🟢 绿灯（正常结束） |
| `SessionEnd` | 🟢 绿灯（会话结束） |

### CatPaw 监控

通过后台线程实时监听 CatPaw 写入的 IDEA 日志文件（`~/Library/Logs/JetBrains/*/idea.log`）中的 `AgentTabService` 状态行，无需任何额外配置。自动扫描并监听 `~/Library/Logs/JetBrains/` 目录下**所有** JetBrains IDE 的日志，同时支持 IntelliJ IDEA、PyCharm、WebStorm 等。

#### 状态判断逻辑

| 日志事件 | 显示状态 | 说明 |
|------|------|------|
| `Status: running` | 🟡 黄灯 | Agent 正在执行工具或思考 |
| `Status: completed` | 🟡 黄灯（短暂）→ 🟢 绿灯 | 等待 2 秒确认无新任务后变绿，避免多工具连续执行时误判为完成 |
| `Status: cancelled` / `failed` | 🔴 红灯 | 进入 10 秒保护期，屏蔽 CatPaw 取消后自动发出的 `running` 事件 |
| 超过 60 秒无任何事件 | 🟢 绿灯 | 超时兜底，防止黄灯卡死 |

### 双源合并规则

同时监控时，优先级为 **红 > 黄 > 绿**：只要有任一 Agent 在忙，灯就亮黄；只有两者都空闲，才亮绿灯。

## 监控模式

点击菜单栏图标可切换监控模式：

| 模式 | 说明 |
|------|------|
| 🔀 两者都监控 | 同时监控 Claude Code 和 CatPaw（默认） |
| 🤖 仅 Claude Code | 只监控命令行 Claude Code |
| 🐾 仅 CatPaw | 只监控 JetBrains IDE 插件 CatPaw（支持 IDEA / PyCharm / WebStorm 等） |

## 配置说明

应用会自动配置以下路径：

- 状态文件：`~/.claude/traffic_light/`
- 配置备份：`~/.claude/traffic_light/settings_backup.json`
- 项目选择：`~/.claude/traffic_light/selected_project`
- 监控模式：`~/.claude/traffic_light/monitor_mode`

## 系统要求

- macOS 10.15+
- Python 3.9+（仅从源码运行时需要）
- CatPaw IDEA 插件（可选，监控 CatPaw 时需要）

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
