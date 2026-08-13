#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""move_notation 单元测试。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fen_utils import fen_to_board                      # noqa: E402
from move_notation import uci_to_cn, format_score       # noqa: E402

FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1'


def _board(fen=FEN_INIT):
    b, _ = fen_to_board(fen)
    return b


def test_red_cannon():
    b = _board()
    assert uci_to_cn('b2e2', b) == '炮八平五'
    assert uci_to_cn('h2e2', b) == '炮二平五'


def test_red_horse():
    b = _board()
    assert uci_to_cn('b0c2', b) == '马八进七'
    assert uci_to_cn('h0g2', b) == '马二进三'


def test_red_elephant():
    b = _board()
    assert uci_to_cn('c0e2', b) == '相七进五'
    assert uci_to_cn('g0e2', b) == '相三进五'


def test_red_advisor():
    b = _board()
    assert uci_to_cn('d0e1', b) == '仕六进五'
    assert uci_to_cn('f0e1', b) == '仕四进五'


def test_red_pawn():
    b = _board()
    assert uci_to_cn('a3a4', b) == '兵九进一'
    assert uci_to_cn('g3g4', b) == '兵三进一'


def test_red_rook():
    b = _board()
    assert uci_to_cn('a0a1', b) == '车九进一'
    assert uci_to_cn('i0i1', b) == '车一进一'


def test_red_king():
    b = _board()
    # 帅在 (4,9)，"帅五进一" -> y8
    assert uci_to_cn('e0e1', b) == '帅五进一'


def test_black_cannon():
    b = _board()
    # 黑炮 (7,2) 平 (4,2)，黑方列号 8 -> 5
    assert uci_to_cn('h7e7', b) == '炮8平5'


def test_black_horse():
    b = _board()
    # 黑马 (7,0) -> (6,2)，黑方进=y增大，马用目标列号 8 -> 7
    assert uci_to_cn('h9g7', b) == '马8进7'


def test_black_pawn():
    b = _board()
    # 黑卒 (6,3) 进一 -> y4，黑方数字步数
    assert uci_to_cn('g6g5', b) == '卒7进1'


def test_double_rook_front_back():
    # 红方两车同列 x=0（九路）：前车 y=5、后车 y=9（红方 y 小在前）
    b = [[None] * 9 for _ in range(10)]
    b[5][0] = 'R'
    b[9][0] = 'R'
    assert uci_to_cn('a4a5', b) == '前车进一'   # (0,5)->(0,4)
    assert uci_to_cn('a0a1', b) == '后车进一'   # (0,9)->(0,8)


def test_black_double_pawn_front_back():
    # 黑方两卒同列 x=4（5路）：黑方 y 大在前
    b = [[None] * 9 for _ in range(10)]
    b[0][4] = 'p'   # 后卒（y 小）
    b[2][4] = 'p'   # 前卒（y 大）
    assert uci_to_cn('e7e6', b) == '前卒进1'   # (4,2)->(4,3)


def test_format_score():
    assert format_score(25, None) == '+25 分'
    assert format_score(-300, None) == '-300 分'
    assert format_score(None, 3) == '杀 3 步'
    assert format_score(None, -2) == '被将 2 步'
    assert format_score(None, None) == '?'


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
