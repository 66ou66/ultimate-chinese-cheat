#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""advisor 层 moves/fen 透传单元测试（不启动真实引擎/窗口，mock 底层）。"""

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 兼容中文/emoji 输出
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

from advisor import Advisor  # noqa: E402


def _advisor():
    """绕过 __init__（不创建 Bot/引擎进程）构造可测 Advisor 实例。"""
    advisor = Advisor.__new__(Advisor)
    advisor.depth = 10
    advisor.movetime = 100
    advisor.multi_pv = 2
    advisor.last = None
    return advisor


def test_analyze_passes_moves_to_engine():
    """advisor.analyze 把 moves 原样透传给 engine.analyze。"""
    advisor = _advisor()
    advisor.engine = mock.Mock()
    advisor.engine._initialized = True
    advisor.analyze('FEN', moves=['b2e2', 'b7e7'])
    advisor.engine.analyze.assert_called_once_with(
        'FEN', depth=10, movetime=100, multi_pv=2, moves=['b2e2', 'b7e7'])


def test_analyze_without_moves():
    """moves 缺省为 None → 传给引擎的 moves 为 None（旧行为）。"""
    advisor = _advisor()
    advisor.engine = mock.Mock()
    advisor.engine._initialized = True
    advisor.analyze('FEN')
    advisor.engine.analyze.assert_called_once_with(
        'FEN', depth=10, movetime=100, multi_pv=2, moves=None)


def test_run_auto_passes_moves_and_fen_override():
    """run_auto(moves, fen)：fen 覆盖识别结果，moves 透传给 analyze。"""
    advisor = _advisor()
    advisor.capture_board = mock.Mock(return_value=('BOARD', True))
    advisor.board_to_full_fen = mock.Mock(return_value='GEN_FEN')
    advisor.analyze = mock.Mock(return_value=mock.Mock())
    advisor.run_auto(moves=['b2e2', 'b7e7'], fen='GIVEN_FEN')
    advisor.analyze.assert_called_once_with('GIVEN_FEN', moves=['b2e2', 'b7e7'])
    # fen 已覆盖 → 不再调用识别生成的 FEN
    advisor.board_to_full_fen.assert_not_called()
    assert advisor.last['turn'] == 'w'


def test_run_auto_uses_recognized_fen_when_no_override():
    """run_auto 不带 fen → 用识别结果生成 FEN，moves 仍透传。"""
    advisor = _advisor()
    advisor.capture_board = mock.Mock(return_value=('BOARD', False))
    advisor.board_to_full_fen = mock.Mock(return_value='GEN_FEN')
    advisor.analyze = mock.Mock(return_value=mock.Mock())
    advisor.run_auto(moves=['b7e7'])
    advisor.board_to_full_fen.assert_called_once_with('BOARD', False, 'b')
    advisor.analyze.assert_called_once_with('GEN_FEN', moves=['b7e7'])


if __name__ == '__main__':
    import traceback
    tests = [(k, v) for k, v in list(globals().items()) if k.startswith('test_')]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  \u2713 {name}")
        except Exception:
            failed += 1
            print(f"  \u2717 {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
