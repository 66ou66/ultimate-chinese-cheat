#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动落子模块（v3）：把引擎输出的 UCI 走法转换为屏幕坐标，并用鼠标点击落子。

依赖：
- fen_utils.uci_to_coord：UCI → 标准棋盘坐标
- Bot.cols_logical / Bot.rows_logical：棋盘格子的绝对屏幕坐标（截图识别所用）
- Win32 API（ctypes）：鼠标控制；非 Windows 环境回退 pyautogui

坐标体系（与项目其余模块保持一致）：
- 标准棋盘：board[y][x]，y=0 黑方底线（rank 9），y=9 红方底线（rank 0）
- 屏幕棋盘：rows_logical[r]（r=0 屏幕顶部），玩家执红时顶部是黑方
- 玩家执红(playing_red=True)：屏幕方向 == 标准方向，屏幕格子 = (fx, fy)
- 玩家执黑(playing_red=False)：屏幕方向 = 标准方向旋转 180°，屏幕格子 = (8-fx, 9-fy)

v3 重写（修复"点击不生效 / 落子回读确认失败"）：
1. 坐标统一：mss 截图以 (win_x, win_y) 为原点，logical_to_pixel 已验证
   (lx-win_x)*dpi_scale 是截图内的正确像素偏移，因此点击的绝对屏幕坐标
   = win_x + (cols_logical[c]-win_x)*dpi_scale。
   删除 v2 的"识别坐标超过 pyautogui.size() 才换算"启发式。
2. 窗口激活可靠化：ShowWindow(SW_RESTORE) + SetWindowPos(HWND_TOPMOST)
   绕过 SetForegroundWindow 对后台进程的前台锁限制，并用
   GetForegroundWindow 验证激活结果（不再静默吞异常）。
3. 鼠标执行直连 Win32：SetCursorPos + mouse_event(LEFTDOWN/LEFTUP)，
   每次移动后 GetCursorPos 回读实际位置并打印，失败可定位。
