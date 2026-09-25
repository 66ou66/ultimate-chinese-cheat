#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""截图前把游戏窗口提到前台（Bot.bring_window_to_front）单元测试。

背景：[1] 键手动识别是在控制台里按的，此刻控制台正是前台窗口；若它叠在游戏
窗口的截图区域上，mss.grab() 截到的就是控制台内容 → CNN 把每格都读出一个
棋子，整盘识别错乱。[6] 键自动落子每次点击前都先激活窗口，所以同一套校准下
识别正常。修复方式：截图前先把游戏窗口提到 Z 序最前，且不抢键盘焦点。

不启动真实窗口 / 鼠标 / 引擎：Bot 用真实类但不找窗口，Win32 用假 user32。
"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

import ctypes  # noqa: E402

import numpy as np  # noqa: E402

from advisor import Advisor                                          # noqa: E402
from xiangqi_bot import Bot, SW_RESTORE, HWND_TOPMOST, HWND_NOTOPMOST  # noqa: E402


# --- 假组件 ---

class _FakeUser32:
    """只实现 bring_window_to_front 用到的 3 个 API，并记录调用顺序。"""

    def __init__(self, foreground=0):
        self.foreground = foreground
        self.calls = []

    def GetForegroundWindow(self):
        return self.foreground

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(('ShowWindow', hwnd, cmd))

    def SetWindowPos(self, hwnd, insert_after, x, y, cx, cy, flags):
        self.calls.append(('SetWindowPos', hwnd, insert_after, flags))


class _FakeCnn:
    """最小假 CNN：让 capture_board / capture_board_voted 能跑通。"""

    def __init__(self):
        self.parse_calls = 0
        self.validate_calls = 0
        self.last_corrections = 0
        self._cell_probs = [[None] * 9 for _ in range(10)]

    def parse_board(self, img, cols_logical, rows_logical, retina_scale,
                    win_x, win_y, cell_w, cell_h, **kwargs):
        self.parse_calls += 1
        return [[None] * 9 for _ in range(10)]

    def _validate_board(self, board):
        self.validate_calls += 1
        return board


class _FakeBot:
    """只提供识别链路用到的属性/方法，并记录窗口前置调用。"""

    def __init__(self):
        self.playing_red = True
        self.cols_logical = list(range(9))
        self.rows_logical = list(range(10))
        self.dpi_scale = 1.0
        self.win_x = 0
        self.win_y = 0
        self.cell_w = 36
        self.cell_h = 36
        self.win_hwnd = 0x1234
        self.find_window_calls = 0
        self.bring_to_front_calls = 0
        self.load_calibration_calls = 0
        self.detect_orientation_calls = 0

    def find_window(self):
        self.find_window_calls += 1
        return True

    def bring_window_to_front(self):
        self.bring_to_front_calls += 1
        return True

    def load_calibration(self):
        self.load_calibration_calls += 1
        return True

    def screenshot_for_processing(self):
        return np.zeros((32, 32, 3), dtype=np.uint8)

    def detect_orientation(self, img):
        self.detect_orientation_calls += 1
        return True


# --- 工具 ---

def _bot_with_hwnd(hwnd=0x1234):
    """真实 Bot（只做 DPI 检测，不找窗口）。"""
    bot = Bot()
    bot.win_hwnd = hwnd
    return bot


def _patch_user32(fake):
    """把 ctypes.windll.user32 换成假实现（退出 with 时自动还原）。"""
    return mock.patch.object(ctypes, 'windll', mock.Mock(user32=fake))


def _advisor():
    advisor = Advisor.__new__(Advisor)   # 跳过 __init__，避免启动引擎进程
    advisor.bot = _FakeBot()
    advisor.cnn = _FakeCnn()
    advisor.last = None
    advisor._orientation_detected = True
    return advisor


# --- Bot.bring_window_to_front ---

def test_bring_to_front_skips_when_game_already_foreground():
    """游戏已是前台窗口（自动落子路径）→ 不改 Z 序，零副作用。"""
    bot = _bot_with_hwnd()
    fake = _FakeUser32(foreground=bot.win_hwnd)
    with _patch_user32(fake):
        assert bot.bring_window_to_front() is True
    assert fake.calls == []


def test_bring_to_front_lifts_window_when_covered():
    """游戏不是前台窗口（[1] 键在控制台按）→ 置顶再取消置顶，抬到最前。"""
    bot = _bot_with_hwnd()
    fake = _FakeUser32(foreground=0x999)   # 前台是控制台
    with _patch_user32(fake):
        assert bot.bring_window_to_front() is True
    assert [c[0] for c in fake.calls] == ['ShowWindow', 'SetWindowPos', 'SetWindowPos']
    assert fake.calls[0][1:] == (bot.win_hwnd, SW_RESTORE)
    assert fake.calls[1][2] == HWND_TOPMOST     # 先置顶：绕过前台锁
    assert fake.calls[2][2] == HWND_NOTOPMOST   # 立刻还原：不永久霸屏
    # 不抢键盘焦点：假 user32 没有 SetForegroundWindow，一旦被调用就抛异常 →
    # 方法返回 False，上面的 `is True` 已兜住；这里再显式断言调用集合。
    assert all(c[0] in ('ShowWindow', 'SetWindowPos') for c in fake.calls)


def test_bring_to_front_without_hwnd_is_noop():
    """没有窗口句柄（未找到窗口 / 非 Windows）→ 返回 False，不抛异常。"""
    bot = _bot_with_hwnd(hwnd=None)
    fake = _FakeUser32()
    with _patch_user32(fake):
        assert bot.bring_window_to_front() is False
    assert fake.calls == []


# --- Advisor 识别链路：截图前先前置窗口 ---

def test_capture_board_brings_window_to_front_first():
    """单帧识别（6/A 等路径）截图前同样先前置窗口。"""
    advisor = _advisor()
    advisor.capture_board()
    assert advisor.bot.find_window_calls == 1
    assert advisor.bot.bring_to_front_calls == 1


def test_capture_board_voted_brings_window_to_front_once():
    """[1] 键多帧识别只在循环外前置一次窗口（不每帧重复激活）。"""
    advisor = _advisor()
    advisor.capture_board_voted(frames=3, interval=0)
    assert advisor.bot.find_window_calls == 1
    assert advisor.bot.bring_to_front_calls == 1
    assert advisor.cnn.parse_calls == 3


def test_prepare_capture_fronts_window_before_calibration():
    """顺序必须是：找窗口 → 前置窗口 → 加载校准（校准依赖同一窗口）。"""
    advisor = _advisor()
    order = []
    advisor.bot.find_window = lambda: order.append('find_window')
    advisor.bot.bring_window_to_front = lambda: order.append('front')
    advisor.bot.load_calibration = lambda: order.append('calib') or True
    advisor._prepare_capture()
    assert order == ['find_window', 'front', 'calib']


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  \u2713 {name}")
        except Exception:
            failed += 1
            print(f"  \u2717 {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
