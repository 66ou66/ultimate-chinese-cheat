#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_battle 全自动对弈逻辑单元测试（不依赖窗口/鼠标/引擎）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fen_utils import fen_to_board, board_placement, is_game_over  # noqa: E402
from move_notation import apply_move                               # noqa: E402
from auto_battle import AutoBattle                                 # noqa: E402

FEN1 = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


def _boards():
    b1, _ = fen_to_board(FEN1)
    b2 = apply_move(b1, 'b2e2')   # 红炮平中
    b3 = apply_move(b2, 'b7e7')   # 黑炮平中
    return b1, b2, b3


# --- 纯函数：棋盘指纹 / 结束检测 ---

def test_board_placement_uniqueness():
    b1, b2, b3 = _boards()
    p1, p2, p3 = board_placement(b1), board_placement(b2), board_placement(b3)
    assert p1 != p2 != p3
    assert board_placement(b1) == p1  # 幂等


def test_board_placement_length():
    b1, _, _ = _boards()
    p = board_placement(b1)
    assert len(p.split('/')) == 10
    assert all(len(rank) == 9 for rank in p.split('/'))


def test_is_game_over():
    b1, _, _ = _boards()
    assert is_game_over(b1) is False
    b_no_red = [row[:] for row in b1]
    b_no_red[9][4] = None          # 去掉帅 K
    assert is_game_over(b_no_red) is True
    b_no_black = [row[:] for row in b1]
    b_no_black[0][4] = None        # 去掉将 k
    assert is_game_over(b_no_black) is True


# --- 差分状态机 ---

class _FakeAdvisorStart:
    """仅用于 start() 的假 advisor。"""

    def __init__(self, board, playing_red):
        self.board = board
        self.playing_red = playing_red

    def capture_board(self):
        return self.board, self.playing_red


def test_start_red_first():
    b1, _, _ = _boards()
    battle = AutoBattle(_FakeAdvisorStart(b1, True))
    board, pr = battle.start()
    assert pr is True
    assert battle.my_turn is True   # 执红先手，立即走
    assert battle.last_placement == board_placement(b1)


def test_start_black_waits():
    b1, _, _ = _boards()
    battle = AutoBattle(_FakeAdvisorStart(b1, False))
    battle.start()
    assert battle.my_turn is False  # 执黑后手，等待对方先走


def test_update_board_detect_opponent_move():
    b1, b2, _ = _boards()
    battle = AutoBattle(None, confirm_times=2)
    battle.playing_red = True
    battle.last_placement = board_placement(b1)
    battle.my_turn = False
    assert battle.update_board(b2) is False  # 第一次：待确认
    assert battle.update_board(b2) is True   # 连续第二次：确认对方走完
    assert battle.my_turn is True
    assert battle.last_placement == board_placement(b2)


def test_update_board_same_placement_no_change():
    b1, _, _ = _boards()
    battle = AutoBattle(None, confirm_times=2)
    battle.last_placement = board_placement(b1)
    battle.my_turn = False
    assert battle.update_board(b1) is False
    assert battle.update_board(b1) is False
    assert battle.my_turn is False


def test_update_board_revert_resets_count():
    b1, b2, _ = _boards()
    battle = AutoBattle(None, confirm_times=2)
    battle.last_placement = board_placement(b1)
    battle.my_turn = False
    battle.update_board(b2)   # 计数 1
    battle.update_board(b1)   # 变回旧局面 → 重置
    battle.update_board(b2)   # 重新计数 1（不触发）
    assert battle.my_turn is False
    battle.update_board(b2)   # 连续第 2 次 → 触发
    assert battle.my_turn is True


# --- 落子流程（假 advisor + 替换 auto_player.pyautogui）---

class _FakeResult:
    def __init__(self, bestmove):
        self.bestmove = bestmove


class _FakeAdvisor:
    """假的 Advisor：run_auto 返回固定着法；capture_board 返回指定局面。"""

    def __init__(self, board, playing_red, bestmove='b2e2', after_board=None):
        self.board = board
        self.playing_red = playing_red
        self.bestmove = bestmove
        self.after_board = after_board
        self.bot = _FakeBot()

    def run_auto(self):
        return {
            'board': self.board,
            'playing_red': self.playing_red,
            'turn': 'w' if self.playing_red else 'b',
            'result': _FakeResult(self.bestmove),
        }

    def capture_board(self):
        if self.after_board is not None:
            return self.after_board, self.playing_red
        return self.board, self.playing_red


class _FakeBot:
    """假的 Bot：提供格子逻辑坐标。"""

    def __init__(self):
        self.cols_logical = [100 + 60 * c for c in range(9)]
        self.rows_logical = [100 + 60 * r for r in range(10)]


def _patch_pyautogui():
    """替换 auto_player 模块里的 pyautogui，防止真实鼠标点击。"""
    import auto_player
    from unittest import mock
    fake = mock.Mock()
    fake.FAILSAFE = True
    fake.click = mock.Mock()
    patcher = mock.patch.object(auto_player, 'pyautogui', fake)
    patcher.start()
    return patcher


def test_play_my_move_ok():
    b1, b2, _ = _boards()
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b2)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(max_retries=1, max_reads=3,
                                   wait_anim=0.01, wait_read=0.01) == 'ok'
    finally:
        patcher.stop()
    assert battle.my_turn is False
    assert battle.last_placement == board_placement(b2)


def test_play_my_move_no_move():
    b1, _, _ = _boards()
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='')
    battle = AutoBattle(fake)
    assert battle.play_my_move(max_retries=1, max_reads=1,
                               wait_anim=0, wait_read=0) == 'no_move'


def test_play_my_move_confirm_failed():
    b1, b2, b3 = _boards()
    # after_board=b3（黑炮平中后），既不是落子前 b1 也不是预期 b2 → 读不到稳定局面
    fake = _FakeAdvisor(b1, playing_red=True, bestmove='b2e2', after_board=b3)
    battle = AutoBattle(fake, click_interval=0.01)
    patcher = _patch_pyautogui()
    try:
        assert battle.play_my_move(max_retries=1, max_reads=2,
                                   wait_anim=0.01, wait_read=0.01) == 'confirm_failed'
    finally:
        patcher.stop()


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✓ {name}")
        except Exception:
            failed += 1
            print(f"  ✗ {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

