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
    NSTimer,
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
        except Exception:
            pass

    def mouseUp_(self, event):
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


# ---------- CatPaw 状态监听 ----------
_catpaw_state_cache    = "green"
_catpaw_last_event_time = 0.0
_catpaw_log_thread     = None
_catpaw_pending_green_at = 0.0
_catpaw_cancelled_at   = 0.0
CATPAW_GREEN_DELAY     = 2.0
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
# 日志路径：~/Library/Application Support/CatPaw/logs/<时间戳>/window*/exthost/output_logging_*/3-Hook Log.log
# Hook 事件格式：
#   beforeSubmitPrompt   → 用户提交，Agent 开始处理（黄灯）
#   afterAgentResponse   → Agent 响应完成（绿灯）
#   beforeShellExecution → 工具调用开始（黄灯）
#   afterShellExecution  → 工具调用结束（继续等 afterAgentResponse 转绿）
#   stop                 → 手动停止（红灯）

_CATPAW_VSCODE_LOG_BASE = Path("~/Library/Application Support/CatPaw/logs").expanduser()

def _find_catpaw_vscode_logs():
    """扫描 CatPaw VSCode 版的最新 Hook Log 文件（支持多 window）"""
    if not _CATPAW_VSCODE_LOG_BASE.exists():
        return []
    # 按时间戳目录名倒序取最新的几个会话
    session_dirs = sorted(_CATPAW_VSCODE_LOG_BASE.glob("*/"), reverse=True)[:3]
    logs = []
    for session_dir in session_dirs:
        # 匹配 output_logging_*/3-Hook Log.log（Hook 事件写在这里）
        for log_path in session_dir.glob("window*/exthost/output_logging_*/3-Hook Log.log"):
            logs.append(log_path)
    return logs

def _catpaw_vscode_log_watcher_single(log_path):
    """监听 CatPaw VSCode 独立客户端的 1-Catpaw.log Hook 事件"""
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
                # 用户提交 / 工具调用开始 → 黄灯
                if ("beforeSubmitPrompt" in line or "beforeShellExecution" in line or
                        "beforeReadFile" in line):
                    now = time.time()
                    _catpaw_last_event_time = now
                    if now - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                        _catpaw_state_cache = "yellow"
                        _catpaw_pending_green_at = 0.0
                # Agent 响应完成 → 延迟转绿
                elif "afterAgentResponse" in line:
                    now = time.time()
                    _catpaw_last_event_time = now
                    if now - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                        _catpaw_pending_green_at = now
                # stop 在 CatPaw 中是正常结束信号（每次 Agent 完成都会触发）
                # 不作为红灯，而是视为完成，触发延迟转绿
                elif "] Hook step requested: stop" in line:
                    now = time.time()
                    _catpaw_last_event_time = now
                    if now - _catpaw_cancelled_at >= CATPAW_CANCEL_PROTECT:
                        _catpaw_pending_green_at = now
    except Exception:
        pass

# ---------- 统一日志监听调度 ----------
# CatPaw VSCode 版每次启动会创建新时间戳目录，需要持续扫描新文件
_catpaw_watched_paths = set()   # 已启动监听的日志路径集合
_catpaw_watcher_lock  = threading.Lock()

def _start_watcher_for_path(log_path, watcher_fn):
    """为单个日志路径启动监听线程（幂等：同路径只启动一次）"""
    path_str = str(log_path)
    with _catpaw_watcher_lock:
        if path_str in _catpaw_watched_paths:
            return
        _catpaw_watched_paths.add(path_str)
    t = threading.Thread(target=watcher_fn, args=(path_str,), daemon=True)
    t.start()
    _log(f"CatPaw 日志监听已启动: {path_str}")

