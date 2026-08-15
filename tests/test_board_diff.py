#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""board_diff 棋盘差分纯函数单测（不依赖窗口/鼠标/引擎）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fen_utils import fen_to_board             # noqa: E402
from move_notation import apply_move           # noqa: E402
from board_diff import (                       # noqa: E402
    diff_boards, single_move_from_diff, is_same_move, contains_move,
    moves_in_diff,
)

FEN1 = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


def _boards():
    b1, _ = fen_to_board(FEN1)
    b2 = apply_move(b1, 'b2e2')   # 红炮平中
    return b1, b2


def test_diff_boards_no_change():
    b1, _ = _boards()
    d = diff_boards(b1, b1)
    assert d.changed == []
    assert d.old_count == d.new_count


def test_diff_boards_single_move():
    b1, b2 = _boards()
    d = diff_boards(b1, b2)
    assert len(d.changed) == 2
    move = single_move_from_diff(d)
    assert move == (7, 1, 7, 4)   # b2e2: 红炮 c2(7,1) 平到 e2(7,4)


def test_single_move_reject_noise():
    """3 格变化（含识别噪声）不构成单步。"""
    b1, _ = _boards()
    noisy = [row[:] for row in b1]
    noisy[3][3] = 'P'   # 额外一格里凭空多一个子
    d = diff_boards(b1, noisy)
    assert single_move_from_diff(d) is None


def test_single_move_capture():
    """吃子：from 有→空，to 被替换为同色子。"""
    board = [[None] * 9 for _ in range(10)]
    board[0][0] = 'r'   # 黑车
    board[9][0] = 'R'   # 红车（同列，中间全空）
    after = apply_move(board, 'a0a9')   # 红车直上吃黑车
    d = diff_boards(board, after)
    assert single_move_from_diff(d) == (9, 0, 0, 0)


def test_moves_in_diff_clean_single_move():
    """干净单步 → 恰好一个候选。"""
    b1, b2 = _boards()
    assert moves_in_diff(diff_boards(b1, b2)) == [(7, 1, 7, 4)]


def test_moves_in_diff_with_noise():
    """对方着法 + 2 个噪声格（4 格变化）→ 仍能枚举出真实着法。

    这是 single_move_from_diff（要求恰好 2 格）无法处理的场景，
    也是"等待对方卡死"的根因。"""
    b1, _ = _boards()
    b_opp = apply_move(b1, 'b7e7')   # 黑炮平中
    noisy = [row[:] for row in b_opp]
    noisy[3][3] = 'P'    # 噪声格
    noisy[4][4] = 'c'    # 噪声格
    d = diff_boards(b1, noisy)
    assert len(d.changed) == 4
    assert single_move_from_diff(d) is None      # 旧逻辑：拒绝
    assert (2, 1, 2, 4) in moves_in_diff(d)      # b7e7 仍被枚举出来


def test_moves_in_diff_capture_plus_noise():
    """吃子 + 噪声格 → 吃子着法仍被枚举出来。"""
    board = [[None] * 9 for _ in range(10)]
    board[0][0] = 'r'   # 黑车
    board[9][0] = 'R'   # 红车
    after = apply_move(board, 'a0a9')   # 红车直上吃黑车
    noisy = [row[:] for row in after]
    noisy[4][4] = 'c'   # 噪声格
    assert (9, 0, 0, 0) in moves_in_diff(diff_boards(board, noisy))


def test_moves_in_diff_no_change():
    """无变化 → 无候选。"""
    b1, _ = _boards()
    assert moves_in_diff(diff_boards(b1, b1)) == []


def test_is_same_move_positive():
    b1, b2 = _boards()
    d_actual = diff_boards(b1, b2)
    d_expect = diff_boards(b1, apply_move(b1, 'b2e2'))
    assert is_same_move(d_actual, d_expect) is True


def test_is_same_move_negative():
    b1, b2 = _boards()
    b3 = apply_move(b2, 'b7e7')   # 对方又走一步 → 相对 b1 是 4 格变化
    d_actual = diff_boards(b1, b3)
    d_expect = diff_boards(b1, b2)
    assert is_same_move(d_actual, d_expect) is False


def test_is_same_move_no_change():
    b1, _ = _boards()
    d_no = diff_boards(b1, b1)
    d_expect = diff_boards(b1, apply_move(b1, 'b2e2'))
    assert is_same_move(d_no, d_expect) is False


def test_contains_move_extra_opponent_move():
    """实际变化 = 预期单步 + 对方又走一步（4 格）→ 仍判定为"包含"。"""
    b1, b2 = _boards()
    b3 = apply_move(b2, 'b7e7')   # 我方红炮 b2e2 + 对方黑炮 b7e7
    d_actual = diff_boards(b1, b3)
    d_expect = diff_boards(b1, b2)
    assert contains_move(d_actual, d_expect) is True


def test_contains_move_noise():
    """实际变化 = 预期单步 + 1 个噪声格（3 格）→ 仍判定为"包含"。"""
    b1, b2 = _boards()
    noisy = [row[:] for row in b2]
    noisy[3][3] = 'P'   # 额外噪声格
    d_actual = diff_boards(b1, noisy)
    d_expect = diff_boards(b1, b2)
    assert contains_move(d_actual, d_expect) is True


def test_contains_move_capture_plus_noise():
    """吃子 + 额外噪声格 → 仍判定为"包含"。"""
    board = [[None] * 9 for _ in range(10)]
    board[0][0] = 'r'   # 黑车
    board[9][0] = 'R'   # 红车
    after = apply_move(board, 'a0a9')   # 红车直上吃黑车
    noisy = [row[:] for row in after]
    noisy[4][4] = 'c'   # 噪声格
    assert contains_move(diff_boards(board, noisy),
                         diff_boards(board, after)) is True


def test_contains_move_wrong_move():
    """实际是另一步棋（不含预期 from/to）→ False。"""
    b1, _ = _boards()
    b_other = apply_move(b1, 'b7e7')   # 黑炮平中，非预期红炮 b2e2
    b2 = apply_move(b1, 'b2e2')
    assert contains_move(diff_boards(b1, b_other),
                         diff_boards(b1, b2)) is False


def test_contains_move_no_change():
    b1, _ = _boards()
    b2 = apply_move(b1, 'b2e2')
    assert contains_move(diff_boards(b1, b1),
                         diff_boards(b1, b2)) is False


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  OK {name}")
        except Exception:
            failed += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
