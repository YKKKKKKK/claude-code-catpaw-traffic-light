#!/usr/bin/env python3
"""
Claude Code 顶部栏红绿灯 —— Python 版
三个灯同时显示，根据状态变化：
- 绿灯常亮：会话进行中
- 黄灯闪烁：需要确认（等待权限）
- 红灯常亮：会话结束
"""
import json
import sys, os
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
import shutil
import atexit
import signal
import time
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
        "SessionStart":       [_make_hook_entry(_hook_cmd("red"))],
        "UserPromptSubmit":   [_make_hook_entry(_hook_cmd("green"))],
        "PermissionRequest":  [_make_hook_entry(_hook_cmd("yellow"))],
        "PreToolUse":         [_make_hook_entry(_hook_cmd("yellow"), matcher=permission_tools)],
        "PostToolUse":        [_make_hook_entry(_hook_cmd("green"), matcher=permission_tools)],
        "Stop":               [_make_hook_entry(_hook_cmd("red"))],
        "SessionEnd":         [_make_hook_entry(_hook_cmd("red"))],
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
        self.state = "red"
        self.blink_on = True
        self.selected_project = get_selected_project()
        self.last_projects = []         # 上次的项目列表，用于检测变化
        self.last_menu_build_time = 0   # 上次构建菜单的时间

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

        # 项目选择
        project_menu = rumps.MenuItem("📁 选择项目")
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

        # 当前项目信息
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("📊 当前项目", callback=None))
        self.menu.add(rumps.MenuItem(f"  项目: {self.selected_project}"))
        self.menu.add(rumps.MenuItem(f"  模型: {self.claude_info['model']}"))

        # 状态说明
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("状态说明", callback=None))
        self.menu.add(rumps.MenuItem("🟢 绿灯常亮 - 会话进行中"))
        self.menu.add(rumps.MenuItem("🟡 黄灯闪烁 - 需要确认"))
        self.menu.add(rumps.MenuItem("🔴 红灯常亮 - 会话结束"))

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

    def check_state(self, _):
        """读取状态文件并更新状态"""
        state_file = get_state_file(self.selected_project)
        try:
            if Path(state_file).exists():
                content = Path(state_file).read_text().strip().lower()
                if content in ("green", "yellow", "red") and self.state != content:
                    self._set_state(content)
            else:
                if self.state != "red":
                    self._set_state("red")
        except Exception:
            pass

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