def _catpaw_log_scanner():
    """持续扫描新的 CatPaw 日志文件并启动对应监听线程"""
    global _catpaw_log_thread
    while True:
        # JetBrains 插件版
        for log_path in _find_idea_logs():
            _start_watcher_for_path(log_path, _catpaw_jetbrains_log_watcher_single)
        # VSCode 独立客户端版（每次重扫，感知新会话目录和新 output_logging_ 目录）
        for log_path in _find_catpaw_vscode_logs():
            _start_watcher_for_path(log_path, _catpaw_vscode_log_watcher_single)
        time.sleep(5)   # 每 5 秒重扫一次

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
            shutil.rmtree(STATE_DIR)
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

    permission_tools = "Bash|Write|Edit|NotebookEdit|WebFetch"
    desired = {
        "SessionStart":      [_make_hook_entry(_hook_cmd("green"))],
        "UserPromptSubmit":  [_make_hook_entry(_hook_cmd("yellow"))],
        "PermissionRequest": [_make_hook_entry(_hook_cmd("yellow"))],
        "PreToolUse":        [_make_hook_entry(_hook_cmd("yellow"), matcher=permission_tools)],
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


# ---------- 桌面挂件 HTML ----------
WIDGET_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html {
    background: transparent;
  }
  body {
    background: transparent;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', sans-serif;
    -webkit-app-region: drag;
    user-select: none;
    -webkit-user-select: none;
  }
  .card {
    width: 88px;
    background: rgba(24, 24, 26, 0.82);
    backdrop-filter: blur(36px) saturate(160%);
    -webkit-backdrop-filter: blur(36px) saturate(160%);
    border-radius: 20px;
    border: 0.5px solid rgba(255, 255, 255, 0.13);
    box-shadow:
      0 4px 28px rgba(0, 0, 0, 0.35),
      0 1px 4px rgba(0, 0, 0, 0.2),
      inset 0 0.5px 0 rgba(255, 255, 255, 0.12);
    padding: 14px 0 12px;
    display: flex; flex-direction: column; align-items: center;
    position: relative;
    will-change: transform;
    transform: translateZ(0);
    -webkit-transform: translateZ(0);
  }
  .title {
    font-size: 8px; font-weight: 600; letter-spacing: 0.1em;
    color: rgba(255,255,255,0.45); text-transform: uppercase;
    margin-bottom: 12px; -webkit-app-region: drag;
  }
  .lights { display: flex; flex-direction: column; align-items: center; width: 100%; }
  .light-unit {
    display: flex; flex-direction: column; align-items: center;
    gap: 5px; padding: 7px 0; width: 100%; position: relative;
  }
  .light-unit + .light-unit::before {
    content: ''; position: absolute; top: 0; left: 18px; right: 18px;
    height: 0.5px; background: rgba(255,255,255,0.06);
  }
  .dot {
    width: 22px; height: 22px; border-radius: 50%;
    transition: all 0.5s cubic-bezier(0.4,0,0.2,1);
    position: relative; flex-shrink: 0;
  }
  .dot.off  { background: rgba(255,255,255,0.06); box-shadow: inset 0 1px 3px rgba(0,0,0,0.5); }
  .dot.red  {
    background: radial-gradient(circle at 38% 32%, #ff6e63, #ff3b30 55%, #c62218);
    box-shadow: 0 0 0 3px rgba(255,59,48,.15), 0 0 14px rgba(255,59,48,.55), 0 0 30px rgba(255,59,48,.2);
  }
  .dot.yellow {
    background: radial-gradient(circle at 38% 32%, #ffe066, #ffd60a 55%, #c9a000);
    box-shadow: 0 0 0 3px rgba(255,214,10,.15), 0 0 14px rgba(255,214,10,.6), 0 0 30px rgba(255,214,10,.22);
    animation: pulse 1.4s ease-in-out infinite;
  }
  .dot.green {
    background: radial-gradient(circle at 38% 32%, #5dff7e, #30d158 55%, #178c38);
    box-shadow: 0 0 0 3px rgba(48,209,88,.15), 0 0 14px rgba(48,209,88,.5), 0 0 30px rgba(48,209,88,.18);
  }
  .dot::after {
    content: ''; position: absolute; top: 4px; left: 5px;
    width: 8px; height: 5px; background: rgba(255,255,255,0.4);
    border-radius: 50%; filter: blur(1px); pointer-events: none; transition: opacity 0.5s;
  }
  .dot.off::after { opacity: 0; }
  @keyframes pulse { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:.6;transform:scale(.92)} }
  .dot-label {
    font-size: 9px; font-weight: 400; color: rgba(255,255,255,0.2);
    height: 11px; text-align: center; transition: color 0.4s, font-weight 0.4s;
    -webkit-app-region: drag;
  }
  .light-unit.active-red    .dot-label { color: rgba(255,90,75,.9);   font-weight: 500; }
  .light-unit.active-yellow .dot-label { color: rgba(255,210,10,.9);  font-weight: 500; }
  .light-unit.active-green  .dot-label { color: rgba(48,209,88,.88);  font-weight: 500; }
  .close-btn {
    position: absolute; top: 8px; right: 8px;
    width: 14px; height: 14px; border-radius: 50%;
    background: rgba(255,255,255,0.07); border: none; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    opacity: 0; transition: opacity 0.18s, background 0.18s;
    -webkit-app-region: no-drag; font-size: 9px;
    color: rgba(255,255,255,0.45); line-height: 1;
  }
  .card:hover .close-btn { opacity: 1; }
  .close-btn:hover { background: rgba(255,59,48,.55); color: rgba(255,255,255,.9); }
</style>
</head>
<body>
<div class="card">
  <button class="close-btn" onclick="window.webkit.messageHandlers.pawClose.postMessage('')">✕</button>
  <div class="title">PawSignal</div>
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
</div>
<script>
  function updateState(state) {
    const dots  = { red: document.getElementById('dot-red'), yellow: document.getElementById('dot-yellow'), green: document.getElementById('dot-green') };
    const units = { red: document.getElementById('unit-red'), yellow: document.getElementById('unit-yellow'), green: document.getElementById('unit-green') };
    Object.values(dots).forEach(d => d.className = 'dot off');
    Object.values(units).forEach(u => u.className = 'light-unit');
    if (state === 'red')         { dots.red.className = 'dot red';       units.red.className = 'light-unit active-red'; }
    else if (state === 'yellow') { dots.yellow.className = 'dot yellow'; units.yellow.className = 'light-unit active-yellow'; }
    else                         { dots.green.className = 'dot green';   units.green.className = 'light-unit active-green'; }
  }
</script>
</body>
</html>"""


# ---------- WKScriptMessageHandler：JS → Python ----------
_WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")

class PawCloseHandler(objc.lookUpClass("NSObject"), protocols=[_WKScriptMessageHandler]):
    """接收挂件内关闭按钮点击"""
    def userContentController_didReceiveScriptMessage_(self, controller, message):
        # 主线程回调：隐藏窗口、更新状态
        _app_delegate_ref[0]._hide_widget()


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

        # 应用以 Accessory 模式运行：无 Dock 图标，无 Cmd+Tab，但可显示窗口
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )

        # 内部状态
        self._state         = "green"
        self._blink_on      = True
        self._monitor_mode  = get_monitor_mode()
        self._widget_enabled = get_widget_enabled()
        self._menubar_hidden = get_menubar_hidden()
        self._selected_project = get_selected_project()
        self._last_projects = []
        self._last_menu_build_time = 0.0
        self._wkview        = None
        self._widget_window = None

        # 创建 StatusBar 图标
        self._status_bar  = NSStatusBar.systemStatusBar()
        self._status_item = self._status_bar.statusItemWithLength_(-1)  # NSVariableStatusItemLength
        self._status_item.setHighlightMode_(True)
        self._update_status_title()
        self._build_menu()

        # 创建桌面挂件窗口（一次性，之后只 show/hide）
        self._create_widget_window()

        if self._widget_enabled:
            self._show_widget()

        # 启动轮询定时器（主线程 RunLoop）
        self._poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL, self, "onPollTimer:", None, True
        )
        self._blink_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            BLINK_INTERVAL, self, "onBlinkTimer:", None, True
        )
        _log("AppDelegate 初始化完成")

    # ── 定时器回调 ──────────────────────────────────────────

    def onPollTimer_(self, timer):
        new_state = self._get_combined_state()
        if new_state != self._state:
            self._state   = new_state
            self._blink_on = True
            self._update_status_title()

        if self._widget_enabled and self._wkview:
            self._push_state_to_widget()

        # 定期刷新菜单（检测新项目）
        now = time.time()
        if now - self._last_menu_build_time > MENU_REFRESH_INTERVAL:
            projects = list_active_projects()
            if projects and self._selected_project not in projects:
                self._selected_project = projects[0]
                set_selected_project(self._selected_project)
            if projects != self._last_projects:
                self._build_menu()

    def onBlinkTimer_(self, timer):
        self._blink_on = not self._blink_on
        self._update_status_title()

    # ── 状态计算 ───────────────────────────────────────────

    def _get_combined_state(self):
        states = []
        if self._monitor_mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
            sf = get_state_file(self._selected_project)
            try:
                content = Path(sf).read_text().strip().lower() if Path(sf).exists() else "green"
                states.append(content if content in ("green","yellow","red") else "green")
            except Exception:
                states.append("green")
        if self._monitor_mode in (MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
            states.append(get_catpaw_state())
        if "red"    in states: return "red"
        if "yellow" in states: return "yellow"
        return "green"

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
        # NSStatusItem 用 button（macOS 10.10+）或 title
        try:
            self._status_item.button().setTitle_(title)
        except Exception:
            self._status_item.setTitle_(title)

    # ── 菜单构建 ───────────────────────────────────────────

    def _build_menu(self):
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

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

        menu.addItem_(NSMenuItem.separatorItem())

        # 监控模式子菜单
        mode_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("🔍 监控模式", None, "")
        mode_menu   = NSMenu.alloc().init()
        mode_menu.setAutoenablesItems_(False)
        mode_items = [
            (MONITOR_MODE_BOTH,   "🔀 两者都监控（Claude Code + CatPaw）"),
            (MONITOR_MODE_CLAUDE, "🤖 仅 Claude Code"),
            (MONITOR_MODE_CATPAW, "🐾 仅 CatPaw（JetBrains 插件 / VSCode 客户端）"),
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
            proj_parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("📁 Claude Code 项目", None, "")
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

    def selectMode_(self, sender):
        self._monitor_mode = sender.representedObject()
        set_monitor_mode(self._monitor_mode)
        self._build_menu()

    def selectProject_(self, sender):
        self._selected_project = sender.representedObject().strip()
        set_selected_project(self._selected_project)
        self._build_menu()

    def quitApp_(self, sender):
        restore_config()
        NSApplication.sharedApplication().terminate_(None)

    # ── 桌面挂件 ───────────────────────────────────────────

    def _create_widget_window(self):
        screen = NSScreen.mainScreen()
        if screen:
            sf = screen.visibleFrame()
            x = sf.origin.x + sf.size.width  - 100 - 20
            y = sf.origin.y + sf.size.height - 230 - 20
        else:
            x, y = 400, 300
        _log(f"创建挂件窗口，位置=({x:.0f},{y:.0f})")

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, 100, 230),
            NSBorderlessWindowMask,
            NSBackingStoreBuffered,
            False,
        )
        win.setLevel_(NSFloatingWindowLevel)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setOpaque_(False)
        win.setHasShadow_(False)
        win.setMovableByWindowBackground_(True)
        # canJoinAllSpaces(1<<3) | canManageApplication(1<<2)
        win.setCollectionBehavior_((1 << 2) | (1 << 3))
        win.setReleasedWhenClosed_(False)

        ucc = WKUserContentController.alloc().init()
        handler = PawCloseHandler.alloc().init()
        ucc.addScriptMessageHandler_name_(handler, "pawClose")
        cfg = WKWebViewConfiguration.alloc().init()
        cfg.setUserContentController_(ucc)

        wk = DraggableWKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, 100, 230), cfg
        )
        wk.setOpaque_(False)
        wk.setValue_forKey_(False, "drawsBackground")
        wk.loadHTMLString_baseURL_(
            NSString.stringWithString_(WIDGET_HTML),
            NSURL.URLWithString_("about:blank")
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
        state = self._get_combined_state()
        js = NSString.stringWithString_(f"updateState('{state}')")
        self._wkview.evaluateJavaScript_completionHandler_(js, None)


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

    # 启动 CatPaw 日志监听
    _ensure_log_watcher()

    app = NSApplication.sharedApplication()
    # Accessory 模式：无 Dock 图标，可显示窗口
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)

    _log("启动 PawSignal (纯 PyObjC)...")
    app.run()
    restore_config()


if __name__ == "__main__":
    main()
