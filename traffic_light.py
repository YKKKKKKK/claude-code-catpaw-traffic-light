#!/usr/bin/env python3
"""
Claude Code / CatPaw 顶部栏红绿灯 —— 纯原生 PyObjC 版（无 rumps）

菜单栏同时显示三盏灯，根据 Agent 状态实时切换：
  ⚫ ⚫ 🟢  绿灯常亮  —— 空闲 / 完成 / 成功（默认状态）
  ⚫ 🟡 ⚫  黄灯闪烁  —— Agent 正在执行 / 思考 / 调用工具
  🔴 ⚫ ⚫  红灯常亮  —— 失败 / 取消 / 异常

支持三种 Agent 来源：
  1. Claude Code（CLI）：通过 ~/.claude/settings.json 注入 hooks
  2. CatPaw JetBrains 插件版：实时 tail ~/Library/Logs/JetBrains/*/idea.log
  3. CatPaw 独立客户端（VSCode 版）：
     实时 tail ~/Library/Application Support/CatPaw/logs/*/window*/exthost/output_logging_*/1-Catpaw.log
     监听 Hook 事件：beforeSubmitPrompt / afterAgentResponse / beforeShellExecution / stop

新功能：
  - 挂件位置记忆：拖动后自动保存，重启恢复（含越界保护）
  - 挂件尺寸可调：小/中/大三档，菜单切换
  - 多会话同时显示：Claude Code 各项目 + CatPaw 各自分行
  - 开机自启动：菜单项写入/移除 LaunchAgents
  - 今日统计：记录执行次数和总时长，不满1分钟显示秒数
  - 黄灯实时计时：挂件底部实时显示当前执行已用时长
  - 屏幕变化自动归位：插拔显示器/切换 Space 后挂件自动归位至可见区域
  - 挂件点击折叠：单击卡片标题可折叠/展开挂件主体
  - 菜单栏实时秒数：黄灯执行时菜单顶部显示已执行秒数
"""
import json
import sys
import os
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
import shutil
import atexit
import signal
import time
import datetime
import threading
import logging as _logging
from pathlib import Path

# ---------- AppKit / WebKit 导入 ----------
from AppKit import (
    NSApplication, NSStatusBar, NSStatusItem,
    NSMenu, NSMenuItem, NSWindow, NSColor,
    NSFloatingWindowLevel, NSBorderlessWindowMask,
    NSBackingStoreBuffered, NSMakeRect, NSScreen,
    NSApplicationActivationPolicyAccessory,
    NSObject, NSRunLoop, NSDate,
    NSTimer, NSNotificationCenter,
)
from WebKit import WKWebView, WKWebViewConfiguration, WKUserContentController
from Foundation import NSURL, NSString
from AppKit import NSEvent
import objc


# ---------- 可拖拽 WKWebView 子类 ----------
# 原生 WKWebView 会吃掉所有鼠标事件，导致窗口无法被拖动。
# 这里重写 mouseDown/mouseDragged，把拖拽事件转发给父窗口。
class DraggableWKWebView(WKWebView):

    def mouseDown_(self, event):
        # 记录按下时的屏幕坐标 和 窗口左下角原点，后续全用屏幕坐标计算
        screen_pt = NSEvent.mouseLocation()
        win_origin = self.window().frame().origin
        # 鼠标相对于窗口左下角的偏移，保持这个偏移不变即可
        self._drag_offset_x = screen_pt.x - win_origin.x
        self._drag_offset_y = screen_pt.y - win_origin.y

    def mouseDragged_(self, event):
        try:
            screen_pt = NSEvent.mouseLocation()
            new_x = screen_pt.x - self._drag_offset_x
            new_y = screen_pt.y - self._drag_offset_y
            self.window().setFrameOrigin_((new_x, new_y))
            # 拖动结束时保存位置（每次 drag 都保，频率可接受）
            _save_widget_position(new_x, new_y)
        except Exception:
            pass

    def mouseUp_(self, event):
        # mouseUp 也保存一次，确保最终位置写入
        try:
            origin = self.window().frame().origin
            _save_widget_position(origin.x, origin.y)
        except Exception:
            pass

