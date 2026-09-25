#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""advisor 的 FEN 生成逻辑单元测试（不依赖真实窗口）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'xiangqi-bot'))

# 兼容中文/emoji 输出（直接跑测试时 stdout 可能是 GBK，避免 ✓/✗ 打印崩溃）
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

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
    # 中盘：顶行缺子 → 用将/帅九宫位置兜底，红帅在下方 → 执红
    mid = [row[:] for row in standard]
    mid[0][0] = None
    assert infer_orientation(mid) is True
    # 底行混入黑子（识别噪声）→ 将/帅仍可判向 → 执红
    messy = [row[:] for row in standard]
    messy[9][0] = 'r'
    assert infer_orientation(messy) is True


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


def test_midgame_orientation_regression():
    """回归：中盘方向误判导致整盘 180° 镜像（用户实测局面）。

    现象：引擎给出 i4i0，记谱误显示为"车一退四"（正确应为"车九进四"）；
          对方 f0f1 误显示为"将6退1"（正确应为"将4进1"）。
    根因：detect_orientation()（红像素比例）在中盘把玩家执红判成执黑，
          而 infer_orientation() 旧版只认开局满子行、中盘返回 None，
          无法纠正 → 整盘 180° 翻转（列号与进退方向全部镜像）。
    修复：infer_orientation() 用将/帅九宫位置（帅恒在 y=7..9、将恒在 y=0..2）
          兜底，中盘亦可稳定判向并纠正。
    """
    from fen_utils import infer_orientation, board_to_fen as to_fen, to_engine_board
    from move_notation import uci_to_cn

    # CNN 输出的"屏幕棋盘"（已被 180° 镜像，红在屏幕上方 = 玩家执黑视角）
    screen_fen = '3AK1B2/4A4/4B4/P3P1r2/9/8R/p8/7c1/4a4/2Ra1k3'
    # 还原后的标准方向局面（红在下方）
    real_fen = '3k1aR2/4a4/1c7/8p/R8/9/2r1P3P/4B4/4A4/2B1KA3'

    screen, _ = fen_to_board(screen_fen)
    # 1) 中盘亦能判向：屏幕棋盘红帅在上方(y=0)、黑将在下方(y=9) → 玩家执黑(False)
    assert infer_orientation(screen) is False

    # 2) 模拟 capture_board 修正：detect_orientation 误判 True → 纠正为 False 后，
    #    屏幕棋盘经 to_engine_board(screen, False) 还原为标准方向 FEN
    playing_red = True
    inferred = infer_orientation(screen)
    if inferred is not None and inferred != playing_red:
        playing_red = inferred
    assert playing_red is False
    assert to_fen(to_engine_board(screen, False)).split(' ')[0] == real_fen

    # 3) 标准方向棋盘 → 玩家执红(True)；与镜像棋盘判向相反（互为 180° 旋转）
    standard, _ = fen_to_board(real_fen)
    assert infer_orientation(standard) is True
    assert to_fen(to_engine_board(standard, True)).split(' ')[0] == real_fen
    assert to_fen(to_engine_board(screen, False)).split(' ')[0] == \
        to_fen(to_engine_board(standard, True)).split(' ')[0]

    # 4) 记谱校验：镜像棋盘上引擎着法 → 误记谱；标准方向 → 正确记谱
    #    引擎最佳 i4i0（红车 9 路 → 底线）等价于标准方向 a5a9
    assert uci_to_cn('i4i0', screen) == '车一退四'      # 镜像 → 误（列号+方向均错）
    assert uci_to_cn('a5a9', standard) == '车九进四'    # 正确
    #    对方应着 f0f1（黑将）等价于标准方向 d9d8
    assert uci_to_cn('f0f1', screen) == '将6退1'        # 镜像 → 误
    assert uci_to_cn('d9d8', standard) == '将4进1'      # 正确（与用户手算一致）


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
