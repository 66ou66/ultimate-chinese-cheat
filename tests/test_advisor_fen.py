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


def test_to_engine_board_black_rotation():
    # black player screen board (180-deg rotated) to standard engine board
    from fen_utils import to_engine_board
    standard, _ = fen_to_board(FEN_INIT)
    screen = [row[::-1] for row in reversed(standard)]
    assert to_engine_board(screen, False) == standard
    assert to_engine_board(standard, True) == standard


def test_uci_to_cn_black_screen_board():
    # raw screen board raises; after to_engine_board it works
    from fen_utils import to_engine_board
    from move_notation import apply_move, uci_to_cn
    standard, _ = fen_to_board(FEN_INIT)
    standard = apply_move(standard, 'b2e2')
    screen = [row[::-1] for row in reversed(standard)]
    try:
        uci_to_cn('e2e6', screen)
    except ValueError:
        pass
    else:
        raise AssertionError('expected ValueError on raw screen board')
    eb = to_engine_board(screen, False)
    assert uci_to_cn('e2e6', eb) == uci_to_cn('e2e6', standard)


def test_infer_orientation():
    from fen_utils import infer_orientation, INITIAL_FEN
    standard, _ = fen_to_board(INITIAL_FEN)
    # 标准方向：顶行全黑、底行全红 → 执红
    assert infer_orientation(standard) is True
    # 屏幕方向（红在上，玩家执黑）→ 执黑
    screen = [row[::-1] for row in reversed(standard)]
    assert infer_orientation(screen) is False
    # 中盘：顶行缺子 → 不判断（沿用已锁定方向）
    mid = [row[:] for row in standard]
    mid[0][0] = None
    assert infer_orientation(mid) is None
    # 非标准开局：底行混入黑子 → 不判断
    messy = [row[:] for row in standard]
    messy[9][0] = 'r'
    assert infer_orientation(messy) is None


def test_black_mirror_fen_regression():
    """回归：执黑时 FEN 不再镜像翻转，引擎不再"乱指导"（马2退1 (b0a2)）。

    真实局面：红方先走炮二平五（右炮 x=7→x=4），轮黑走。
    模拟 advisor.capture_board 新增的方向双重校验：
    detect_orientation() 误判 playing_red=True 时，
    infer_orientation(board) 应根据棋盘内容修正为 False，使 FEN 还原标准方向。
    """
    from fen_utils import infer_orientation, board_to_fen as to_fen, to_engine_board
    from move_notation import uci_to_cn

    real_fen = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C2C4/9/RNBAKABNR b - - 0 1'
    mirrored_fen = 'RNBAKABNR/9/4C2C1/P1P1P1P1P/9/9/p1p1p1p1p/1c5c1/9/rnbakabnr b - - 0 1'

    standard, _ = fen_to_board(real_fen)
    # 玩家执黑：CNN 输出屏幕方向 = 标准方向旋转 180°
    screen = [row[::-1] for row in reversed(standard)]

    # 1) 方向推断：屏幕方向（红在上）→ 执黑(False)
    assert infer_orientation(screen) is False

    # 2) 模拟 capture_board 修正逻辑：detect_orientation 误判为 True → 修正为 False
    playing_red = True
    inferred = infer_orientation(screen)
    if inferred is not None and inferred != playing_red:
        playing_red = inferred
    assert playing_red is False
    # 修正后：屏幕棋盘 + playing_red=False → 还原标准 FEN（不再镜像）
    assert to_fen(to_engine_board(screen, False), turn='b') == real_fen

    # 3) 正确局面下黑方应如"炮2平5"(b7e7)；镜像局面才会出现荒谬的"马2退1"(b0a2)
    assert uci_to_cn('b7e7', standard) == '炮2平5'
    mirrored, _ = fen_to_board(mirrored_fen)
    assert uci_to_cn('b0a2', mirrored) == '马2退1'


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
