#!/usr/bin/env python3
"""
Claude Code / CatPaw 顶部栏红绿灯 —— Python 版
三个灯同时显示，根据状态变化：
- 绿灯常亮：空闲 / 完成 / 成功
- 黄灯闪烁：Agent 正在执行 / 思考 / 调用工具
- 红灯常亮：失败 / 拒绝 / 取消 / 异常
"""
import json
import sys, os
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
import shutil
import atexit
import signal
import time
import threading
import rumps
from pathlib import Path

# ---------- 配置 ----------
BASE_DIR = os.path.expanduser("~/.claude/traffic_light")
STATE_DIR = BASE_DIR
CONFIG_PATH = os.path.expanduser("~/.claude/settings.json")
BACKUP_PATH = os.path.join(BASE_DIR, "settings_backup.json")
SELECTED_FILE = os.path.join(BASE_DIR, "selected_project")
POLL_INTERVAL = 0.3       # 轮询间隔（秒）
BLINK_INTERVAL = 0.5      # 闪烁间隔（秒）
MENU_REFRESH_INTERVAL = 2 # 菜单刷新间隔（秒），避免频繁重建

# 红绿灯相关的 hook 命令标识（用于清理旧条目）
TRAFFIC_MARKER = "traffic_light_app"

# 灯的符号
LIGHT_ON = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
LIGHT_OFF = "⚫"

# ---------- CatPaw 配置 ----------
# IDEA 日志路径（实时写入，用于监听 CatPaw Agent 状态变化）
IDEA_LOG_PATH = os.path.expanduser("~/Library/Logs/JetBrains/IntelliJIdea2024.1/idea.log")
# CatPaw 空闲超时（秒）：超过此时间无 running/completed 事件 → 视为空闲
CATPAW_IDLE_TIMEOUT = 60

# 监控模式
MONITOR_MODE_CLAUDE = "claude"    # 仅监控 Claude Code
MONITOR_MODE_CATPAW = "catpaw"    # 仅监控 CatPaw
MONITOR_MODE_BOTH   = "both"      # 同时监控两者（任一活跃则亮灯）
MONITOR_MODE_FILE   = os.path.join(BASE_DIR, "monitor_mode")


# ---------- 监控模式管理 ----------
def get_monitor_mode():
    """获取当前监控模式，默认为 both"""
    try:
        if Path(MONITOR_MODE_FILE).exists():
            mode = Path(MONITOR_MODE_FILE).read_text().strip()
            if mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
                return mode
    except Exception:
        pass
    return MONITOR_MODE_BOTH


