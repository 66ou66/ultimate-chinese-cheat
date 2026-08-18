#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hotkey 全局热键（`` ` `` 键）边沿检测单元测试。

不依赖真实键盘：通过替换 hotkey._read_key_state 模拟按键状态序列，
验证「长按只触发一次」和「松手后再按可再次触发」的边沿语义。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotkey  # noqa: E402


def _reset():
    hotkey._state['down'] = False


def _mock(seq):
    it = iter(seq)
    hotkey._read_key_state = lambda: next(it)


def test_never_pressed_returns_false():
    _reset()
    _mock([False, False, False])
    assert hotkey.poll_stop() is False
    assert hotkey.poll_stop() is False
    assert hotkey.poll_stop() is False


def test_edge_triggers_once_while_held():
    """按住不放：只在按下瞬间触发一次，后续轮询不重复触发。"""
    _reset()
    _mock([False, True, True, True, False])
    assert hotkey.poll_stop() is False  # 松开
    assert hotkey.poll_stop() is True   # 按下瞬间 → 触发
    assert hotkey.poll_stop() is False  # 仍按住 → 不重复
    assert hotkey.poll_stop() is False  # 仍按住 → 不重复
    assert hotkey.poll_stop() is False  # 松开 → 复位


def test_press_release_press_again_triggers_twice():
    """松手后再按：可以再次触发（下次退出模式时依然有效）。"""
    _reset()
    _mock([False, True, False, True, False])
    assert hotkey.poll_stop() is False
    assert hotkey.poll_stop() is True   # 第一次按下
    assert hotkey.poll_stop() is False  # 松开
    assert hotkey.poll_stop() is True   # 再次按下 → 再次触发
    assert hotkey.poll_stop() is False  # 松开


def test_exception_treated_as_not_pressed():
    """GetAsyncKeyState 异常时按未按下处理，且不破坏后续边沿状态。"""
    _reset()

    def boom():
        raise OSError('mock failure')

    hotkey._read_key_state = boom
    assert hotkey.poll_stop() is False
    assert hotkey.poll_stop() is False
