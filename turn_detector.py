#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
谁走棋（turn）判定模块。

当前采用手动策略：用户按键指定当前轮到谁走（'w'=红方 / 'b'=黑方），
并支持一键翻转（下完一步后自动交换）。

后续可扩展差分策略（DiffTurnDetector）：连续两次识别棋盘，
若棋盘发生变化（对手刚走完）则轮到我方，用于自动判定。
"""


class TurnDetector:
    """手动 turn 管理器。"""

    RED = 'w'    # 红方走
    BLACK = 'b'  # 黑方走

    _LABELS = {RED: '红方', BLACK: '黑方'}

    def __init__(self, turn='w'):
        self.turn = turn

    def set(self, side):
        """设置为指定一方走。side: 'w' 或 'b'。"""
        if side not in (self.RED, self.BLACK):
            raise ValueError(f"非法 turn 值: {side!r}（应为 'w' 或 'b'）")
        self.turn = side

    def flip(self):
        """翻转轮次：w <-> b。"""
        self.turn = self.BLACK if self.turn == self.RED else self.RED

    def current(self):
        return self.turn

    def label(self):
        return self._LABELS[self.turn]


class DiffTurnDetector(TurnDetector):
    """
    差分策略（预留扩展）：通过对比前后两次识别到的棋盘判断轮到谁。

    尚未实现具体逻辑，仅定义接口。基本思路：
    1. 记录上一次棋盘 snapshot。
    2. 每次识别后与上次对比：
       - 若棋盘棋子数量/位置发生变化，说明对方刚走完一步，现在轮到我方。
       - 首次识别仍需用户手动指定一次。
    """

    def __init__(self):
        super().__init__()
        self.last_board = None

    def update_from_board(self, board):
        """对比新旧棋盘并更新 turn（占位实现，后续填充）。"""
        self.last_board = board
        # TODO: 实现差分逻辑
