#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""走法应用与 PV 推演（apply_move / uci_cn / pv_to_cn）单元测试。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fen_utils import fen_to_board                       # noqa: E402
from move_notation import apply_move, uci_cn, pv_to_cn   # noqa: E402

# 用户实际反馈的中局局面（红方走）
FEN = 'r2akab2/3r5/2n1bcn2/p1N1p2Rp/6p2/2P6/Pc2P1P1P/1C2C1N2/9/R1BAKAB2 w - - 0 1'


def test_apply_move_basic():
    board, _ = fen_to_board(FEN)
    assert board[7][4] == 'C'          # e2 的炮
    new = apply_move(board, 'e2e6')
    # 原棋盘不被修改
    assert board[7][4] == 'C'
    assert board[3][4] == 'p'          # e6 原为黑卒
    # 新棋盘：炮移到 e6（吃掉黑卒）
    assert new[7][4] is None
    assert new[3][4] == 'C'


def test_apply_move_capture():
    board, _ = fen_to_board(FEN)
    # e2e6 即炮吃黑卒：目标格 (4,3) 原为黑卒 p
    assert board[3][4] == 'p'
    new = apply_move(board, 'e2e6')
    assert new[3][4] == 'C'    # 炮吃掉卒
    assert new[7][4] is None   # 原炮位置清空


def test_uci_cn():
    board, _ = fen_to_board(FEN)
    assert uci_cn('e2e6', board) == '炮五进四 (e2e6)'


def test_pv_to_cn():
    board, _ = fen_to_board(FEN)
    pv = ['e2e6', 'd9e8', 'e6e4']
    result = pv_to_cn(pv, board)
    assert result[0] == '炮五进四 (e2e6)'
    assert result[1] == '士4进5 (d9e8)'
    assert result[2] == '炮五退二 (e6e4)'


def test_pv_to_cn_without_uci():
    board, _ = fen_to_board(FEN)
    assert pv_to_cn(['e2e6'], board, with_uci=False) == ['炮五进四']


def test_pv_to_cn_full_line():
    board, _ = fen_to_board(FEN)
    pv = ['e2e6', 'd9e8', 'e6e4', 'a9d9', 'c0e2', 'd8d6', 'h6d6', 'd9d6']
    result = pv_to_cn(pv, board)
    # 长度应等于 PV 长度，且每一步都带 (uci)
    assert len(result) == len(pv)
    for cn_uci in result:
        assert '(' in cn_uci and ')' in cn_uci
    # 第一步和第二步的确定性断言
    assert result[0] == '炮五进四 (e2e6)'
    assert result[1] == '士4进5 (d9e8)'


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
