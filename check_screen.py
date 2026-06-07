#!/usr/bin/env python3
"""检查屏幕信息"""
from AppKit import NSScreen, NSApplication

NSApplication.sharedApplication()

for i, screen in enumerate(NSScreen.screens()):
    frame = screen.frame()
    vf = screen.visibleFrame()
    print(f"Screen {i}: frame=({frame.origin.x:.0f},{frame.origin.y:.0f},{frame.size.width:.0f}x{frame.size.height:.0f})")
    print(f"         visibleFrame=({vf.origin.x:.0f},{vf.origin.y:.0f},{vf.size.width:.0f}x{vf.size.height:.0f})")
    print(f"         backingScaleFactor={screen.backingScaleFactor()}")

