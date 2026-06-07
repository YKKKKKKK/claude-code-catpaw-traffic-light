#!/usr/bin/env python3
"""
PawSignal 桌面悬浮窗版本
————————————————————————
在桌面上显示一个半透明磨砂玻璃风格的红绿灯小挂件，
实时反映 Claude Code 和 CatPaw 的 Agent 工作状态。

状态说明：
  🟢 绿灯常亮  —— 空闲 / 完成 / 成功
  🟡 黄灯呼吸  —— Agent 正在执行 / 思考 / 调用工具
  🔴 红灯常亮  —— 失败 / 取消 / 异常

复用 traffic_light.py 的全部状态监听逻辑，仅替换 UI 层。
"""

import sys
import os
import json
import time
import threading
import signal
import atexit

# 兼容打包后路径
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

# 复用 traffic_light.py 的核心逻辑
from traffic_light import (
    configure_hooks,
    restore_config,
    get_catpaw_state,
    get_monitor_mode,
    set_monitor_mode,
    get_selected_project,
    list_active_projects,
    get_state_file,
    _find_idea_logs,
    _catpaw_state_cache,
    MONITOR_MODE_CLAUDE,
    MONITOR_MODE_CATPAW,
    MONITOR_MODE_BOTH,
    CATPAW_GREEN_DELAY,
    CATPAW_CANCEL_PROTECT,
)
import traffic_light as _tl
from pathlib import Path
import webview

# ---------- HTML / CSS / JS ----------
HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: transparent;
    overflow: hidden;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'SF Pro Display', sans-serif;
    -webkit-app-region: drag;
    user-select: none;
  }

  /* 主卡片：深色磨砂玻璃 */
  .card {
    width: 88px;
    background: rgba(24, 24, 26, 0.72);
    backdrop-filter: blur(36px) saturate(160%);
    -webkit-backdrop-filter: blur(36px) saturate(160%);
    border-radius: 20px;
    border: 0.5px solid rgba(255, 255, 255, 0.13);
    box-shadow:
      0 4px 28px rgba(0, 0, 0, 0.35),
      0 1px 4px rgba(0, 0, 0, 0.2),
      inset 0 0.5px 0 rgba(255, 255, 255, 0.12),
      inset 0 -0.5px 0 rgba(0, 0, 0, 0.1);
    padding: 14px 0 12px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
    position: relative;
  }

  /* 标题 */
  .title {
    font-size: 8px;
    font-weight: 600;
    letter-spacing: 0.1em;
    color: rgba(255, 255, 255, 0.45);
    text-transform: uppercase;
    margin-bottom: 12px;
    -webkit-app-region: drag;
  }

  /* 灯列容器 */
  .lights {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
    width: 100%;
  }

  /* 单灯单元：圆点 + 文字 */
  .light-unit {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 5px;
    padding: 7px 0;
    width: 100%;
    position: relative;
  }

  /* 分隔线 */
  .light-unit + .light-unit::before {
    content: '';
    position: absolute;
    top: 0;
    left: 18px;
    right: 18px;
    height: 0.5px;
    background: rgba(255, 255, 255, 0.06);
  }

  /* 圆形指示灯 */
  .dot {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
    position: relative;
    flex-shrink: 0;
  }

  /* 熄灭 */
  .dot.off {
    background: rgba(255, 255, 255, 0.06);
    box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.5);
  }

  /* 红灯 */
  .dot.red {
    background: radial-gradient(circle at 38% 32%, #ff6e63, #ff3b30 55%, #c62218);
    box-shadow:
      0 0 0 3px rgba(255, 59, 48, 0.15),
      0 0 14px rgba(255, 59, 48, 0.55),
      0 0 30px rgba(255, 59, 48, 0.2);
  }

  /* 黄灯 */
  .dot.yellow {
    background: radial-gradient(circle at 38% 32%, #ffe066, #ffd60a 55%, #c9a000);
    box-shadow:
      0 0 0 3px rgba(255, 214, 10, 0.15),
      0 0 14px rgba(255, 214, 10, 0.6),
      0 0 30px rgba(255, 214, 10, 0.22);
    animation: pulse 1.4s ease-in-out infinite;
  }

  /* 绿灯 */
  .dot.green {
    background: radial-gradient(circle at 38% 32%, #5dff7e, #30d158 55%, #178c38);
    box-shadow:
      0 0 0 3px rgba(48, 209, 88, 0.15),
      0 0 14px rgba(48, 209, 88, 0.5),
      0 0 30px rgba(48, 209, 88, 0.18);
  }

  /* 灯面高光 */
  .dot::after {
    content: '';
    position: absolute;
    top: 4px;
    left: 5px;
    width: 8px;
    height: 5px;
    background: rgba(255, 255, 255, 0.4);
    border-radius: 50%;
    filter: blur(1px);
    pointer-events: none;
    transition: opacity 0.5s;
  }
  .dot.off::after { opacity: 0; }

  /* 黄灯呼吸 */
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%       { opacity: 0.6; transform: scale(0.92); }
  }

  /* 灯下文字 */
  .dot-label {
    font-size: 9px;
    font-weight: 400;
    letter-spacing: 0.01em;
    color: rgba(255, 255, 255, 0.2);
    height: 11px;
    text-align: center;
    transition: color 0.4s, font-weight 0.4s;
    -webkit-app-region: drag;
  }

  .light-unit.active-red    .dot-label { color: rgba(255, 90, 75, 0.9);  font-weight: 500; }
  .light-unit.active-yellow .dot-label { color: rgba(255, 210, 10, 0.9); font-weight: 500; }
  .light-unit.active-green  .dot-label { color: rgba(48, 209, 88, 0.88); font-weight: 500; }

  /* 关闭按钮 */
  .close-btn {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.07);
    border: none;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.18s, background 0.18s;
    -webkit-app-region: no-drag;
    font-size: 9px;
    color: rgba(255, 255, 255, 0.45);
    line-height: 1;
  }
  .card:hover .close-btn { opacity: 1; }
  .close-btn:hover {
    background: rgba(255, 59, 48, 0.55);
    color: rgba(255, 255, 255, 0.9);
  }
</style>
</head>
<body>
<div class="card">
  <button class="close-btn" onclick="pywebview.api.close_widget()">✕</button>
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
    const dots  = { red: document.getElementById('dot-red'),  yellow: document.getElementById('dot-yellow'),  green: document.getElementById('dot-green')  };
    const units = { red: document.getElementById('unit-red'), yellow: document.getElementById('unit-yellow'), green: document.getElementById('unit-green') };

    Object.values(dots).forEach(d => d.className = 'dot off');
    Object.values(units).forEach(u => u.className = 'light-unit');

    if (state === 'red') {
      dots.red.className  = 'dot red';
      units.red.className = 'light-unit active-red';
    } else if (state === 'yellow') {
      dots.yellow.className  = 'dot yellow';
      units.yellow.className = 'light-unit active-yellow';
    } else {
      dots.green.className  = 'dot green';
      units.green.className = 'light-unit active-green';
    }
  }
