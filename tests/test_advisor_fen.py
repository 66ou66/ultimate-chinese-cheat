#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""advisor 的 FEN 生成逻辑单元测试（不依赖真实窗口）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'xiangqi-bot'))

from fen_utils import fen_to_board           # noqa: E402
from xiangqi_bot import Bot                  # noqa: E402

FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'
PLACEMENT_EXPECT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


def test_board_to_fen_playing_red():
    bot = Bot()
    bot.playing_red = True
    board, _ = fen_to_board(FEN_INIT)
    assert bot.board_to_fen(board) == PLACEMENT_EXPECT


def test_board_to_fen_playing_black():
    """玩家执黑时，board 是屏幕方向（红在上），需翻转回标准方向。"""
    bot = Bot()
    bot.playing_red = False
    standard, _ = fen_to_board(FEN_INIT)
    # 模拟屏幕方向：标准方向整体旋转 180 度
    screen = [row[::-1] for row in reversed(standard)]
    assert bot.board_to_fen(screen) == PLACEMENT_EXPECT


def test_full_fen_suffix():
    from advisor import Advisor
    a = Advisor.__new__(Advisor)  # 跳过 __init__，避免启动引擎/窗口
    a.bot = Bot()
    board, _ = fen_to_board(FEN_INIT)
    fen = a.board_to_full_fen(board, playing_red=True, turn='w')
    assert fen == f"{PLACEMENT_EXPECT} w - - 0 1"

    fen_b = a.board_to_full_fen(board, playing_red=True, turn='b')
    assert fen_b == f"{PLACEMENT_EXPECT} b - - 0 1"


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
