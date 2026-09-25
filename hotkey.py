#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局热键模块：用 Win32 GetAsyncKeyState 监听 `` ` `` 键（1 键左边，VK_OEM_3）。

背景：无限全自动对战（6）/自动识别（4）等模式运行时，脚本会
把焦点切到游戏窗口并自动点击鼠标，终端（msvcrt）读不到键盘输入，导致
Q/Esc 停止键失效。本模块直接读取物理键盘状态，与窗口焦点无关 —— 无论
焦点在哪，按 `` ` `` 键都能立即退出当前模式并返回主菜单。

用法：
    from hotkey import poll_stop

    while True:
        if poll_stop():   # `` ` `` 被新按下（边沿触发）
            return        # 立即退出当前模式返回主菜单
        ...

仅支持 Windows（本项目核心即 Windows：msvcrt / ctypes.windll）。
"""

import ctypes

# `` ` `` / ~ 键（1 键左边的物理键）的虚拟键码（VK_OEM_3）
VK_OEM_3 = 0xC0

# 边沿检测状态：记录上一轮该键是否处于按下状态
_state = {'down': False}


def _read_key_state():
    """读取 `` ` `` 键当前是否按下（全局，与窗口焦点无关）。"""
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK_OEM_3) & 0x8000)


def poll_stop():
    """非阻塞检测 `` ` `` 键是否被【新按下】。

    边沿触发：只有「上次松开 → 本次按下」这一瞬间返回 True，
    按住不放不会重复触发，适合放入各模式的主循环里轮询。

    Returns:
        bool: True = 刚按下了 `` ` `` 键，应退出当前模式返回主菜单。
    """
    try:
        down = _read_key_state()
    except Exception:
        down = False
    pressed = down and not _state['down']
    _state['down'] = down
    return pressed
