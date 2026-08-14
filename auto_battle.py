#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全自动对弈模块：差分状态机 + 分析落子。

流程：
    开局识别（自动判断我方执红/执黑）→ 状态机循环：
      等待对方：截屏差分检测棋盘变化 → 确认对方走完 → 轮到我方
      我方回合：引擎分析 → 自动点击落子 → 回读确认 → 回到等待对方

纯逻辑部分（board_placement / is_game_over / AutoBattle.update_board / start）
不依赖 pyautogui / 引擎 / 窗口，可独立单测；落子依赖在方法内 lazy import。
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fen_utils import board_placement, is_game_over  # noqa: E402
from move_notation import apply_move                 # noqa: E402

# 起点→终点点击间隔（秒）
CLICK_INTERVAL = 0.3
# 全自动轮询间隔（秒）
POLL_INTERVAL = 0.8


class AutoBattle:
    """全自动对弈状态机。

    关键状态：
      - playing_red: 我方执红（屏幕下方是红方）？
      - my_turn: 当前是否轮到我方走
      - last_placement: 已确认的棋盘指纹
      - pending_placement / confirm_count: 差分变化的确认计数（防动画/识别抖动）
    """

    def __init__(self, advisor, click_interval=CLICK_INTERVAL,
                 poll_interval=POLL_INTERVAL, confirm_times=2):
        self.advisor = advisor
        self.click_interval = click_interval
        self.poll_interval = poll_interval
        self.confirm_times = confirm_times
        self.playing_red = None
        self.last_placement = None
        self.pending_placement = None
        self.confirm_count = 0
        self.my_turn = False

    # --- 开局 ---

    def start(self):
        """开局：识别我方执哪方，初始化差分状态机。

        执红(playing_red=True) → 我方先行；执黑 → 等待对方先走。

        Returns:
            (board, playing_red)
        """
        board, playing_red = self.advisor.capture_board()
        self.playing_red = playing_red
        self.last_placement = board_placement(board)
        self.my_turn = playing_red
        return board, playing_red

    # --- 差分检测（等待对方时每轮调用）---

    def update_board(self, board):
        """喂入一次识别结果，做差分检测。

        对方走完（新局面连续 confirm_times 次识别一致）→ my_turn=True 并返回 True。
        未检测到稳定变化 / 仍轮不到我方 → 返回 False。

        中途变回旧局面会重置确认计数（防动画/识别抖动误判）。
        """
        cur = board_placement(board)
        if cur != self.last_placement:
            if cur != self.pending_placement:
                self.pending_placement = cur
                self.confirm_count = 1
            else:
                self.confirm_count += 1
        else:
            self.pending_placement = None
            self.confirm_count = 0
        if (self.pending_placement is not None
                and self.confirm_count >= self.confirm_times
                and not self.my_turn):
            self.last_placement = self.pending_placement
            self.pending_placement = None
            self.confirm_count = 0
            self.my_turn = True
            return True
        return False

    # --- 我方回合：分析 + 落子 + 回读确认 ---

    def play_my_move(self, max_retries=3, max_reads=5,
                     wait_anim=0.6, wait_read=0.4):
        """轮到我方：引擎分析 → 自动点击落子 → 回读确认落子生效。

        Args（主要供测试缩短等待）:
            max_retries: 点击未生效时的重试次数
            max_reads:   回读时最多尝试读取次数（防动画中间态）
            wait_anim:   落子后等待动画稳定的秒数
            wait_read:   回读间隔秒数

        Returns:
            'no_move': 引擎无合法着法（对局结束）
            'ok': 落子成功并回读确认
            'confirm_failed': 回读确认失败（需人工介入）
        """
        data = self.advisor.run_auto()
        result = data['result']
        if not result.bestmove:
            return 'no_move'
        uci = result.bestmove
        before = board_placement(data['board'])
        expected = board_placement(apply_move(data['board'], uci))

        from auto_player import AutoPlayer
        player = AutoPlayer(self.advisor.bot, click_interval=self.click_interval)
        for _ in range(max_retries):
            player.play_uci(uci, data['playing_red'])
            time.sleep(wait_anim)
            confirmed = False
            for _ in range(max_reads):
                try:
                    board2, _ = self.advisor.capture_board()
                except Exception:
                    break
                p2 = board_placement(board2)
                if p2 == expected:
                    confirmed = True
                    break
                if p2 == before:
                    break  # 点击未生效，立即重试落子
                time.sleep(wait_read)  # 动画/识别不稳，继续读
            if confirmed:
                self.last_placement = expected
                self.my_turn = False
                return 'ok'
        return 'confirm_failed'