4. 全链路诊断：窗口激活结果、目标坐标、鼠标实际位置全部打印。
"""

import os
import sys
import time
import ctypes
import ctypes.wintypes
import traceback

try:
    import pyautogui
except ImportError:  # 非 Windows / 纯坐标转换环境
    pyautogui = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fen_utils import uci_to_coord  # noqa: E402

# 保留 pyautogui 安全机制：鼠标甩到屏幕左上角立即中止
if pyautogui is not None:
    pyautogui.FAILSAFE = True

# 起点 → 终点 点击间隔（秒），防止点击过快被游戏吞掉
CLICK_INTERVAL = 0.6

# --- Win32 常量 ---
SW_RESTORE = 9                 # ShowWindow：还原最小化
HWND_TOPMOST = -1              # SetWindowPos：置顶
HWND_NOTOPMOST = -2            # SetWindowPos：取消置顶
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

_IS_WINDOWS = (os.name == 'nt')


def _activate_window(hwnd):
    """把窗口激活到前台，返回是否确认成功（仅 Windows）。

    SetForegroundWindow 对后台进程有前台锁限制（AllowSetForegroundWindow），
    直接调用经常静默失败——这正是 v2 中"点击落到终端/别处"的根因。
    这里改用组合拳：
      1. ShowWindow(SW_RESTORE)      还原最小化
      2. SetWindowPos(HWND_TOPMOST)  临时置顶，绕过前台锁
      3. SetForegroundWindow + BringWindowToTop 激活
    最后用 GetForegroundWindow 回读验证，不再静默吞异常。
    """
    if not hwnd:
        return False
    if not _IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.10)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        time.sleep(0.05)
        r = user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        time.sleep(0.15)
        fg = user32.GetForegroundWindow()
        ok = bool(fg) and (fg == hwnd)
        if not ok:
            print(f"    [窗口] 激活未确认：SetForegroundWindow={r} "
                  f"GetForegroundWindow=0x{fg:x} 目标=0x{hwnd:x}")
        return ok
    except Exception:
        print(f"    [窗口] 激活异常:\n{traceback.format_exc()}")
        return False


def _mouse_move_to(x, y):
    """移动鼠标到屏幕坐标 (x, y)，返回移动后的实际位置 (px, py)。"""
    if _IS_WINDOWS:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(round(x)), int(round(y)))
        time.sleep(0.05)
        pt = ctypes.wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return (pt.x, pt.y)
    if pyautogui is not None:
        pyautogui.moveTo(x, y, duration=0.05)
        return pyautogui.position()
    raise RuntimeError("无可用鼠标控制后端（Windows 需要 ctypes，非 Windows 需要 pyautogui）")


def _mouse_click():
    """在当前位置发送左键按下 + 抬起。"""
    if _IS_WINDOWS:
        user32 = ctypes.windll.user32
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.05)
        return
    if pyautogui is not None:
        pyautogui.click()
        return
    raise RuntimeError("无可用鼠标控制后端（Windows 需要 ctypes，非 Windows 需要 pyautogui）")


def _click_at(x, y, dwell=0.05):
    """移动到屏幕坐标 (x, y) 并左键点击，返回移动后的实际鼠标位置。"""
    pos = _mouse_move_to(x, y)
    time.sleep(dwell)
    _mouse_click()
    return pos


def _unset_topmost(hwnd):
    """取消置顶，恢复窗口正常 Z 序（整步棋点击完成后调用）。"""
    if not hwnd or not _IS_WINDOWS:
        return
    try:
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                          SWP_NOMOVE | SWP_NOSIZE)
    except Exception:
        print(f"    [窗口] 取消置顶异常:\n{traceback.format_exc()}")


def uci_to_screen_cell(uci, playing_red):
    """UCI 走法 → 屏幕方向的格子索引。

    Args:
        uci: UCI 走法，如 'b2e2'。
        playing_red: True=我方执红（红在屏幕下方），False=我方执黑（红在屏幕上方）。

    Returns:
        (from_c, from_r, to_c, to_r)：起点/终点的屏幕格子索引。
        c=列 0..8（左→右），r=行 0..9（上→下）。
    """
    (fx, fy), (tx, ty) = uci_to_coord(uci)
    if playing_red:
        return (fx, fy, tx, ty)
    return (8 - fx, 9 - fy, 8 - tx, 9 - ty)


class AutoPlayer:
    """把 UCI 走法映射到屏幕坐标并点击落子（v3：Win32 直控 + 全链路诊断）。"""

    def __init__(self, bot, click_interval=CLICK_INTERVAL, verbose=True):
        self.bot = bot
        self.click_interval = click_interval
        self.verbose = verbose

    # --- 坐标换算 ---

    def cell_to_screen(self, c, r):
        """屏幕格子索引 → 屏幕绝对像素坐标（与 mss 截图同一坐标系）。

        识别侧 logical_to_pixel 已验证：(lx - win_x) * dpi_scale 是格子中心
        在 mss 截图内的像素偏移；而 mss 截图以屏幕 (win_x, win_y) 为原点。
        因此点击的绝对屏幕坐标 = win_x + (lx - win_x) * dpi_scale。
        识别与点击共用同一公式，任何 DPI 下都不会错位。
        """
        bot = self.bot
        px = bot.win_x + (bot.cols_logical[c] - bot.win_x) * bot.dpi_scale
        py = bot.win_y + (bot.rows_logical[r] - bot.win_y) * bot.dpi_scale
        return px, py

    # --- 点击 ---

    def click_cell(self, c, r, settle=0.25):
        """点击指定格子的中心：激活窗口 → 移动 → 点击 → 回读验证。"""
        px, py = self.cell_to_screen(c, r)
        if self.verbose:
            print(f"    [点击] 格(r{r},c{c}) 目标坐标=({px:.1f},{py:.1f})")
        _activate_window(getattr(self.bot, 'win_hwnd', None))
        pos = _click_at(px, py)
        if self.verbose:
            print(f"    [验证] 鼠标实际位置=({pos[0]},{pos[1]})")
        time.sleep(settle)

    def play_uci(self, uci, playing_red):
        """落一步棋：先激活窗口，再点起点棋子，最后点目标格（点击-点击）。

        Args:
            uci: UCI 走法，如 'b2e2'。
            playing_red: True=我方执红，False=我方执黑。

        Returns:
            ((from_c, from_r), (to_c, to_r))：起终点屏幕格子索引。
        """
        _activate_window(getattr(self.bot, 'win_hwnd', None))
        from_c, from_r, to_c, to_r = uci_to_screen_cell(uci, playing_red)
        self.click_cell(from_c, from_r)
        time.sleep(self.click_interval)
        self.click_cell(to_c, to_r)
        _unset_topmost(getattr(self.bot, 'win_hwnd', None))
        return (from_c, from_r), (to_c, to_r)
