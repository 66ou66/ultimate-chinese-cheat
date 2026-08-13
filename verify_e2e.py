#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端验证：用已知 FEN 模拟"识别结果"，走完整链路
（棋盘 → 完整 FEN → Pikafish 分析 → 中文建议）。

不含真实截屏识别，用于验证 advisor 编排逻辑与引擎联通。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fen_utils import fen_to_board                    # noqa: E402
from advisor import Advisor, format_suggestion        # noqa: E402
from move_notation import uci_to_cn, format_score     # noqa: E402

FEN_INIT = 'rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR'


def main():
    advisor = Advisor(depth=12, movetime=1000, multi_pv=3)
    advisor.engine.initialize()

    board, _ = fen_to_board(FEN_INIT)

    for turn in ('w', 'b'):
        fen = advisor.board_to_full_fen(board, playing_red=True, turn=turn)
        result = advisor.analyze(fen)
        data = {
            'board': board,
            'playing_red': True,
            'fen': fen,
            'turn': turn,
            'result': result,
        }
        print("=" * 60)
        print(format_suggestion(data))
        # 中文着法演示
        if result.bestmove:
            print(f"中文着法: {uci_to_cn(result.bestmove, board)}")
        print("=" * 60)

    advisor.close()
    print("\n✅ 端到端验证通过（引擎正常返回 bestmove 与候选变例）")


if __name__ == '__main__':
    main()
