#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto_player 坐标转换单元测试（不依赖窗口/鼠标/引擎）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_player import uci_to_screen_cell  # noqa: E402


def test_uci_to_screen_red():
    """我执红：屏幕方向与标准方向一致（红炮 b2e2 平中）。"""
    assert uci_to_screen_cell('b2e2', True) == (1, 7, 4, 7)


def test_uci_to_screen_black():
    """我执黑：屏幕方向 = 标准方向旋转 180°（黑炮 b7e7 平中）。"""
    assert uci_to_screen_cell('b7e7', False) == (7, 7, 4, 7)


def test_red_black_inverse():
    """同一 UCI：执黑结果 = 执红结果再旋转 180°，两者自洽。"""
    red = uci_to_screen_cell('b2e2', True)     # (1,7,4,7)
    black = uci_to_screen_cell('b2e2', False)  # (7,2,4,2)
    assert black == (8 - red[0], 9 - red[1], 8 - red[2], 9 - red[3])


def test_screen_cell_matches_board_flip():
    """执黑时转换出的屏幕格子，与 board_to_fen 的 180° 翻转逻辑一致。"""
    from fen_utils import fen_to_board, uci_to_coord

    FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'
    standard, _ = fen_to_board(FEN_INIT)

    (fx, fy), (tx, ty) = uci_to_coord('b7e7')  # 标准方向黑炮平中
    assert standard[fy][fx] == 'c'             # b7 确实是黑炮
    assert standard[ty][tx] is None            # e7 初始为空

    screen = [row[::-1] for row in reversed(standard)]  # 与 Bot.board_to_fen 翻转一致

    from_c, from_r, to_c, to_r = uci_to_screen_cell('b7e7', playing_red=False)
    assert (from_c, from_r) == (8 - fx, 9 - fy)
    assert (to_c, to_r) == (8 - tx, 9 - ty)
    assert screen[from_r][from_c] == 'c'       # 起点在屏幕棋盘上仍是黑炮
    assert screen[to_r][to_c] is None          # 终点在屏幕棋盘上为空


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