def set_monitor_mode(mode):
    """设置监控模式"""
    try:
        Path(MONITOR_MODE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(MONITOR_MODE_FILE).write_text(mode)
    except Exception:
        pass


# ---------- CatPaw 状态监听（基于 idea.log 实时日志） ----------
# 关键日志行示例：
#   Tab状态已更新，ID: xxx, Status: running    → Agent 正在执行 → 黄灯
#   Tab状态已更新，ID: xxx, Status: completed  → Agent 完成     → 绿灯
#
# 后台线程持续 tail idea.log，解析到状态变化后更新全局缓存变量。
# 主线程轮询时只读缓存，不做任何 IO。

_catpaw_state_cache = "green"      # 全局缓存：当前 CatPaw 状态
_catpaw_last_event_time = 0.0      # 最后一次收到事件的时间戳
_catpaw_log_thread = None          # 后台监听线程
_catpaw_pending_green_at = 0.0     # completed 事件时间，延迟后才切绿灯
_catpaw_cancelled_at = 0.0         # cancelled 事件时间，保护期内忽略 running
CATPAW_GREEN_DELAY = 2.0           # completed 后等待此秒数，确认没有新 running 才变绿
CATPAW_CANCEL_PROTECT = 10.0       # cancelled 后保护期（秒），期间 running 不覆盖红灯


def _find_idea_log():
    """自动查找 IDEA 日志文件，支持不同版本的 IntelliJ"""
    # 先用配置的路径
    if Path(IDEA_LOG_PATH).exists():
        return IDEA_LOG_PATH
    # 自动搜索其他版本
    base = Path("~/Library/Logs/JetBrains").expanduser()
    if base.exists():
        candidates = sorted(base.glob("*/idea.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def _catpaw_log_watcher():
    """后台线程：持续监听 idea.log 中的 AgentTabService 状态行"""
    global _catpaw_state_cache, _catpaw_last_event_time, _catpaw_pending_green_at, _catpaw_cancelled_at

    log_path = _find_idea_log()
    if not log_path:
        return

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            # 先跳到文件末尾，只处理新增日志
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    # 检测日志轮转（文件被替换）
                    try:
                        if os.stat(log_path).st_ino != os.fstat(f.fileno()).st_ino:
                            break  # 文件已轮转，退出重新打开
                    except Exception:
                        pass
                    continue

                # 匹配关键行：Tab状态已更新，Status: running / completed
                if "AgentTabService" in line and "Tab状态已更新" in line:
                    _catpaw_last_event_time = time.time()
                    if "Status: running" in line:
                        # running：若在取消保护期内，忽略此事件（CatPaw 取消后会立刻发一个 running）
                        if time.time() - _catpaw_cancelled_at < CATPAW_CANCEL_PROTECT:
                            pass  # 保护期内忽略
                        else:
                            _catpaw_state_cache = "yellow"
                            _catpaw_pending_green_at = 0.0
                    elif "Status: completed" in line:
                        # completed 后进入 pending 等待期，CATPAW_GREEN_DELAY 秒无新 running 才变绿
                        # 若在取消保护期内，completed 不清除红灯
                        if time.time() - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                            _catpaw_pending_green_at = time.time()
                    elif "Status: failed" in line or "Status: error" in line or "Status: cancelled" in line:
                        _catpaw_state_cache = "red"
                        _catpaw_pending_green_at = 0.0
                        _catpaw_cancelled_at = time.time()  # 记录取消时间，开始保护期

    except Exception:
        pass

    # 线程退出后标记为 None，下次调用时重启
    global _catpaw_log_thread
    _catpaw_log_thread = None


def _ensure_log_watcher():
    """确保后台监听线程在运行"""
    global _catpaw_log_thread
    if _catpaw_log_thread is None or not _catpaw_log_thread.is_alive():
        _catpaw_log_thread = threading.Thread(target=_catpaw_log_watcher, daemon=True)
        _catpaw_log_thread.start()


def get_catpaw_state():
    """
    返回 CatPaw 当前状态（从后台日志监听线程的缓存中读取）。
    返回值: "green" / "yellow" / "red"

    判断逻辑：
    - running 事件  → 立即返回 yellow
    - completed 事件 → 等待 CATPAW_GREEN_DELAY 秒，期间若无新 running，则变绿
      （因为 CatPaw 每个工具执行后会 completed→running 连续切换，只有最后一个
       completed 后不再有 running，才说明 Agent 真正空闲）
    - 超过 CATPAW_IDLE_TIMEOUT 秒无任何事件 → 强制绿灯
    """
    global _catpaw_state_cache, _catpaw_pending_green_at, _catpaw_cancelled_at

    _ensure_log_watcher()

    now = time.time()

    # 检查 pending green：completed 后等足 CATPAW_GREEN_DELAY 秒且没有新 running → 变绿
    if _catpaw_pending_green_at > 0 and _catpaw_state_cache == "yellow":
        if now - _catpaw_pending_green_at >= CATPAW_GREEN_DELAY:
            _catpaw_state_cache = "green"
            _catpaw_pending_green_at = 0.0

    # 取消保护期结束后，红灯自动变绿（用户看完红灯提示后恢复空闲）
    if _catpaw_state_cache == "red" and _catpaw_cancelled_at > 0:
        if now - _catpaw_cancelled_at > CATPAW_CANCEL_PROTECT:
            _catpaw_state_cache = "green"
            _catpaw_cancelled_at = 0.0

    # 超时保护：黄灯持续超过 CATPAW_IDLE_TIMEOUT 秒 → 强制绿灯（防止卡死）
    if _catpaw_last_event_time > 0:
        if now - _catpaw_last_event_time > CATPAW_IDLE_TIMEOUT and _catpaw_state_cache == "yellow":
            _catpaw_state_cache = "green"

    return _catpaw_state_cache


def get_state_file(project_name=None):
    """获取指定项目的状态文件路径"""
    if project_name is None:
        project_name = get_selected_project()
    return os.path.join(STATE_DIR, f"{project_name}.state")


def get_selected_project():
    """获取当前选中的项目名，默认选中第一个活跃项目"""
    try:
        if Path(SELECTED_FILE).exists():
            return Path(SELECTED_FILE).read_text().strip()
    except Exception:
        pass
    projects = list_active_projects()
    return projects[0] if projects else "default"


def set_selected_project(project_name):
    """设置当前选中的项目"""
    try:
        Path(SELECTED_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SELECTED_FILE).write_text(project_name)
    except Exception:
        pass


def list_active_projects():
    """列出所有有状态文件的项目"""
    try:
        Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
        return sorted(f.stem for f in Path(STATE_DIR).glob("*.state"))
    except Exception:
        return []


def backup_config():
    """备份原始配置文件"""
    if Path(CONFIG_PATH).exists():
        try:
            shutil.copy2(CONFIG_PATH, BACKUP_PATH)
            print(f"已备份原始配置: {BACKUP_PATH}")
            return True
        except Exception as e:
            print(f"备份配置失败: {e}")
    return True


def restore_config():
    """还原备份的配置文件并清理所有新增文件"""
    # 还原配置
    if Path(BACKUP_PATH).exists():
        try:
            shutil.copy2(BACKUP_PATH, CONFIG_PATH)
            Path(BACKUP_PATH).unlink()
            print(f"已还原原始配置: {CONFIG_PATH}")
        except Exception as e:
            print(f"还原配置失败: {e}")

    # 清理状态目录
    if Path(STATE_DIR).exists():
        try:
            shutil.rmtree(STATE_DIR)
            print(f"已清理状态目录: {STATE_DIR}")
        except Exception as e:
            print(f"清理状态目录失败: {e}")

    # 清理选择文件
    if Path(SELECTED_FILE).exists():
        try:
            Path(SELECTED_FILE).unlink()
            print(f"已清理选择文件: {SELECTED_FILE}")
        except Exception as e:
            print(f"清理选择文件失败: {e}")

    # 清理旧版单文件（兼容）
    old_file = os.path.expanduser("~/.claude/.traffic_light")
    if Path(old_file).exists():
        try:
            Path(old_file).unlink()
            print(f"已清理旧版状态文件: {old_file}")
        except Exception:
            pass


def _is_traffic_hook(entry):
    """判断一个 hook 条目是否属于红绿灯"""
    return any(TRAFFIC_MARKER in h.get("command", "") for h in entry.get("hooks", []))


def _make_hook_entry(command, matcher=""):
    """创建一个符合 Claude Code 格式的 hook 条目"""
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command}],
    }