</script>
</body>
</html>
"""


# ---------- Python 与 JS 的桥接 ----------
class WidgetAPI:
    """暴露给 JS 调用的 Python 方法"""

    def close_widget(self):
        """点击关闭按钮退出"""
        restore_config()
        os._exit(0)


# ---------- 启动时扫描日志历史，推断初始状态 ----------
def _init_catpaw_state_from_history(tail_lines=500):
    """
    启动时读取日志末尾 tail_lines 行，找到最后一条 AgentTabService 状态行，
    以此作为 CatPaw 的初始状态，避免新启动时因没有历史而一直显示绿灯。
    """
    logs = _find_idea_logs()
    if not logs:
        return

    last_status = None
    last_ts = 0.0

    for log_path in logs:
        try:
            with open(str(log_path), "r", encoding="utf-8", errors="replace") as f:
                # 高效读取末尾 N 行
                f.seek(0, 2)
                size = f.tell()
                # 每行平均约 200 字节，读取 tail_lines 行
                read_bytes = min(size, tail_lines * 200)
                f.seek(max(0, size - read_bytes))
                lines = f.readlines()

            for line in reversed(lines):
                if "AgentTabService" in line and "Tab状态已更新" in line:
                    if "Status: running" in line:
                        last_status = "yellow"
                    elif "Status: completed" in line:
                        last_status = "green"
                    elif "Status: cancelled" in line or "Status: failed" in line or "Status: error" in line:
                        last_status = "red"
                    if last_status:
                        break
        except Exception:
            pass

    if last_status:
        _tl._catpaw_state_cache = last_status


# ---------- 状态轮询线程 ----------
def _state_watcher(window, monitor_mode_ref):
    """后台线程：每 300ms 读一次状态，有变化时推送到前端 JS"""
    last_state = None
    while True:
        try:
            mode = monitor_mode_ref[0]
            states = []

            if mode in (MONITOR_MODE_CLAUDE, MONITOR_MODE_BOTH):
                project = get_selected_project()
                state_file = get_state_file(project)
                try:
                    if Path(state_file).exists():
                        content = Path(state_file).read_text().strip().lower()
                        states.append(content if content in ("green", "yellow", "red") else "green")
                    else:
                        states.append("green")
                except Exception:
                    states.append("green")

            if mode in (MONITOR_MODE_CATPAW, MONITOR_MODE_BOTH):
                states.append(get_catpaw_state())

            # 合并：red > yellow > green
            if "red" in states:
                current = "red"
            elif "yellow" in states:
                current = "yellow"
            else:
                current = "green"

            if current != last_state:
                last_state = current
                window.evaluate_js(f"updateState('{current}')")

        except Exception:
            pass
        time.sleep(0.3)


# ---------- 入口 ----------
def main():
    print("正在配置 Claude Code hooks...")
    configure_hooks()

    # 启动时扫描日志历史，推断 CatPaw 当前状态（避免初始时显示错误的绿灯）
    print("扫描日志历史，推断初始状态...")
    _init_catpaw_state_from_history()

    atexit.register(restore_config)

    def signal_handler(sig, frame):
        restore_config()
        os._exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 监控模式（可通过文件动态修改）
    monitor_mode_ref = [get_monitor_mode()]

    api = WidgetAPI()

    # 创建透明悬浮窗
    window = webview.create_window(
        title="PawSignal",
        html=HTML,
        width=100,
        height=230,
        x=40,
        y=100,
        resizable=False,
        frameless=True,          # 无系统标题栏
        transparent=True,        # 背景透明
        on_top=True,             # 始终置顶
        js_api=api,
    )

    # 启动状态监听线程
    t = threading.Thread(target=_state_watcher, args=(window, monitor_mode_ref), daemon=True)
    t.start()

    print("启动 PawSignal 桌面挂件...")
    webview.start(debug=False)


if __name__ == "__main__":
    main()