# ---------- 文件日志 ----------
_LOG_PATH = os.path.expanduser("~/Library/Logs/PawSignal.log")
_logging.basicConfig(
    filename=_LOG_PATH,
    level=_logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
def _log(msg):
    _logging.info(msg)
    print(msg, flush=True)

# ---------- 配置 ----------
BASE_DIR = os.path.expanduser("~/.claude/traffic_light")
STATE_DIR = BASE_DIR
CONFIG_PATH = os.path.expanduser("~/.claude/settings.json")
BACKUP_PATH = os.path.join(BASE_DIR, "settings_backup.json")
SELECTED_FILE = os.path.join(BASE_DIR, "selected_project")
POLL_INTERVAL = 0.3
BLINK_INTERVAL = 0.5
MENU_REFRESH_INTERVAL = 2

TRAFFIC_MARKER = "traffic_light_app"

LIGHT_ON  = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
LIGHT_OFF = "⚫"

# ---------- CatPaw 配置 ----------
CATPAW_IDLE_TIMEOUT   = 60
MONITOR_MODE_CLAUDE   = "claude"
MONITOR_MODE_CATPAW   = "catpaw"
MONITOR_MODE_BOTH     = "both"
MONITOR_MODE_FILE     = os.path.join(BASE_DIR, "monitor_mode")
WIDGET_ENABLED_FILE   = os.path.join(BASE_DIR, "widget_enabled")
MENUBAR_HIDDEN_FILE   = os.path.join(BASE_DIR, "menubar_hidden")

# ---- 新增配置文件路径 ----
WIDGET_POSITION_FILE  = os.path.join(BASE_DIR, "widget_position")   # x,y
WIDGET_SIZE_FILE      = os.path.join(BASE_DIR, "widget_size")       # small/medium/large
LAUNCH_AGENT_PLIST    = os.path.expanduser(
    "~/Library/LaunchAgents/com.pawsignal.traffic-light.plist"
)
STATS_FILE            = os.path.join(BASE_DIR, "daily_stats.json")  # 今日统计
CLAUDE_PROJECTS_DIR   = os.path.expanduser("~/.claude/projects")        # Claude Code 会话日志

# ---- 挂件尺寸预设（窗口宽度固定，高度自适应内容） ----
WIDGET_SH = 0   # 不留阴影边距，彻底无阴影
# h 设为足够大的值（内容自适应，不会截断），卡片用 height:auto 撑高
WIDGET_SIZES = {
    "small":  {"w": 72,  "h": 500, "dot": 16, "card_w": 72,  "pad_v": 10, "gap": 4},
    "medium": {"w": 96,  "h": 500, "dot": 22, "card_w": 96,  "pad_v": 14, "gap": 5},
    "large":  {"w": 124, "h": 500, "dot": 30, "card_w": 124, "pad_v": 18, "gap": 7},
}


# ---------- 持久化读写 ----------
def get_monitor_mode():
    try:
        if Path(MONITOR_MODE_FILE).exists():
            mode = Path(MONITOR_MODE_FILE).read_text().strip()
            if mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
                return mode
    except Exception:
        pass
    return MONITOR_MODE_BOTH

def set_monitor_mode(mode):
    try:
        Path(MONITOR_MODE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(MONITOR_MODE_FILE).write_text(mode)
    except Exception:
        pass

def get_widget_enabled():
    try:
        if Path(WIDGET_ENABLED_FILE).exists():
            return Path(WIDGET_ENABLED_FILE).read_text().strip() == "1"
    except Exception:
        pass
    return True

def set_widget_enabled(enabled: bool):
    try:
        Path(WIDGET_ENABLED_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(WIDGET_ENABLED_FILE).write_text("1" if enabled else "0")
    except Exception:
        pass

def get_menubar_hidden():
    try:
        if Path(MENUBAR_HIDDEN_FILE).exists():
            return Path(MENUBAR_HIDDEN_FILE).read_text().strip() == "1"
    except Exception:
        pass
    return False

def set_menubar_hidden(hidden: bool):
    try:
        Path(MENUBAR_HIDDEN_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(MENUBAR_HIDDEN_FILE).write_text("1" if hidden else "0")
    except Exception:
        pass

# ---- 挂件位置 ----
def _save_widget_position(x, y):
    try:
        Path(WIDGET_POSITION_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(WIDGET_POSITION_FILE).write_text(f"{x},{y}")
    except Exception:
        pass

def _load_widget_position():
    """返回 (x, y) 或 None"""
    try:
        if Path(WIDGET_POSITION_FILE).exists():
            parts = Path(WIDGET_POSITION_FILE).read_text().strip().split(",")
            if len(parts) == 2:
                return float(parts[0]), float(parts[1])
    except Exception:
        pass
    return None

def _clamp_position_to_screen(x, y, w, h):
    """确保挂件至少有一部分在屏幕可见区域内，防止位置越界"""
    try:
        screens = NSScreen.screens()
        if not screens:
            return x, y
        # 找到所有屏幕的联合可视区域
        min_x = min(s.visibleFrame().origin.x for s in screens)
        min_y = min(s.visibleFrame().origin.y for s in screens)
        max_x = max(s.visibleFrame().origin.x + s.visibleFrame().size.width  for s in screens)
        max_y = max(s.visibleFrame().origin.y + s.visibleFrame().size.height for s in screens)
        # 保证挂件至少有 30px 在屏幕内（而不是完全飞出去）
        margin = 30
        new_x = max(min_x - w + margin, min(x, max_x - margin))
        new_y = max(min_y - h + margin, min(y, max_y - margin))
        return new_x, new_y
    except Exception:
        return x, y

# ---- 挂件尺寸 ----
def get_widget_size():
    try:
        if Path(WIDGET_SIZE_FILE).exists():
            v = Path(WIDGET_SIZE_FILE).read_text().strip()
            if v in WIDGET_SIZES:
                return v
    except Exception:
        pass
    return "medium"

def set_widget_size(size: str):
    try:
        Path(WIDGET_SIZE_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(WIDGET_SIZE_FILE).write_text(size)
    except Exception:
        pass

# ---- 今日统计 ----
def _today_str():
    return datetime.date.today().isoformat()

def _load_stats():
    try:
        if Path(STATS_FILE).exists():
            data = json.loads(Path(STATS_FILE).read_text())
            if data.get("date") == _today_str():
                # 兼容旧版（无 token 字段）
                data.setdefault("total_tokens", 0)
                data.setdefault("scanned_uuids", [])
                return data
    except Exception:
        pass
    return {"date": _today_str(), "runs": 0, "total_seconds": 0,
            "total_tokens": 0, "scanned_uuids": []}

def _save_stats(data):
    try:
        Path(STATS_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(STATS_FILE).write_text(json.dumps(data))
    except Exception:
        pass

def _format_tokens(tokens):
    """将 token 数格式化为可读字符串"""
    if tokens < 1000:
        return f"{tokens}"
    elif tokens < 10000:
        return f"{tokens/1000:.1f}k"
    else:
        return f"{round(tokens/1000)}k"

# ---- 今日 token 扫描 ----
_token_scan_lock = threading.Lock()

def _scan_today_tokens():
    """扫描 ~/.claude/projects/ 下今日的 JSONL，统计 token 用量，增量更新 stats"""
    today = _today_str()  # e.g. "2026-06-09"
    projects_dir = Path(CLAUDE_PROJECTS_DIR)
    if not projects_dir.exists():
        return
    with _token_scan_lock:
        stats = _load_stats()
        scanned = set(stats.get("scanned_uuids", []))
        new_tokens = 0
        new_uuids = []
        try:
            for jsonl_file in projects_dir.rglob("*.jsonl"):
                try:
                    with open(jsonl_file, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                obj = json.loads(line)
                            except Exception:
                                continue
                            # 只处理 assistant 消息，有 usage 字段
                            if obj.get("type") != "assistant":
                                continue
                            uuid = obj.get("uuid", "")
                            if not uuid or uuid in scanned:
                                continue
                            # 检查时间戳是否是今天
                            ts = obj.get("timestamp", "")
                            if not ts.startswith(today):
                                continue
                            usage = obj.get("message", {}).get("usage", {})
                            if not usage:
                                continue
                            tokens = (
                                usage.get("input_tokens", 0)
                                + usage.get("cache_creation_input_tokens", 0)
                                + usage.get("cache_read_input_tokens", 0)
                                + usage.get("output_tokens", 0)
                            )
                            new_tokens += tokens
                            new_uuids.append(uuid)
                except Exception:
                    continue
        except Exception:
            pass
        if new_tokens > 0 or new_uuids:
            stats = _load_stats()  # 重新加载防止并发覆盖
            stats["total_tokens"] = stats.get("total_tokens", 0) + new_tokens
            existing = set(stats.get("scanned_uuids", []))
            existing.update(new_uuids)
            stats["scanned_uuids"] = list(existing)
            _save_stats(stats)

def _format_duration(secs):
    """将秒数格式化为可读字符串：不满60秒显示秒，否则显示分钟"""
    if secs < 60:
        return f"{secs} 秒"
    else:
        mins = secs // 60
        return f"{mins} 分钟"

# ---- LaunchAgent 自启动 ----
def _get_launch_agent_plist_content():
    exe = sys.executable
    script = os.path.abspath(__file__)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.pawsignal.traffic-light</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>{script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{_LOG_PATH}</string>
    <key>StandardErrorPath</key>
    <string>{_LOG_PATH}</string>
</dict>
</plist>
"""

def is_launch_agent_enabled():
    return Path(LAUNCH_AGENT_PLIST).exists()

def enable_launch_agent():
    try:
        Path(LAUNCH_AGENT_PLIST).parent.mkdir(parents=True, exist_ok=True)
        Path(LAUNCH_AGENT_PLIST).write_text(_get_launch_agent_plist_content())
        os.system(f"launchctl load '{LAUNCH_AGENT_PLIST}' 2>/dev/null")
        _log(f"LaunchAgent 已启用: {LAUNCH_AGENT_PLIST}")
    except Exception as e:
        _log(f"启用 LaunchAgent 失败: {e}")

def disable_launch_agent():
    try:
        if Path(LAUNCH_AGENT_PLIST).exists():
            os.system(f"launchctl unload '{LAUNCH_AGENT_PLIST}' 2>/dev/null")
            Path(LAUNCH_AGENT_PLIST).unlink()
        _log("LaunchAgent 已禁用")
    except Exception as e:
        _log(f"禁用 LaunchAgent 失败: {e}")

# ---------- CatPaw 状态监听 ----------
_catpaw_state_cache    = "green"
_catpaw_last_event_time = 0.0
_catpaw_log_thread     = None
_catpaw_pending_green_at = 0.0
_catpaw_cancelled_at   = 0.0
_catpaw_stopped        = False   # stop 事件已触发，等 beforeSubmitPrompt 确认后才变绿
CATPAW_GREEN_DELAY     = 3.0   # stop 后 3 秒内无新工具调用则变绿
CATPAW_CANCEL_PROTECT  = 10.0

# ---------- CatPaw JetBrains 插件版日志（idea.log）----------

def _find_idea_logs():
    base = Path("~/Library/Logs/JetBrains").expanduser()
    if not base.exists():
        return []
    return list(base.glob("*/idea.log"))

def _catpaw_jetbrains_log_watcher_single(log_path):
    """监听 JetBrains 插件版 CatPaw 的 idea.log"""
    global _catpaw_state_cache, _catpaw_last_event_time
    global _catpaw_pending_green_at, _catpaw_cancelled_at
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    try:
                        if os.stat(log_path).st_ino != os.fstat(f.fileno()).st_ino:
                            break
                    except Exception:
                        pass
                    continue
                if "AgentTabService" in line and "Tab状态已更新" in line:
                    _catpaw_last_event_time = time.time()
                    if "Status: running" in line:
                        if time.time() - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                            _catpaw_state_cache = "yellow"
                            _catpaw_pending_green_at = 0.0
                    elif "Status: completed" in line:
                        if time.time() - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                            _catpaw_pending_green_at = time.time()
                    elif "Status: failed" in line or "Status: error" in line or "Status: cancelled" in line:
                        _catpaw_state_cache = "red"
                        _catpaw_pending_green_at = 0.0
                        _catpaw_cancelled_at = time.time()
    except Exception:
        pass

# ---------- CatPaw 独立客户端（VSCode 版）日志监听 ----------
_CATPAW_VSCODE_LOG_BASE = Path("~/Library/Application Support/CatPaw/logs").expanduser()

def _find_catpaw_vscode_logs():
    """扫描 CatPaw VSCode 版的最新 Hook Log 文件（支持多 window）"""
    if not _CATPAW_VSCODE_LOG_BASE.exists():
        return []
    session_dirs = sorted(_CATPAW_VSCODE_LOG_BASE.glob("*/"), reverse=True)[:3]
    logs = []
    for session_dir in session_dirs:
        for log_path in session_dir.glob("window*/exthost/output_logging_*/3-Hook Log.log"):
            logs.append(log_path)
    return logs

def _catpaw_vscode_log_watcher_single(log_path):
    """监听 CatPaw VSCode 独立客户端的 Hook Log 事件"""
    global _catpaw_state_cache, _catpaw_last_event_time
    global _catpaw_pending_green_at, _catpaw_cancelled_at, _catpaw_stopped
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    try:
                        if os.stat(log_path).st_ino != os.fstat(f.fileno()).st_ino:
                            break
                    except Exception:
                        pass
                    continue
                if "beforeSubmitPrompt" in line:
                    # 用户发送了新消息 → 标志上一轮已真正结束，立即变黄（新一轮开始）
                    now = time.time()
                    _catpaw_last_event_time = now
                    _catpaw_stopped = False
                    if now - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                        # 先快速过绿灯（上一轮已结束），再立即回黄（新一轮开始）
                        _catpaw_state_cache = "yellow"
                        _catpaw_pending_green_at = 0.0
                elif ("beforeShellExecution" in line or "beforeReadFile" in line):
                    # Agent 工具调用开始 → 黄灯，清除变绿倒计时
                    now = time.time()
                    _catpaw_last_event_time = now
                    _catpaw_stopped = False
                    if now - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                        _catpaw_state_cache = "yellow"
                        _catpaw_pending_green_at = 0.0
                elif "afterAgentResponse" in line:
                    # Agent 回复完毕，可能还有后续工具调用，不触发变绿，只记录时间
                    now = time.time()
                    _catpaw_last_event_time = now
                elif "] Hook step requested: stop" in line:
                    # Agent 本轮停止 → 开始 5 秒倒计时
                    # 5 秒内如果来了 beforeShellExecution/beforeReadFile 则取消倒计时继续黄灯
                    # 5 秒内如果来了 beforeSubmitPrompt（用户发新消息）则直接变黄（新一轮）
                    now = time.time()
                    _catpaw_last_event_time = now
                    _catpaw_stopped = True
                    if now - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                        _catpaw_pending_green_at = now
    except Exception:
        pass

# ---------- 统一日志监听调度 ----------
_catpaw_watched_paths = set()
_catpaw_watcher_lock  = threading.Lock()

def _start_watcher_for_path(log_path, watcher_fn):
    path_str = str(log_path)
    with _catpaw_watcher_lock:
        if path_str in _catpaw_watched_paths:
            return
        _catpaw_watched_paths.add(path_str)

    def _wrapper():
        try:
            watcher_fn(path_str)
        finally:
            # 线程结束（含日志轮转 break）后，从已监听集合中移除
            # 下次扫描器扫到同路径时会重新启动新线程
            with _catpaw_watcher_lock:
                _catpaw_watched_paths.discard(path_str)
            _log(f"CatPaw 日志监听已退出，等待重连: {path_str}")

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    _log(f"CatPaw 日志监听已启动: {path_str}")

def _catpaw_log_scanner():
    while True:
        for log_path in _find_idea_logs():
            _start_watcher_for_path(log_path, _catpaw_jetbrains_log_watcher_single)
        for log_path in _find_catpaw_vscode_logs():
            _start_watcher_for_path(log_path, _catpaw_vscode_log_watcher_single)
        time.sleep(5)

def _ensure_log_watcher():
    global _catpaw_log_thread
    if _catpaw_log_thread is None or not _catpaw_log_thread.is_alive():
        _catpaw_log_thread = threading.Thread(target=_catpaw_log_scanner, daemon=True)
        _catpaw_log_thread.start()

def get_catpaw_state():
    global _catpaw_state_cache, _catpaw_pending_green_at, _catpaw_cancelled_at
    _ensure_log_watcher()
    now = time.time()
    if _catpaw_pending_green_at > 0 and _catpaw_state_cache == "yellow":
        if now - _catpaw_pending_green_at >= CATPAW_GREEN_DELAY:
            _catpaw_state_cache = "green"
            _catpaw_pending_green_at = 0.0
    if _catpaw_state_cache == "red" and _catpaw_cancelled_at > 0:
        if now - _catpaw_cancelled_at > CATPAW_CANCEL_PROTECT:
            _catpaw_state_cache = "green"
            _catpaw_cancelled_at = 0.0
    if _catpaw_last_event_time > 0:
        if now - _catpaw_last_event_time > CATPAW_IDLE_TIMEOUT and _catpaw_state_cache == "yellow":
            _catpaw_state_cache = "green"
    return _catpaw_state_cache


# ---------- 项目/配置工具 ----------
def get_state_file(project_name=None):
    if project_name is None:
        project_name = get_selected_project()
    return os.path.join(STATE_DIR, f"{project_name}.state")

def get_selected_project():
    try:
        if Path(SELECTED_FILE).exists():
            return Path(SELECTED_FILE).read_text().strip()
    except Exception:
        pass
    projects = list_active_projects()
    return projects[0] if projects else "default"

def set_selected_project(project_name):
    try:
        Path(SELECTED_FILE).parent.mkdir(parents=True, exist_ok=True)
        Path(SELECTED_FILE).write_text(project_name)
    except Exception:
        pass

def list_active_projects():
    try:
        Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
        return sorted(f.stem for f in Path(STATE_DIR).glob("*.state"))
    except Exception:
        return []

def backup_config():
    if Path(CONFIG_PATH).exists():
        try:
            shutil.copy2(CONFIG_PATH, BACKUP_PATH)
        except Exception:
            pass

def restore_config():
    if Path(BACKUP_PATH).exists():
        try:
            shutil.copy2(BACKUP_PATH, CONFIG_PATH)
            Path(BACKUP_PATH).unlink()
        except Exception:
            pass
    if Path(STATE_DIR).exists():
        try:
            # 只删除 .state 文件和 settings_backup，保留统计/配置文件
            KEEP_FILES = {
                "daily_stats.json",
                "widget_position",
                "widget_size",
                "monitor_mode",
                "widget_enabled",
                "menubar_hidden",
                "selected_project",
            }
            for f in Path(STATE_DIR).iterdir():
                if f.name not in KEEP_FILES:
                    try:
                        f.unlink() if f.is_file() else shutil.rmtree(f)
                    except Exception:
                        pass
        except Exception:
            pass
    old_file = os.path.expanduser("~/.claude/.traffic_light")
    if Path(old_file).exists():
        try:
            Path(old_file).unlink()
        except Exception:
            pass

def _is_traffic_hook(entry):
    return any(TRAFFIC_MARKER in h.get("command", "") for h in entry.get("hooks", []))

def _make_hook_entry(command, matcher=""):
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}

def configure_hooks():
    Path(CONFIG_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
    backup_config()
    config = {}
    if Path(CONFIG_PATH).exists():
        try:
            config = json.loads(Path(CONFIG_PATH).read_text())
        except Exception:
            config = {}
    hooks = config.get("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}

    def _hook_cmd(state):
        marker = f"# {TRAFFIC_MARKER}"
        return (f'project=$(basename "${{CLAUDE_PROJECT_DIR:-$PWD}}") && '
                f'mkdir -p {STATE_DIR} && '
                f'echo {state} > {STATE_DIR}/"$project".state {marker}')

    def _hook_cmd_yellow_safe():
        """写 yellow，但如果当前是 permission 状态则跳过（避免覆盖确认提示）"""
        marker = f"# {TRAFFIC_MARKER}"
        sf = f'{STATE_DIR}/"$project".state'
        return (f'project=$(basename "${{CLAUDE_PROJECT_DIR:-$PWD}}") && '
                f'mkdir -p {STATE_DIR} && '
                f'cur=$(cat {sf} 2>/dev/null || echo green) && '
                f'[ "$cur" != "permission" ] && echo yellow > {sf} {marker} || true')

    permission_tools = "Bash|Write|Edit|NotebookEdit|WebFetch"
    desired = {
        "SessionStart":      [_make_hook_entry(_hook_cmd("green"))],
        "UserPromptSubmit":  [_make_hook_entry(_hook_cmd("yellow"))],
        # PermissionRequest = 需要用户确认执行命令，写入特殊状态 permission
        "PermissionRequest": [_make_hook_entry(_hook_cmd("permission"))],
        # PreToolUse/PostToolUse 不覆盖 permission 状态（等用户点确认后才变回 yellow）
        "PreToolUse":        [_make_hook_entry(_hook_cmd_yellow_safe(), matcher=permission_tools)],
        "PostToolUse":       [_make_hook_entry(_hook_cmd("yellow"), matcher=permission_tools)],
        "Stop":              [_make_hook_entry(_hook_cmd("green"))],
        "SessionEnd":        [_make_hook_entry(_hook_cmd("green"))],
    }
    for hook_name, new_entries in desired.items():
        existing = hooks.get(hook_name, [])
        if not isinstance(existing, list):
            existing = []
        cleaned = [e for e in existing if not _is_traffic_hook(e)]
        cleaned.extend(new_entries)
        hooks[hook_name] = cleaned
    config["hooks"] = hooks
    try:
        Path(CONFIG_PATH).write_text(json.dumps(config, indent=2, sort_keys=True))
        _log(f"Claude Code 配置已更新: {CONFIG_PATH}")
    except Exception as e:
        _log(f"写入配置失败: {e}")


# ---------- 桌面挂件 HTML 生成器 ----------
def _build_widget_html(size_key="medium"):
    s = WIDGET_SIZES.get(size_key, WIDGET_SIZES["medium"])
    w        = s["w"]
    h        = s["h"]
    dot_sz   = s["dot"]
    card_w   = s["card_w"]
    pad_v    = s["pad_v"]
    gap      = s["gap"]
    sh       = WIDGET_SH   # body padding = 阴影扩散边距
    title_sz = max(7, dot_sz // 3)
    label_sz = max(8, dot_sz // 2 - 2)
    gloss_w  = max(6, dot_sz // 2 - 2)
    gloss_h  = max(4, dot_sz // 3)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  html {{ background: transparent; }}
  body {{
    background: transparent;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif;
    -webkit-app-region: drag;
    user-select: none;
    -webkit-user-select: none;
    /* 让 body 高度由内容决定，不截断卡片 */
    display: inline-block;
    vertical-align: top;
  }}
  .card {{
    width: {card_w}px;
    height: auto;  /* 内容自适应，不固定高度 */
    background: rgba(24, 24, 26, 0.82);
    backdrop-filter: blur(36px) saturate(160%);
    -webkit-backdrop-filter: blur(36px) saturate(160%);
    border-radius: 20px;
    border: 0.5px solid rgba(255, 255, 255, 0.13);
    box-shadow: inset 0 0.5px 0 rgba(255, 255, 255, 0.12);
    padding: {pad_v}px 0 {max(pad_v-2,8)}px;
    display: flex; flex-direction: column; align-items: center;
    position: relative;
    will-change: transform;
    transform: translateZ(0);
    -webkit-transform: translateZ(0);
  }}
  .title {{
    font-size: {title_sz}px; font-weight: 600; letter-spacing: 0.1em;
    color: rgba(255,255,255,0.45); text-transform: uppercase;
    margin-bottom: {max(8,gap*2)}px; -webkit-app-region: drag;
    cursor: pointer;
  }}
  /* ── 折叠动画 ── */
  .collapsible {{
    overflow: hidden;
    transition: max-height 0.3s ease, opacity 0.3s ease;
    max-height: 600px;
    opacity: 1;
    width: 100%;
    display: flex; flex-direction: column; align-items: center;
  }}
  .collapsible.collapsed {{
    max-height: 0;
    opacity: 0;
  }}
  /* ── 固定三灯区 ── */
  .lights {{
    display: flex; flex-direction: column; align-items: center; width: 100%;
  }}
  .light-unit {{
    display: flex; flex-direction: column; align-items: center;
    gap: {gap}px; padding: {gap+2}px 0; width: 100%; position: relative;
  }}
  .light-unit + .light-unit::before {{
    content: ''; position: absolute; top: 0; left: 18px; right: 18px;
    height: 0.5px; background: rgba(255,255,255,0.06);
  }}
  .dot {{
    width: {dot_sz}px; height: {dot_sz}px; border-radius: 50%;
    transition: all 0.5s cubic-bezier(0.4,0,0.2,1);
    position: relative; flex-shrink: 0;
  }}
  .dot.off   {{ background: rgba(255,255,255,0.06); box-shadow: inset 0 1px 3px rgba(0,0,0,0.5); }}
  .dot.red   {{
    background: radial-gradient(circle at 38% 32%, #ff6e63, #ff3b30 55%, #c62218);
    box-shadow: 0 0 0 3px rgba(255,59,48,.15), 0 0 14px rgba(255,59,48,.55), 0 0 30px rgba(255,59,48,.2);
  }}
  .dot.yellow {{
    background: radial-gradient(circle at 38% 32%, #ffe066, #ffd60a 55%, #c9a000);
    box-shadow: 0 0 0 3px rgba(255,214,10,.15), 0 0 14px rgba(255,214,10,.6), 0 0 30px rgba(255,214,10,.22);
    animation: pulse 1.4s ease-in-out infinite;
  }}
  .dot.green {{
    background: radial-gradient(circle at 38% 32%, #5dff7e, #30d158 55%, #178c38);
    box-shadow: 0 0 0 3px rgba(48,209,88,.15), 0 0 14px rgba(48,209,88,.5), 0 0 30px rgba(48,209,88,.18);
  }}
  .dot::after {{
    content: ''; position: absolute; top: 4px; left: 5px;
    width: {gloss_w}px; height: {gloss_h}px; background: rgba(255,255,255,0.4);
    border-radius: 50%; filter: blur(1px); pointer-events: none; transition: opacity 0.5s;
  }}
  .dot.off::after {{ opacity: 0; }}
  @keyframes pulse {{ 0%,100%{{opacity:1;transform:scale(1)}} 50%{{opacity:.6;transform:scale(.92)}} }}
  .dot-label {{
    font-size: {label_sz}px; font-weight: 400; color: rgba(255,255,255,0.2);
    height: 11px; text-align: center; transition: color 0.4s, font-weight 0.4s;
    -webkit-app-region: drag;
  }}
  .light-unit.active-red    .dot-label {{ color: rgba(255,90,75,.9);   font-weight: 500; }}
  .light-unit.active-yellow .dot-label {{ color: rgba(255,210,10,.9);  font-weight: 500; }}
  .light-unit.active-green  .dot-label {{ color: rgba(48,209,88,.88);  font-weight: 500; }}
  /* ── 黄灯实时计时 ── */
  .elapsed-bar {{
    margin-top: {gap}px;
    font-size: {max(7,label_sz-1)}px; color: rgba(255,210,10,0.75);
    text-align: center; padding: 0 8px;
    padding-top: {gap}px;
    -webkit-app-region: drag;
    display: none;
    border-top: 0.5px solid rgba(255,255,255,0.06);
  }}
  /* ── 多会话来源标签区（固定三灯下方，仅多来源时显示） ── */
  .sessions-bar {{
    width: 100%; padding: 0 8px;
    border-top: 0.5px solid rgba(255,255,255,0.06);
    margin-top: {gap}px; padding-top: {gap}px;
    display: none; flex-direction: column; gap: 3px;
  }}
  .sessions-bar.visible {{ display: flex; }}
  .session-row {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 2px 4px;
  }}
  .session-name {{
    font-size: {max(7,label_sz-1)}px; color: rgba(255,255,255,0.3);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%;
  }}
  .session-dot {{
    width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
  }}
  .session-dot.red    {{ background: #ff3b30; box-shadow: 0 0 4px rgba(255,59,48,.7); }}
  .session-dot.yellow {{ background: #ffd60a; box-shadow: 0 0 4px rgba(255,214,10,.7); animation: pulse 1.4s ease-in-out infinite; }}
  .session-dot.green  {{ background: #30d158; box-shadow: 0 0 4px rgba(48,209,88,.6); }}
  .session-dot.off    {{ background: rgba(255,255,255,0.12); }}
  /* ── 今日统计 ── */
  .stats-bar {{
    margin-top: {gap}px;
    font-size: {max(7,label_sz-1)}px; color: rgba(255,255,255,0.22);
    text-align: center; padding: 0 8px;
    border-top: 0.5px solid rgba(255,255,255,0.06);
    padding-top: {gap}px;
    -webkit-app-region: drag;
    display: none;
  }}
  /* ── 折叠状态下标题颜色微调 ── */
  .title.collapsed-state {{ color: rgba(255,255,255,0.3); }}
  /* ── 抖动动画 ── */
  @keyframes shake {{
    0%,100% {{ transform: translateX(0); }}
    15%     {{ transform: translateX(-6px); }}
    30%     {{ transform: translateX(6px); }}
    45%     {{ transform: translateX(-5px); }}
    60%     {{ transform: translateX(5px); }}
    75%     {{ transform: translateX(-3px); }}
    90%     {{ transform: translateX(3px); }}
  }}
  .card.shaking {{ animation: shake 0.55s ease; }}
  /* ── 确认提示条 ── */
  .alert-bar {{
    display: none;
    margin-top: {gap}px;
    padding: {gap}px 8px;
    border-top: 0.5px solid rgba(255,200,10,0.25);
    font-size: {max(7,label_sz-1)}px;
    color: rgba(255,210,10,0.95);
    text-align: center;
    font-weight: 600;
    letter-spacing: 0.02em;
    -webkit-app-region: drag;
    animation: alertPulse 1.2s ease-in-out infinite;
  }}
  .alert-bar.visible {{ display: block; }}
  @keyframes alertPulse {{
    0%,100% {{ opacity: 1; }}
    50%     {{ opacity: 0.5; }}
  }}
</style>
</head>
<body>
<div class="card">
  <div class="title" id="title-bar" onclick="toggleCollapse()">PawSignal</div>

  <!-- 可折叠区域 -->
  <div class="collapsible" id="collapsible-body">
    <!-- 始终显示的固定三灯 -->
    <div class="lights">
      <div class="light-unit" id="unit-red">
        <div class="dot off" id="dot-red"></div>
        <span class="dot-label">失败/取消</span>
      </div>
      <div class="light-unit" id="unit-yellow">
        <div class="dot off" id="dot-yellow"></div>
        <span class="dot-label">执行中</span>
      </div>
      <div class="light-unit active-green" id="unit-green">
        <div class="dot green" id="dot-green"></div>
        <span class="dot-label">空闲</span>
      </div>
    </div>

    <!-- 黄灯执行实时计时 -->
    <div class="elapsed-bar" id="elapsed-bar">⏱ 执行中 0 秒</div>

    <!-- 多来源时在下方逐行显示各来源状态 -->
    <div class="sessions-bar" id="sessions-bar"></div>

    <!-- 确认提示条 -->
    <div class="alert-bar" id="alert-bar">⚠️ 请回到 Agent 确认操作</div>

    <!-- 今日统计 -->
    <div class="stats-bar" id="stats-bar"></div>
  </div>
</div>
<script>
  var _collapsed = false;
  var _currentState = 'green';
  var _yellowStartTs = 0;      // 黄灯开始时间戳（由 Python 传入，毫秒）
  var _elapsedTimer = null;

  // ── 折叠/展开 ──
  function toggleCollapse() {{
    _collapsed = !_collapsed;
    var body  = document.getElementById('collapsible-body');
    var title = document.getElementById('title-bar');
    if (_collapsed) {{
      body.classList.add('collapsed');
      title.classList.add('collapsed-state');
    }} else {{
      body.classList.remove('collapsed');
      title.classList.remove('collapsed-state');
    }}
  }}

  var _prevNeedsConfirm = false;  // 记录上一轮是否处于确认状态

  // ── 固定三灯 ──
  function updateState(state, yellowStartMs, needsConfirm) {{
    var prevState = _currentState;
    _currentState = state;
    var dots  = {{ red: document.getElementById('dot-red'), yellow: document.getElementById('dot-yellow'), green: document.getElementById('dot-green') }};
    var units = {{ red: document.getElementById('unit-red'), yellow: document.getElementById('unit-yellow'), green: document.getElementById('unit-green') }};
    Object.values(dots).forEach(function(d)  {{ d.className = 'dot off'; }});
    Object.values(units).forEach(function(u) {{ u.className = 'light-unit'; }});
    if (state === 'red')         {{ dots.red.className = 'dot red';       units.red.className    = 'light-unit active-red'; }}
    else if (state === 'yellow') {{ dots.yellow.className = 'dot yellow'; units.yellow.className = 'light-unit active-yellow'; }}
    else                         {{ dots.green.className = 'dot green';   units.green.className  = 'light-unit active-green'; }}

    // 黄灯实时计时
    var elapsedBar = document.getElementById('elapsed-bar');
    var alertBar   = document.getElementById('alert-bar');
    var card       = document.querySelector('.card');
    if (state === 'yellow') {{
      if (prevState !== 'yellow') {{
        _yellowStartTs = (yellowStartMs && yellowStartMs > 0) ? yellowStartMs : Date.now();
        elapsedBar.style.display = 'block';
        _startElapsedTimer();
      }}
      // ── 只有 needsConfirm 刚变为 true 时，才抖动 + 显示提示条 ──
      if (needsConfirm && !_prevNeedsConfirm) {{
        alertBar.classList.add('visible');
        card.classList.add('shaking');
        setTimeout(function() {{ card.classList.remove('shaking'); }}, 600);
      }} else if (!needsConfirm) {{
        alertBar.classList.remove('visible');
      }}
    }} else {{
      elapsedBar.style.display = 'none';
      alertBar.classList.remove('visible');
      _yellowStartTs = 0;
      if (_elapsedTimer) {{ clearInterval(_elapsedTimer); _elapsedTimer = null; }}
    }}
    _prevNeedsConfirm = !!needsConfirm;
    setTimeout(_reportHeight, 50);  // 内容变化后汇报高度
  }}

  function _startElapsedTimer() {{
    if (_elapsedTimer) clearInterval(_elapsedTimer);
    _elapsedTimer = setInterval(function() {{
      if (_currentState !== 'yellow') {{
        clearInterval(_elapsedTimer); _elapsedTimer = null; return;
      }}
      var secs = Math.floor((Date.now() - _yellowStartTs) / 1000);
      var text;
      if (secs < 60) {{
        text = '⏱ 执行中 ' + secs + ' 秒';
      }} else {{
        text = '⏱ 执行中 ' + Math.floor(secs / 60) + ' 分 ' + (secs % 60) + ' 秒';
      }}
      document.getElementById('elapsed-bar').textContent = text;
    }}, 1000);
  }}

  // ── 多来源列表 ──
  function updateSessions(sessions) {{
    var bar = document.getElementById('sessions-bar');
    if (!sessions || sessions.length <= 1) {{
      bar.className = 'sessions-bar';
      bar.innerHTML = '';
      return;
    }}
    bar.className = 'sessions-bar visible';
    bar.innerHTML = sessions.map(function(s) {{
      return '<div class="session-row">' +
        '<span class="session-name">' + (s.label || '') + '</span>' +
        '<span class="session-dot ' + (s.state || 'off') + '"></span>' +
        '</div>';
    }}).join('');
    setTimeout(_reportHeight, 50);
  }}

  function showStats(text) {{
    var bar = document.getElementById('stats-bar');
    bar.style.display = 'block';
    bar.textContent = text || '今日 0 次';
    _reportHeight();
  }}

  // ── 向 Python 汇报卡片实际高度 ──
  function _reportHeight() {{
    try {{
      var h = document.querySelector('.card').getBoundingClientRect().height;
      window.webkit.messageHandlers.pawResize.postMessage(Math.ceil(h));
    }} catch(e) {{}}
  }}
  // 初始化完成后也汇报一次
  window.addEventListener('load', function() {{ setTimeout(_reportHeight, 100); }});
</script>
</body>
</html>"""


# ---------- WKScriptMessageHandler：JS → Python 高度回调 ----------
_WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

class _PawResizeHandler(objc.lookUpClass("NSObject"), protocols=[_WKScriptMessageHandler]):
    """接收 JS 汇报的卡片高度，动态调整窗口大小"""
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        try:
            card_h = int(message.body())
            delegate = _app_delegate_ref[0]
            if delegate and delegate._widget_window:
                win = delegate._widget_window
                wk  = delegate._wkview
                s   = WIDGET_SIZES.get(delegate._widget_size, WIDGET_SIZES["medium"])
                w   = s["w"]
                origin = win.frame().origin
                # macOS 坐标系原点在左下角，调整高度时需要同步移动 y 坐标，保持窗口顶部不动
                old_h = win.frame().size.height
                if abs(card_h - old_h) > 2:  # 变化超过 2px 才更新，防止抖动
                    new_y = origin.y + old_h - card_h
                    win.setFrame_display_(NSMakeRect(origin.x, new_y, w, card_h), True)
                    wk.setFrame_(NSMakeRect(0, 0, w, card_h))
        except Exception:
            pass


# ---------- 全局 delegate 引用（供回调访问） ----------
_app_delegate_ref = [None]


# ---------- AppDelegate ----------
class AppDelegate(NSObject):
    """
    纯 PyObjC AppDelegate。
    负责：StatusBar 菜单栏、桌面挂件窗口、状态轮询定时器。
    """

    def applicationDidFinishLaunching_(self, notification):
        _log("applicationDidFinishLaunching")
        _app_delegate_ref[0] = self

        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )

        # 内部状态
        self._state          = "green"
        self._prev_state     = "green"
        self._blink_on       = True
        self._monitor_mode   = get_monitor_mode()
        self._widget_enabled = get_widget_enabled()
        self._menubar_hidden = get_menubar_hidden()
        self._widget_size    = get_widget_size()
        self._selected_project = get_selected_project()
        self._last_projects  = []
        self._last_menu_build_time = 0.0
        self._wkview         = None
        self._widget_window  = None

        # 今日统计
        self._stats          = _load_stats()
        self._yellow_start   = 0.0   # 黄灯开始时间，用于统计时长

        # 创建 StatusBar 图标
        self._status_bar  = NSStatusBar.systemStatusBar()
        self._status_item = self._status_bar.statusItemWithLength_(-1)
        self._status_item.setHighlightMode_(True)
        self._update_status_title()
        self._build_menu()

        # 创建桌面挂件窗口
        self._create_widget_window()

        if self._widget_enabled:
            self._show_widget()

        # 启动轮询定时器
        self._poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL, self, "onPollTimer:", None, True
        )
        self._blink_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            BLINK_INTERVAL, self, "onBlinkTimer:", None, True
        )

        # 监听屏幕参数变化（插拔显示器 / Space 切换）
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self,
            "onScreenChanged:",
            "NSApplicationDidChangeScreenParametersNotification",
            None,
        )
        _log("AppDelegate 初始化完成")

    # ── 定时器回调 ──────────────────────────────────────────

    def onPollTimer_(self, timer):
        new_state = self._get_combined_state()

        # 检测状态变化
        if new_state != self._state:
            old_state = self._state
            self._state   = new_state
            self._blink_on = True
            self._update_status_title()

            # ---- 统计：黄灯时长 ----
            if old_state == "yellow" and new_state != "yellow":
                # 黄灯结束
                elapsed = time.time() - self._yellow_start if self._yellow_start > 0 else 0
                self._yellow_start = 0.0
                self._stats = _load_stats()  # 重新加载（防止跨天）
                self._stats["total_seconds"] += int(elapsed)
                _save_stats(self._stats)
            if new_state == "yellow" and old_state != "yellow":
                # 黄灯开始
                self._yellow_start = time.time()
                self._stats = _load_stats()
                self._stats["runs"] += 1
                _save_stats(self._stats)

            # 刷新菜单（状态变化时立即更新菜单栏统计行）
            self._build_menu()

        if self._widget_enabled and self._wkview:
            self._push_state_to_widget()

        # 定期刷新菜单（包含实时秒数）
        now = time.time()
        if now - self._last_menu_build_time > MENU_REFRESH_INTERVAL:
            projects = list_active_projects()
            if projects and self._selected_project not in projects:
                self._selected_project = projects[0]
                set_selected_project(self._selected_project)
            if projects != self._last_projects or self._state == "yellow":
                # 黄灯时每次刷新都重建菜单（更新实时秒数）
                self._build_menu()

    def onBlinkTimer_(self, timer):
        self._blink_on = not self._blink_on
        self._update_status_title()

    def onScreenChanged_(self, notification):
        """屏幕参数变化时（插拔显示器 / Space 切换），将挂件归位至可见区域"""
        if self._widget_window and self._widget_enabled:
            origin = self._widget_window.frame().origin
            size   = self._widget_window.frame().size
            nx, ny = _clamp_position_to_screen(
                origin.x, origin.y, size.width, size.height
            )
            if abs(nx - origin.x) > 1 or abs(ny - origin.y) > 1:
                _log(f"屏幕变化，挂件归位: ({origin.x:.0f},{origin.y:.0f}) → ({nx:.0f},{ny:.0f})")
                self._widget_window.setFrameOrigin_((nx, ny))
                _save_widget_position(nx, ny)

    # ── 状态计算 ───────────────────────────────────────────

    def _get_combined_state(self):
        """返回聚合灯色：遍历所有 Claude 项目，取最高优先级（red > yellow > green）"""
        states = []
        if self._monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            for p in list_active_projects():
                sf = get_state_file(p)
                try:
                    content = Path(sf).read_text().strip().lower() if Path(sf).exists() else "green"
                    # permission 显示为黄灯
                    if content == "permission":
                        states.append("yellow")
                    else:
                        states.append(content if content in ("green","yellow","red") else "green")
                except Exception:
                    states.append("green")
            # 无任何项目时默认绿灯
            if not states:
                states.append("green")
        if self._monitor_mode in (MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
            states.append(get_catpaw_state())
        if "red"    in states: return "red"
        if "yellow" in states: return "yellow"
        return "green"

    def _needs_confirm(self):
        """检查是否有任何项目处于 permission（等待用户确认）状态"""
        if self._monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            for p in list_active_projects():
                sf = get_state_file(p)
                try:
                    if Path(sf).exists() and Path(sf).read_text().strip().lower() == "permission":
                        return True
                except Exception:
                    pass
        return False

    def _get_session_states(self):
        """返回各来源的状态列表，用于多会话显示"""
        sessions = []
        if self._monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            # 多个项目聚合为一个 Claude 条目，取最高优先级状态（red > yellow > green）
            projects = list_active_projects()
            agg = "green"
            for p in projects:
                sf = get_state_file(p)
                try:
                    content = Path(sf).read_text().strip().lower() if Path(sf).exists() else "green"
                    st = content if content in ("green","yellow","red") else "green"
                except Exception:
                    st = "green"
                if st == "red" or (st == "yellow" and agg != "red"):
                    agg = st
            sessions.append({"state": agg, "label": "Claude"})
        if self._monitor_mode in (MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
            sessions.append({"state": get_catpaw_state(), "label": "CatPaw"})
        # 如果只有单来源，不显示 label（保持简洁）
        if len(sessions) == 1:
            sessions[0]["label"] = ""
        return sessions

    # ── StatusBar 显示 ─────────────────────────────────────

    def _update_status_title(self):
        if self._menubar_hidden:
            title = "🐾"
        else:
            lights = [LIGHT_OFF, LIGHT_OFF, LIGHT_OFF]
            if self._state == "green":
                lights[2] = LIGHT_ON["green"]
            elif self._state == "yellow":
                lights[1] = LIGHT_ON["yellow"] if self._blink_on else LIGHT_OFF
            else:
                lights[0] = LIGHT_ON["red"]
            title = " ".join(lights)
        try:
            self._status_item.button().setTitle_(title)
        except Exception:
            self._status_item.setTitle_(title)

    # ── 菜单构建 ───────────────────────────────────────────

    def _build_menu(self):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        # 今日统计摘要（只读）—— 黄灯时额外显示已执行秒数
        stats = _load_stats()
        runs   = stats.get("runs", 0)
        secs   = stats.get("total_seconds", 0)
        tokens = stats.get("total_tokens", 0)
        dur_text = _format_duration(secs) if secs > 0 else "0 秒"
        if runs > 0:
            stats_label = f"📈 今日：执行 {runs} 次，共 {dur_text}"
        else:
            stats_label = "📈 今日：暂无执行记录"
        # 黄灯时追加实时已用时
        if self._state == "yellow" and self._yellow_start > 0:
            cur_secs = int(time.time() - self._yellow_start)
            stats_label += f"  ·  本次 {_format_duration(cur_secs)}"
        mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(stats_label, None, "")
        mi.setEnabled_(False)
        menu.addItem_(mi)
        # token 统计行（仅 Claude 模式有数据时显示）
        if tokens > 0:
            token_label = f"🔢 今日 Claude Token：{_format_tokens(tokens)}"
            mi2 = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(token_label, None, "")
            mi2.setEnabled_(False)
            menu.addItem_(mi2)

        menu.addItem_(NSMenuItem.separatorItem())

        # 桌面挂件开关
        widget_label = "🖥️  桌面挂件：已开启" if self._widget_enabled else "🖥️  桌面挂件：已关闭"
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(widget_label, "toggleWidget:", "")
        item.setTarget_(self)
        menu.addItem_(item)

        # 菜单栏图标开关
        hide_label = "👁  菜单栏图标：已隐藏" if self._menubar_hidden else "👁  菜单栏图标：显示中"
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(hide_label, "toggleMenubar:", "")
        item.setTarget_(self)
        menu.addItem_(item)

        # 开机自启动
        autostart_label = "🚀 开机自动启动：已开启" if is_launch_agent_enabled() else "🚀 开机自动启动：已关闭"
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(autostart_label, "toggleAutoStart:", "")
        item.setTarget_(self)
        menu.addItem_(item)

        menu.addItem_(NSMenuItem.separatorItem())

        # 挂件尺寸子菜单
        size_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("📐 挂件大小", None, "")
        size_menu   = NSMenu.alloc().init()
        size_menu.setAutoenablesItems_(False)
        size_labels = [("small", "🔹 小"), ("medium", "🔷 中（默认）"), ("large", "🔶 大")]
        for size_key, size_lbl in size_labels:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(size_lbl, "selectSize:", "")
            mi.setTarget_(self)
            mi.setRepresentedObject_(size_key)
            if size_key == self._widget_size:
                mi.setState_(1)
            size_menu.addItem_(mi)
        size_parent.setSubmenu_(size_menu)
        menu.addItem_(size_parent)

        # 监控模式子菜单
        mode_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("🔍 监控模式", None, "")
        mode_menu   = NSMenu.alloc().init()
        mode_menu.setAutoenablesItems_(False)
        mode_items = [
(MONITOR_MODE_BOTH,   "🔀 两者都监控（Claude + CatPaw）"),
        (MONITOR_MODE_CLAUDE, "🤖 仅 Claude"),
        (MONITOR_MODE_CATPAW, "🐾 仅 CatPaw"),
        ]
        for mode_key, mode_label in mode_items:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(mode_label, "selectMode:", "")
            mi.setTarget_(self)
            mi.setRepresentedObject_(mode_key)
            if mode_key == self._monitor_mode:
                mi.setState_(1)
            mode_menu.addItem_(mi)
        mode_parent.setSubmenu_(mode_menu)
        menu.addItem_(mode_parent)

        # 项目子菜单（仅 Claude 模式显示）
        if self._monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            proj_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("📁 Claude 项目", None, "")
            proj_menu   = NSMenu.alloc().init()
            proj_menu.setAutoenablesItems_(False)
            projects = list_active_projects()
            if not projects:
                pi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("  (无活跃项目)", None, "")
                pi.setEnabled_(False)
                proj_menu.addItem_(pi)
            else:
                for p in projects:
                    pi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(f"  {p}", "selectProject:", "")
                    pi.setTarget_(self)
                    pi.setRepresentedObject_(p)
                    if p == self._selected_project:
                        pi.setState_(1)
                    proj_menu.addItem_(pi)
            proj_parent.setSubmenu_(proj_menu)
            menu.addItem_(proj_parent)
            self._last_projects = projects
        else:
            self._last_projects = list_active_projects()

        menu.addItem_(NSMenuItem.separatorItem())

        # 状态说明（只读）
        for label in [
            "📊 当前状态",
            "🟢 绿灯常亮 - 空闲 / 完成 / 成功",
            "🟡 黄灯闪烁 - 执行中 / 思考 / 工具调用",
            "🔴 红灯常亮 - 失败 / 取消 / 异常",
        ]:
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, None, "")
            mi.setEnabled_(False)
            menu.addItem_(mi)

        menu.addItem_(NSMenuItem.separatorItem())

        # 退出
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("退出", "quitApp:", "q")
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)

        self._status_item.setMenu_(menu)
        self._last_menu_build_time = time.time()

    # ── 菜单动作 ───────────────────────────────────────────

    def toggleWidget_(self, sender):
        self._widget_enabled = not self._widget_enabled
        set_widget_enabled(self._widget_enabled)
        if self._widget_enabled:
            self._show_widget()
        else:
            self._hide_widget()
        self._build_menu()

    def toggleMenubar_(self, sender):
        self._menubar_hidden = not self._menubar_hidden
        set_menubar_hidden(self._menubar_hidden)
        self._update_status_title()
        self._build_menu()

    def toggleAutoStart_(self, sender):
        if is_launch_agent_enabled():
            disable_launch_agent()
        else:
            enable_launch_agent()
        self._build_menu()

    def selectMode_(self, sender):
        self._monitor_mode = sender.representedObject()
        set_monitor_mode(self._monitor_mode)
        self._build_menu()

    def selectProject_(self, sender):
        self._selected_project = sender.representedObject().strip()
        set_selected_project(self._selected_project)
        self._build_menu()

    def selectSize_(self, sender):
        self._widget_size = sender.representedObject()
        set_widget_size(self._widget_size)
        # 重建挂件窗口（尺寸变了）
        was_visible = self._widget_enabled
        old_pos = None
        if self._widget_window:
            origin = self._widget_window.frame().origin
            old_pos = (origin.x, origin.y)
            self._widget_window.orderOut_(None)
        self._create_widget_window(restore_pos=old_pos)
        if was_visible:
            self._show_widget()
        self._build_menu()

    def quitApp_(self, sender):
        restore_config()
        NSApplication.sharedApplication().terminate_(None)

    # ── 桌面挂件 ───────────────────────────────────────────

    def _create_widget_window(self, restore_pos=None):
        s = WIDGET_SIZES.get(self._widget_size, WIDGET_SIZES["medium"])
        w, h = s["w"], s["h"]

        # 决定初始位置：优先恢复保存的位置，并做越界保护
        pos = restore_pos or _load_widget_position()
        if pos:
            x, y = _clamp_position_to_screen(pos[0], pos[1], w, h)
        else:
            screen = NSScreen.mainScreen()
            if screen:
                sf = screen.visibleFrame()
                x = sf.origin.x + sf.size.width  - w - 20
                y = sf.origin.y + sf.size.height - h - 20
            else:
                x, y = 400, 300
        _log(f"创建挂件窗口，尺寸=({w},{h})，位置=({x:.0f},{y:.0f})")

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h),
            NSBorderlessWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        win.setLevel_(NSFloatingWindowLevel)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setOpaque_(False)
        win.setHasShadow_(False)
        win.invalidateShadow()
        win.setMovableByWindowBackground_(True)
        win.setCollectionBehavior_((1 << 2) | (1 << 3))
        win.setReleasedWhenClosed_(False)

        ucc = WKUserContentController.alloc().init()
        resize_handler = _PawResizeHandler.alloc().init()
        ucc.addScriptMessageHandler_name_(resize_handler, "pawResize")
        cfg = WKWebViewConfiguration.alloc().init()
        cfg.setUserContentController_(ucc)

        wk = DraggableWKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, w, h), cfg
        )
        wk.setOpaque_(False)
        wk.setValue_forKey_(False, "drawsBackground")
        html = _build_widget_html(self._widget_size)
        # 使用本地 file:// baseURL，确保 messageHandlers 不被安全策略拦截
        tmp_base = NSURL.fileURLWithPath_(os.path.expanduser("~"))
        wk.loadHTMLString_baseURL_(
            NSString.stringWithString_(html),
            tmp_base
        )

        win.setContentView_(wk)
        self._widget_window = win
        self._wkview = wk
        _log("挂件窗口创建完成")

    def _show_widget(self):
        if self._widget_window:
            self._widget_window.orderFrontRegardless()
            _log(f"挂件已显示: isVisible={self._widget_window.isVisible()}")
            self._push_state_to_widget()

    def _hide_widget(self):
        set_widget_enabled(False)
        self._widget_enabled = False
        if self._widget_window:
            self._widget_window.orderOut_(None)
        self._build_menu()
        _log("挂件已隐藏")

    def _push_state_to_widget(self):
        if not self._wkview:
            return
        # 1. 更新固定三灯（根据聚合状态）
        combined = self._get_combined_state()
        sessions = self._get_session_states()
        arr = json.dumps(sessions, ensure_ascii=False)
        # 传入黄灯开始的 JS 时间戳（毫秒），让 JS 端自行计时
        yellow_start_ms = int(self._yellow_start * 1000) if self._yellow_start > 0 else 0
        needs_confirm = "true" if self._needs_confirm() else "false"
        js = f"updateState('{combined}', {yellow_start_ms}, {needs_confirm}); updateSessions({arr})"
        # 今日统计 bar
        stats = _load_stats()
        runs   = stats.get("runs", 0)
        secs   = stats.get("total_seconds", 0)
        tokens = stats.get("total_tokens", 0)
        dur_text = _format_duration(secs) if secs > 0 else ""
        parts = [f"今日 {runs} 次"]
        if secs > 0:
            parts.append(dur_text)
        if tokens > 0:
            parts.append(f"Claude {_format_tokens(tokens)} tokens")
        stats_text = "  ".join(parts)
        js += f"; showStats({json.dumps(stats_text)})"
        self._wkview.evaluateJavaScript_completionHandler_(
            NSString.stringWithString_(js), None
        )


# ---------- 入口 ----------
def main():
    _log("正在配置 Claude Code hooks...")
    configure_hooks()

    atexit.register(restore_config)

    def signal_handler(sig, frame):
        restore_config()
        os._exit(0)
    signal.signal(signal.SIGINT,  signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    _ensure_log_watcher()

    # 启动 token 扫描后台线程（每 30 秒增量扫描一次）
    def _token_scan_loop():
        while True:
            try:
                _scan_today_tokens()
            except Exception:
                pass
            time.sleep(30)
    t = threading.Thread(target=_token_scan_loop, daemon=True)
    t.start()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)

    _log("启动 PawSignal (纯 PyObjC)...")
    app.run()
    restore_config()


if __name__ == "__main__":
    main()