# ---------- 自动配置 Hook ----------
def configure_hooks():
    """安全地将所需的 hook 合并到 ~/.claude/settings.json"""
    Path(CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)

    backup_config()

    # 读取现有配置
    config = {}
    if Path(CONFIG_PATH).exists():
        try:
            config = json.loads(Path(CONFIG_PATH).read_text())
        except Exception:
            config = {}

    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    # hook 命令：根据项目目录动态生成状态文件路径
    def _hook_cmd(state):
        marker = f"# {TRAFFIC_MARKER}"
        return f'project=$(basename "${{CLAUDE_PROJECT_DIR:-$PWD}}") && mkdir -p {STATE_DIR} && echo {state} > {STATE_DIR}/"$project".state {marker}'

    # 只对需要权限的工具类型触发黄灯
    permission_tools = "Bash|Write|Edit|NotebookEdit|WebFetch"

    desired = {
        "SessionStart":       [_make_hook_entry(_hook_cmd("green"))],   # 会话开始 → 绿灯（空闲，等待输入）
        "UserPromptSubmit":   [_make_hook_entry(_hook_cmd("yellow"))],  # 用户提交 → 黄灯（执行中）
        "PermissionRequest":  [_make_hook_entry(_hook_cmd("yellow"))],  # 等待权限 → 黄灯
        "PreToolUse":         [_make_hook_entry(_hook_cmd("yellow"), matcher=permission_tools)],   # 工具前 → 黄灯
        "PostToolUse":        [_make_hook_entry(_hook_cmd("yellow"), matcher=permission_tools)],   # 工具后仍在执行 → 黄灯
        "Stop":               [_make_hook_entry(_hook_cmd("green"))],   # 正常结束 → 绿灯（完成）
        "SessionEnd":         [_make_hook_entry(_hook_cmd("green"))],   # 会话结束 → 绿灯
    }

    for hook_name, new_entries in desired.items():
        existing = hooks.get(hook_name, [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [e for e in existing if not _is_traffic_hook(e)]
        cleaned.extend(new_entries)
        hooks[hook_name] = cleaned
        print(f"已设置 hook: {hook_name}")

    config["hooks"] = hooks
    try:
        Path(CONFIG_PATH).write_text(json.dumps(config, indent=2, sort_keys=True))
        print(f"Claude Code 配置已更新: {CONFIG_PATH}")
    except Exception as e:
        print(f"写入配置失败: {e}")


# ---------- 菜单栏应用 ----------
class TrafficLightApp(rumps.App):
    def __init__(self):
        super().__init__("", quit_button="退出")
        self.state = "green"
        self.blink_on = True
        self.selected_project = get_selected_project()
        self.last_projects = []         # 上次的项目列表，用于检测变化
        self.last_menu_build_time = 0   # 上次构建菜单的时间
        self.monitor_mode = get_monitor_mode()  # 监控模式

        # 定时器
        rumps.Timer(self.check_state, POLL_INTERVAL).start()
        rumps.Timer(self.blink, BLINK_INTERVAL).start()

        # 读取 Claude 配置信息
        self.claude_info = self._load_claude_info()

        # 初始化
        self._build_menu()
        self.update_display()

    def _load_claude_info(self):
        """读取 Claude 配置信息"""
        info = {"model": "未知"}
        try:
            if Path(CONFIG_PATH).exists():
                config = json.loads(Path(CONFIG_PATH).read_text())
                model = config.get("env", {}).get("ANTHROPIC_MODEL", "") or config.get("model", "未知")
                info["model"] = model
        except Exception:
            pass
        return info

    def _build_menu(self):
        """动态构建菜单"""
        self.menu.clear()

        # 监控模式选择
        mode_menu = rumps.MenuItem("🔍 监控模式")
        mode_items = [
            (MONITOR_MODE_BOTH,   "🔀 两者都监控（Claude Code + CatPaw）"),
            (MONITOR_MODE_CLAUDE, "🤖 仅 Claude Code"),
            (MONITOR_MODE_CATPAW, "🐾 仅 CatPaw"),
        ]
        for mode_key, mode_label in mode_items:
            item = rumps.MenuItem(mode_label)
            item.set_callback(self._on_select_mode)
            if mode_key == self.monitor_mode:
                item.state = True
            mode_menu.add(item)
        self.menu.add(mode_menu)

        # Claude Code 项目选择（仅在监控 Claude Code 时显示）
        if self.monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            project_menu = rumps.MenuItem("📁 Claude Code 项目")
            projects = list_active_projects()
            if not projects:
                item = rumps.MenuItem("  (无活跃项目)")
                item.set_callback(None)
                project_menu.add(item)
            else:
                for p in projects:
                    item = rumps.MenuItem(f"  {p}")
                    item.set_callback(self._on_select_project)
                    if p == self.selected_project:
                        item.state = True
                    project_menu.add(item)
            self.menu.add(project_menu)
        else:
            projects = list_active_projects()

        # 当前信息
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("📊 当前状态", callback=None))
        if self.monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            self.menu.add(rumps.MenuItem(f"  Claude 项目: {self.selected_project}"))
            self.menu.add(rumps.MenuItem(f"  Claude 模型: {self.claude_info['model']}"))
        if self.monitor_mode in (MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
            catpaw_available = "✅ 已连接" if _find_idea_log() else "❌ 未检测到 IDEA 日志"
            self.menu.add(rumps.MenuItem(f"  CatPaw: {catpaw_available}"))

        # 状态说明
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("状态说明", callback=None))
        self.menu.add(rumps.MenuItem("🟢 绿灯常亮 - 空闲 / 完成 / 成功"))
        self.menu.add(rumps.MenuItem("🟡 黄灯闪烁 - 执行中 / 思考 / 工具调用"))
        self.menu.add(rumps.MenuItem("🔴 红灯常亮 - 失败 / 取消 / 异常"))

        self.last_projects = projects
        self.last_menu_build_time = time.time()

    def _on_select_project(self, sender):
        """项目选择回调"""
        self.selected_project = sender.title.strip()
        set_selected_project(self.selected_project)
        self.state = "red"
        self.blink_on = True
        self._build_menu()
        self.update_display()

    def _on_select_mode(self, sender):
        """监控模式选择回调"""
        label = sender.title.strip()
        if "Claude Code + CatPaw" in label or "两者" in label:
            self.monitor_mode = MONITOR_MODE_BOTH
        elif "Claude Code" in label:
            self.monitor_mode = MONITOR_MODE_CLAUDE
        elif "CatPaw" in label:
            self.monitor_mode = MONITOR_MODE_CATPAW
        set_monitor_mode(self.monitor_mode)
        self.state = "red"
        self.blink_on = True
        self._build_menu()
        self.update_display()

    def _get_combined_state(self):
        """根据监控模式获取综合状态"""
        states = []

        if self.monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            state_file = get_state_file(self.selected_project)
            try:
                if Path(state_file).exists():
                    content = Path(state_file).read_text().strip().lower()
                    if content in ("green", "yellow", "red"):
                        states.append(content)
                    else:
                        states.append("green")
                else:
                    # 状态文件不存在 = Claude Code 未在使用，视为空闲绿灯
                    states.append("green")
            except Exception:
                states.append("green")

        if self.monitor_mode in (MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
            states.append(get_catpaw_state())

        # 合并状态：优先级 red > yellow > green
        # （有任一异常就亮红，有任一执行中就亮黄，全部空闲才亮绿）
        if "red" in states:
            return "red"
        if "yellow" in states:
            return "yellow"
        return "green"

    def check_state(self, _):
        """读取状态并更新显示"""
        new_state = self._get_combined_state()
        if self.state != new_state:
            self._set_state(new_state)

        # 定期刷新菜单（检测新项目），避免过于频繁
        now = time.time()
        if now - self.last_menu_build_time > MENU_REFRESH_INTERVAL:
            projects = list_active_projects()
            # 自动选中第一个项目（当前无选中或选中项已不存在时）
            if projects and (self.selected_project not in projects):
                self.selected_project = projects[0]
                set_selected_project(self.selected_project)
            if projects != self.last_projects:
                self._build_menu()

    def _set_state(self, new_state):
        """设置新状态并重置闪烁"""
        self.state = new_state
        self.blink_on = True

    def blink(self, _):
        """闪烁效果"""
        self.blink_on = not self.blink_on
        self.update_display()

    def update_display(self):
        """根据状态更新菜单栏显示"""
        lights = [LIGHT_OFF, LIGHT_OFF, LIGHT_OFF]
        if self.state == "green":
            lights[2] = LIGHT_ON["green"]
        elif self.state == "yellow":
            lights[1] = LIGHT_ON["yellow"] if self.blink_on else LIGHT_OFF
        else:
            lights[0] = LIGHT_ON["red"]
        self.title = " ".join(lights)


# ---------- 入口 ----------
def main():
    print("正在配置 Claude Code hooks...")
    configure_hooks()

    atexit.register(restore_config)

    def signal_handler(sig, frame):
        restore_config()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("启动红绿灯监视器...")
    TrafficLightApp().run()


if __name__ == "__main__":
    main()
