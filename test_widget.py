#!/usr/bin/env python3
"""独立测试 NSPanel + WKWebView 窗口显示"""
from AppKit import (
    NSPanel, NSColor, NSFloatingWindowLevel,
    NSBorderlessWindowMask, NSBackingStoreBuffered, NSMakeRect,
    NSApplication,
)
from WebKit import WKWebView, WKWebViewConfiguration
from Foundation import NSURL, NSString
import time, threading

app = NSApplication.sharedApplication()

panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
    NSMakeRect(200, 400, 120, 250),
    NSBorderlessWindowMask,
    NSBackingStoreBuffered,
    False,
)
panel.setLevel_(NSFloatingWindowLevel)
panel.setBackgroundColor_(NSColor.redColor())  # 红色背景，方便确认是否显示
panel.setOpaque_(True)
panel.setHasShadow_(True)
panel.setMovableByWindowBackground_(True)

cfg = WKWebViewConfiguration.alloc().init()
wk = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 120, 250), cfg)
wk.setOpaque_(False)
wk.setBackgroundColor_(NSColor.clearColor())
html = NSString.stringWithString_(
    '<html><body style="background:rgba(30,30,30,0.9);display:flex;align-items:center;justify-content:center;height:100vh;margin:0">'
    '<h2 style="color:white;font-family:sans-serif">PawSignal</h2></body></html>'
)
wk.loadHTMLString_baseURL_(html, NSURL.URLWithString_("about:blank"))
panel.setContentView_(wk)

panel.orderFrontRegardless()
print("窗口已调用 orderFront_，等待5秒...")

def auto_quit():
    time.sleep(5)
    app.terminate_(None)

t = threading.Thread(target=auto_quit, daemon=True)
t.start()

app.run()
print("完成")

