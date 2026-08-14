#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动落子模块：把引擎输出的 UCI 走法转换为屏幕坐标，并用鼠标点击-点击落子。

依赖：
- fen_utils.uci_to_coord：UCI → 标准棋盘坐标
- Bot.cols_logical / Bot.rows_logical：棋盘格子的屏幕逻辑坐标（与 pyautogui 同坐标系）
- pyautogui：鼠标控制（缺失时仅纯坐标转换可用）

坐标体系（与项目其余模块保持一致）：
- 标准棋盘：board[y][x]，y=0 黑方底线（rank 9），y=9 红方底线（rank 0）
- 屏幕棋盘：rows_logical[r]（r=0 屏幕顶部），玩家执红时顶部是黑方
- 玩家执红(playing_red=True)：屏幕方向 == 标准方向，屏幕格子 = (fx, fy)
- 玩家执黑(playing_red=False)：屏幕方向 = 标准方向旋转 180°，屏幕格子 = (8-fx, 9-fy)
"""

import os
import sys
import time

try:
    import pyautogui
except ImportError:  # 纯坐标转换/测试环境可用，执行落子需要 pyautogui
    pyautogui = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fen_utils import uci_to_coord  # noqa: E402

# 保留 pyautogui 安全机制：鼠标甩到屏幕左上角立即中止
if pyautogui is not None:
    pyautogui.FAILSAFE = True

# 起点 → 终点 点击间隔（秒），防止点击过快被游戏吞掉
CLICK_INTERVAL = 0.3


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
    """把 UCI 走法映射到屏幕坐标并点击落子（点击-点击方式）。"""

    def __init__(self, bot, click_interval=CLICK_INTERVAL):
        self.bot = bot
        self.click_interval = click_interval

    def cell_to_screen(self, c, r):
        """屏幕格子索引 → 屏幕逻辑坐标 (px, py)。"""
        return (self.bot.cols_logical[c], self.bot.rows_logical[r])

    def click_cell(self, c, r):
        """点击指定格子的中心。"""
        if pyautogui is None:
            raise RuntimeError("pyautogui 未安装，无法执行鼠标落子。")
        px, py = self.cell_to_screen(c, r)
        pyautogui.click(int(round(px)), int(round(py)))

    def play_uci(self, uci, playing_red):
        """落一步棋：先点起点棋子，再点目标格（点击-点击）。

        Args:
            uci: UCI 走法，如 'b2e2'。
            playing_red: True=我方执红，False=我方执黑。

        Returns:
            ((from_c, from_r), (to_c, to_r))：起终点屏幕格子索引。
        """
        from_c, from_r, to_c, to_r = uci_to_screen_cell(uci, playing_red)
        self.click_cell(from_c, from_r)
        time.sleep(self.click_interval)
        self.click_cell(to_c, to_r)
        return (from_c, from_r), (to_c, to_r)
